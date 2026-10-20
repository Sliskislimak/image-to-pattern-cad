"""
Scale Recovery Engine for Stage 2 Pattern Semantic Digitizer.

Implements multi-method physical scale recovery (px -> mm):
- ScaleResult: Authoritative Pydantic model for scale factors.
- Scenario A: Dimension-driven anisotropic calibration (F05) & tailoring text parser (F04).
- Scenario B: Two-point reference calibration (F01).
- Scenario C: 50x50 mm reference square quad detector (F02) & DPI header extractor (F03).
- Fallback 96 DPI & tiered auto-detect cascade.
"""

from __future__ import annotations

import math
import re
import struct
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import cv2
import numpy as np
from pydantic import BaseModel, Field, field_validator

from src.core.contract import Contour, InternalLine, Notch, PathSegment, PatternPiece, Point2D
from src.core.geometry import (
    calibrate_piece_anisotropic,
    get_contour_bounds,
    get_contour_dimensions_mm,
)

# ---------------------------------------------------------------------------
# 1. Common Data Contracts
# ---------------------------------------------------------------------------

CalibrationMethod = Literal[
    "dimension_anisotropic",
    "two_point_reference",
    "calibration_square",
    "dpi_metadata",
    "fallback_default",
]

PointInput = Union[Point2D, Tuple[float, float], List[float], np.ndarray]


class ScaleResult(BaseModel):
    """
    Standardized result model for physical scale estimation (px -> mm).
    Immutable across calibration scenarios (A, B, C, DPI, and fallback).
    """

    mm_per_px_x: float = Field(
        gt=0.0, description="Scale factor along X axis in physical millimeters per pixel."
    )
    mm_per_px_y: float = Field(
        gt=0.0, description="Scale factor along Y axis in physical millimeters per pixel."
    )
    is_isotropic: bool = Field(
        default=True,
        description="True if X and Y scale factors are identical within tolerance.",
    )
    calibration_method: CalibrationMethod = Field(
        description="Method used to establish physical scale."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score of calibration (0.0 = blind guess, 1.0 = exact direct ground truth).",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Diagnostic parameters, detected coordinates, and intermediate metrics.",
    )

    @field_validator("mm_per_px_x", "mm_per_px_y")
    @classmethod
    def check_finite_positive(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0.0:
            raise ValueError(f"Scale factor must be a finite strictly positive number (> 0), got: {v}")
        return v

    @property
    def mm_per_px(self) -> float:
        """Mean scale factor for isotropic operations: 0.5 * (mm_per_px_x + mm_per_px_y)."""
        return 0.5 * (self.mm_per_px_x + self.mm_per_px_y)

    @property
    def px_per_mm(self) -> float:
        """Inverted scale factor (pixels per millimeter)."""
        return 1.0 / self.mm_per_px

    @classmethod
    def create_isotropic(
        cls,
        mm_per_px: float,
        method: CalibrationMethod,
        confidence: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ScaleResult":
        """Convenience factory for isotropic scale results."""
        return cls(
            mm_per_px_x=mm_per_px,
            mm_per_px_y=mm_per_px,
            is_isotropic=True,
            calibration_method=method,
            confidence=confidence,
            metadata=metadata or {},
        )


# ---------------------------------------------------------------------------
# 2. Scenario B: Two-Point Reference Calibration (F01)
# ---------------------------------------------------------------------------


def _normalize_point(pt: PointInput) -> Tuple[float, float]:
    """Extracts (x, y) coordinates from Point2D, tuple, list, or ndarray."""
    if isinstance(pt, Point2D):
        return (float(pt.x), float(pt.y))
    if isinstance(pt, np.ndarray):
        flat = pt.ravel()
        if len(flat) < 2:
            raise ValueError(
                f"Point sequence must have at least 2 elements, got {len(flat)}: {pt!r}"
            )
        return (float(flat[0]), float(flat[1]))
    if isinstance(pt, (tuple, list)):
        if len(pt) < 2:
            raise ValueError(
                f"Point sequence must have at least 2 elements, got {len(pt)}: {pt!r}"
            )
        return (float(pt[0]), float(pt[1]))
    if hasattr(pt, "x") and hasattr(pt, "y"):
        return (float(getattr(pt, "x")), float(getattr(pt, "y")))
    raise TypeError(
        f"Invalid point type '{type(pt).__name__}'. Expected Point2D or (x, y), got: {pt!r}"
    )


def calibrate_from_two_points(
    p1: PointInput,
    p2: PointInput,
    real_distance_cm: float,
) -> ScaleResult:
    """
    Calculates isotropic scale (mm_per_px) from two reference pixel coordinates and real distance in cm.

    Args:
        p1: Coordinates of first reference point (Point2D or (x, y) tuple/list).
        p2: Coordinates of second reference point (Point2D or (x, y) tuple/list).
        real_distance_cm: Known physical distance in centimeters (must be > 0).

    Returns:
        ScaleResult with is_isotropic=True, confidence=1.0, and calibration_method="two_point_reference".

    Raises:
        ValueError: If pixel distance between p1 and p2 <= 1e-6 (coincident points, E01).
        ValueError: If real_distance_cm <= 0 or not finite (E02).
        TypeError: If p1 or p2 are not valid point representations.
    """
    try:
        dist_cm = float(real_distance_cm)
    except (TypeError, ValueError) as err:
        raise TypeError(
            f"Declared real-world distance must be numeric, got: {real_distance_cm!r}"
        ) from err

    if math.isnan(dist_cm) or math.isinf(dist_cm) or dist_cm <= 0.0:
        raise ValueError("Declared real-world distance must be strictly positive.")

    x1, y1 = _normalize_point(p1)
    x2, y2 = _normalize_point(p2)

    for val in (x1, y1, x2, y2):
        if math.isnan(val) or math.isinf(val):
            raise ValueError("Point coordinates must be finite real numbers.")

    dx = x2 - x1
    dy = y2 - y1
    d_px = math.hypot(dx, dy)

    if d_px <= 1e-6:
        raise ValueError("Pixel distance between reference points must be greater than zero.")

    real_distance_mm = dist_cm * 10.0
    if not math.isfinite(real_distance_mm) or not math.isfinite(d_px):
        raise ValueError("Calculated distance exceeds floating-point representation limits.")

    mm_per_px = real_distance_mm / d_px
    if not math.isfinite(mm_per_px) or mm_per_px <= 0.0:
        raise ValueError("Resulting scale factor must be finite and positive.")

    return ScaleResult(
        mm_per_px_x=mm_per_px,
        mm_per_px_y=mm_per_px,
        is_isotropic=True,
        calibration_method="two_point_reference",
        confidence=1.0,
        metadata={
            "p1": [x1, y1],
            "p2": [x2, y2],
            "pixel_distance": d_px,
            "real_distance_cm": dist_cm,
            "real_distance_mm": real_distance_mm,
            "scale_factor_mm_per_px": mm_per_px,
        },
    )


def scale_contour(contour: Contour, scale: ScaleResult) -> Contour:
    """
    Applies ScaleResult to transform a Contour from pixel units to physical millimeters.
    Preserves all tailoring semantics (seam_allowance_mm, edge_role) on each segment.
    """
    sx = scale.mm_per_px_x
    sy = scale.mm_per_px_y
    new_segments: list[PathSegment] = []
    for seg in contour.segments:
        start = seg.start.scaled(sx, sy)
        end = seg.end.scaled(sx, sy)
        cp1 = seg.cp1.scaled(sx, sy) if seg.cp1 else None
        cp2 = seg.cp2.scaled(sx, sy) if seg.cp2 else None
        new_segments.append(
            PathSegment(
                type=seg.type,
                start=start,
                end=end,
                cp1=cp1,
                cp2=cp2,
                seam_allowance_mm=seg.seam_allowance_mm,
                edge_role=seg.edge_role,
            )
        )
    return Contour(segments=new_segments, is_closed=contour.is_closed)


# ---------------------------------------------------------------------------
# 3. Scenario A: Anisotropic Calibration & Tailoring Text Parser (F04, F05)
# ---------------------------------------------------------------------------

_UNIT_TO_CM: Dict[str, float] = {
    "cm": 1.0,
    "mm": 0.1,
    "in": 2.54,
    "inch": 2.54,
    "inches": 2.54,
    '"': 2.54,
    "”": 2.54,
}

_MULTIPLIER_PATTERNS = [
    # (x2), (x 2), (2x), (2), (x1), (2 szt), (2 pcs)
    re.compile(r"\(\s*[xX]?\s*(\d{1,2})\s*(?:[xX]|szt\.?|pcs\.?|pieces|items)?\s*\)", re.IGNORECASE),
    # Tailoring quantity keywords: cut 2, cortar 2, kroić 2, 2 szt, 2 pcs
    re.compile(
        r"\b(?:cut|cortar|kroi[cć]|skroi[cć]|ilo[sś][cć]|cantidad)\s*[:=]?\s*(\d{1,2})\b|\b(\d{1,2})\s*(?:szt\.?|pcs\.?)(?!\w)",
        re.IGNORECASE,
    ),
    # Attached 'x': x1, x2, X1, X2, 2x, 1x (no spaces between x and number)
    re.compile(r"\b(?:[xX](\d{1,2})|(\d{1,2})[xX])\b"),
]

# Spaced standalone 'x 2' or 'x 1' when NOT preceded by a number or dimension unit
_SPACED_X_REGEX = re.compile(
    r"(?:^|\s)[xX]\s*(\d{1,2})\b(?!\s*(?:cm|mm|in|inch|inches|\"|”|[.,]\s*\d|\d))",
    re.IGNORECASE,
)
_PRECEDING_NUM_OR_UNIT = re.compile(
    r"(?:\d|\b(?:cm|mm|in|inch|inches|\"|”))\s*$",
    re.IGNORECASE,
)

_FOLD_REGEX = re.compile(
    r"\b(doblez|doble|al\s+lomo|lomo|pliegue|zgi[eę]cie|z[lł]o[zż]enie|na\s+zgi[eę]ciu|fold|cut\s+on\s+fold|place\s+on\s+fold|center\s+fold|lbf)\b",
    re.IGNORECASE,
)

_ELASTIC_REGEX = re.compile(
    r"\b(elastizar|el[aá]stico|goma|[sś]ci[aą]gacz|gumka|elastyczn\w*|elastic\w*|shirring|gather\w*)\b",
    re.IGNORECASE,
)

_FABRIC_REGEX = re.compile(
    r"(?:en\s+)?(tela\s+exterior(?:\s+y\s+forro)?|tela\s+principal|forro|entretela|tkanina\s+zasadnicza|podszewka|wierzch|ocieplina|shell(?:\s+fabric)?|lining|interfacing|main\s+fabric)",
    re.IGNORECASE,
)

_DIM_PAIR_REGEX = re.compile(
    r"(?P<w>\d+(?:\s*[.,]\s*\d+)?)\s*(?P<u1>cm|mm|in|inch|inches|\"|”)?\s*"
    r"(?:x|X|\*|×|\s+by\s+|\s+na\s+|\s+por\s+)\s*"
    r"(?P<h>\d+(?:\s*[.,]\s*\d+)?)\s*(?P<u2>cm|mm|in|inch|inches|\"|”)?",
    re.IGNORECASE,
)

_SINGLE_DIM_REGEX = re.compile(
    r"\b(?P<val>\d+(?:\s*[.,]\s*\d+)?)\s*(?P<unit>cm|mm|in|inch|inches|\"|”)?\b",
    re.IGNORECASE,
)


def parse_tailoring_text(text: str) -> Dict[str, Any]:
    """
    Parses Spanish, Polish, and English pattern annotation strings.

    Extracts:
    - piece_name: Optional[str]
    - width_cm: Optional[float]
    - height_cm: Optional[float]
    - quantity: int (default: 1)
    - has_fold: bool (default: False)
    - has_elastic: bool (default: False)
    - fabric_note: str (default: "")
    - unit: str (normalized unit, default: "cm")
    - raw_text: str
    """
    raw_text = text
    cleaned = text.strip()
    # Normalize multiplication signs and quotes
    cleaned = cleaned.replace("×", "x").replace("✕", "x").replace("✖", "x").replace("*", "x")
    cleaned = cleaned.replace("”", '"').replace("“", '"').replace("″", '"')

    # Phase 1: Extract multiplier & mask to avoid confusion with dimension 'x' (E06)
    quantity = 1
    masked_text = cleaned
    matched_multiplier = False
    for pat in _MULTIPLIER_PATTERNS:
        match = pat.search(masked_text)
        if match:
            # Extract number from matching groups
            groups = [g for g in match.groups() if g is not None]
            if groups:
                try:
                    quantity = int(groups[0])
                except ValueError:
                    quantity = 1
            start, end = match.span()
            # Replace match span with spaces to preserve string indexing
            masked_text = masked_text[:start] + (" " * (end - start)) + masked_text[end:]
            matched_multiplier = True
            break

    # If no multiplier found yet, check for spaced standalone 'x 2' not preceded by dimension
    if not matched_multiplier:
        match = _SPACED_X_REGEX.search(masked_text)
        if match:
            prefix = masked_text[:match.start()].rstrip()
            if not _PRECEDING_NUM_OR_UNIT.search(prefix):
                try:
                    quantity = int(match.group(1))
                except ValueError:
                    quantity = 1
                start, end = match.span()
                masked_text = masked_text[:start] + (" " * (end - start)) + masked_text[end:]

    # Phase 2: Detect semantic keywords
    has_fold = bool(_FOLD_REGEX.search(masked_text))
    has_elastic = bool(_ELASTIC_REGEX.search(masked_text))

    fabric_match = _FABRIC_REGEX.search(masked_text)
    fabric_note = fabric_match.group(0).strip() if fabric_match else ""

    # Phase 3: Extract dimensions (Resolves E06 and E07)
    width_cm: Optional[float] = None
    height_cm: Optional[float] = None
    unit_str = "cm"
    dim_match_start: Optional[int] = None

    dim_pair = _DIM_PAIR_REGEX.search(masked_text)
    if dim_pair:
        dim_match_start = dim_pair.start()
        w_val = float(dim_pair.group("w").replace(",", ".").replace(" ", ""))
        h_val = float(dim_pair.group("h").replace(",", ".").replace(" ", ""))
        u1 = (dim_pair.group("u1") or "").lower()
        u2 = (dim_pair.group("u2") or "").lower()

        # Unit inheritance
        if u1 and not u2:
            u2 = u1
        elif u2 and not u1:
            u1 = u2
        elif not u1 and not u2:
            u1 = u2 = "cm"

        factor_1 = _UNIT_TO_CM.get(u1, 1.0)
        factor_2 = _UNIT_TO_CM.get(u2, 1.0)
        width_cm = w_val * factor_1
        height_cm = h_val * factor_2
        unit_str = u1 if u1 in ("cm", "mm", "in") else "cm"

        # Check for trailing multiplier after dimensions (e.g., "Visera 20 cm x 11 cm x 2")
        if quantity == 1:
            after_dim = masked_text[dim_pair.end():]
            trailing_multiplier = re.search(r"(?:^|\s)[xX]\s*(\d{1,2})\b(?!\s*(?:cm|mm|in|\"|”|\d))", after_dim)
            if trailing_multiplier:
                quantity = int(trailing_multiplier.group(1))
                # Mask out trailing multiplier
                t_start = dim_pair.end() + trailing_multiplier.start()
                t_end = dim_pair.end() + trailing_multiplier.end()
                masked_text = masked_text[:t_start] + (" " * (t_end - t_start)) + masked_text[t_end:]
    else:
        # Fallback to single dimension (E07)
        single_dim = _SINGLE_DIM_REGEX.search(masked_text)
        if single_dim:
            dim_match_start = single_dim.start()
            val = float(single_dim.group("val").replace(",", ".").replace(" ", ""))
            u = (single_dim.group("unit") or "cm").lower()
            factor = _UNIT_TO_CM.get(u, 1.0)
            width_cm = val * factor
            height_cm = None
            unit_str = u if u in ("cm", "mm", "in") else "cm"

    # Phase 4: Extract piece name from prefix before dimensions/keywords
    piece_name: Optional[str] = None
    if dim_match_start is not None and dim_match_start > 0:
        prefix = masked_text[:dim_match_start].strip()
        # Clean prefix: remove numbers/bullet points, trailing punctuation
        prefix = re.sub(r"^[\d\.\-\)\s]+", "", prefix)
        prefix = re.sub(r"[\-\:\(\)]+$", "", prefix).strip()
        if prefix:
            piece_name = prefix
    elif masked_text and dim_match_start is None:
        # If no dimension found, entire masked string before keywords might be name
        prefix = re.split(r"[\-\:\(]", masked_text)[0].strip()
        prefix = re.sub(r"^[\d\.\-\)\s]+", "", prefix)
        prefix = re.sub(r"[\-\:\(\)]+$", "", prefix).strip()
        if prefix:
            piece_name = prefix

    return {
        "piece_name": piece_name,
        "width_cm": width_cm,
        "height_cm": height_cm,
        "quantity": quantity,
        "has_fold": has_fold,
        "has_elastic": has_elastic,
        "fabric_note": fabric_note,
        "unit": unit_str,
        "raw_text": raw_text,
    }


def compute_anisotropic_scale(
    pixel_width: float,
    pixel_height: float,
    target_width_cm: float,
    target_height_cm: float,
    tol_isotropic: float = 0.01,
) -> ScaleResult:
    """
    Computes anisotropic mm_per_px factors from pixel bounding box and declared target dimensions.
    """
    try:
        pw = float(pixel_width)
        ph = float(pixel_height)
        tw = float(target_width_cm)
        th = float(target_height_cm)
    except (TypeError, ValueError) as err:
        raise TypeError(f"Dimensions must be numeric, got: {err}") from err

    if not math.isfinite(pw) or not math.isfinite(ph) or pw <= 0.0 or ph <= 0.0:
        raise ValueError(f"Pixel dimensions must be strictly positive, got ({pixel_width} x {pixel_height}).")
    if not math.isfinite(tw) or not math.isfinite(th) or tw <= 0.0 or th <= 0.0:
        raise ValueError(
            f"Target dimensions must be strictly positive, got ({target_width_cm} x {target_height_cm})."
        )

    target_w_mm = tw * 10.0
    target_h_mm = th * 10.0
    if not math.isfinite(target_w_mm) or not math.isfinite(target_h_mm):
        raise ValueError("Target dimensions exceed floating-point representation limits.")

    sx = target_w_mm / pw
    sy = target_h_mm / ph
    if not math.isfinite(sx) or not math.isfinite(sy) or sx <= 0.0 or sy <= 0.0:
        raise ValueError("Resulting scale factors must be finite and positive.")

    max_s = max(sx, sy)
    is_iso = (abs(sx - sy) / max_s) < tol_isotropic if max_s > 0 else True

    return ScaleResult(
        mm_per_px_x=sx,
        mm_per_px_y=sy,
        is_isotropic=is_iso,
        calibration_method="dimension_anisotropic",
        confidence=1.0,
        metadata={
            "pixel_width": pw,
            "pixel_height": ph,
            "target_width_cm": tw,
            "target_height_cm": th,
            "target_width_mm": target_w_mm,
            "target_height_mm": target_h_mm,
        },
    )


def calibrate_piece_by_dimensions(
    piece: PatternPiece,
    target_width_cm: float,
    target_height_cm: float,
) -> PatternPiece:
    """
    Scales a PatternPiece independently along X and Y axes to match exact target dimensions.
    Resets cut_contour to None, preserves segment edge roles and seam allowances, and scales notches.

    Raises:
        ValueError: If target dimensions <= 0 (E08).
        ValueError: If piece contour is degenerate or has 0 dimensions (E08).
    """
    try:
        tw = float(target_width_cm)
        th = float(target_height_cm)
    except (TypeError, ValueError) as err:
        raise TypeError(f"Target dimensions must be numeric, got: {err}") from err

    if not math.isfinite(tw) or not math.isfinite(th) or tw <= 0.0 or th <= 0.0:
        raise ValueError(
            f"Target dimensions must be strictly positive (> 0), got ({target_width_cm} x {target_height_cm})."
        )

    if not piece.outer_contour.segments:
        raise ValueError(f"Element '{piece.name}' has no contour segments.")

    min_x, min_y, max_x, max_y = get_contour_bounds(piece.outer_contour, steps_per_curve=64)
    curr_w = max_x - min_x
    curr_h = max_y - min_y
    if curr_w <= 1e-6 or curr_h <= 1e-6:
        raise ValueError(f"Element '{piece.name}' ma zerowe wymiary konturu ({curr_w} x {curr_h}).")

    target_w_mm = tw * 10.0
    target_h_mm = th * 10.0
    if not math.isfinite(target_w_mm) or not math.isfinite(target_h_mm):
        raise ValueError("Target dimensions exceed floating-point representation limits.")

    sx = target_w_mm / curr_w
    sy = target_h_mm / curr_h
    if not math.isfinite(sx) or not math.isfinite(sy) or sx <= 0.0 or sy <= 0.0:
        raise ValueError("Resulting scale factors must be finite and strictly positive.")

    # Update target dimensions on piece model
    updated_piece = piece.model_copy(
        update={
            "target_width_cm": tw,
            "target_height_cm": th,
        }
    )

    # Perform anisotropic calibration
    calibrated = calibrate_piece_anisotropic(updated_piece)

    # Restore segment edge_role and seam_allowance_mm since transform_contour resets them
    orig_segs = piece.outer_contour.segments
    calib_segs = calibrated.outer_contour.segments
    restored_outer = calibrated.outer_contour
    if len(orig_segs) == len(calib_segs):
        restored_segs = [
            calib_segs[i].model_copy(
                update={
                    "edge_role": orig_segs[i].edge_role,
                    "seam_allowance_mm": orig_segs[i].seam_allowance_mm,
                }
            )
            for i in range(len(calib_segs))
        ]
        restored_outer = Contour(
            segments=restored_segs,
            is_closed=calibrated.outer_contour.is_closed,
        )

    # Restore internal lines segment attributes
    restored_internals: List[InternalLine] = []
    for orig_line, calib_line in zip(piece.internal_lines, calibrated.internal_lines):
        orig_isegs = orig_line.contour.segments
        calib_isegs = calib_line.contour.segments
        if len(orig_isegs) == len(calib_isegs):
            restored_isegs = [
                calib_isegs[j].model_copy(
                    update={
                        "edge_role": orig_isegs[j].edge_role,
                        "seam_allowance_mm": orig_isegs[j].seam_allowance_mm,
                    }
                )
                for j in range(len(calib_isegs))
            ]
            restored_internals.append(
                calib_line.model_copy(
                    update={
                        "contour": Contour(
                            segments=restored_isegs,
                            is_closed=calib_line.contour.is_closed,
                        )
                    }
                )
            )
        else:
            restored_internals.append(calib_line)

    # Transform notches to calibrated coordinate space
    scaled_notches: List[Notch] = []
    for n in piece.notches:
        scaled_pos = Point2D(
            x=(n.position.x - min_x) * sx,
            y=(n.position.y - min_y) * sy,
        )
        scaled_dir: Optional[Point2D] = None
        if n.direction is not None:
            ndx = n.direction.x * sx
            ndy = n.direction.y * sy
            norm = math.hypot(ndx, ndy)
            scaled_dir = Point2D(x=ndx / norm, y=ndy / norm) if norm > 1e-9 else Point2D(x=ndx, y=ndy)
        scaled_notches.append(
            n.model_copy(
                update={
                    "position": scaled_pos,
                    "direction": scaled_dir,
                }
            )
        )

    return calibrated.model_copy(
        update={
            "outer_contour": restored_outer,
            "cut_contour": None,
            "internal_lines": restored_internals,
            "notches": scaled_notches,
        }
    )


# ---------------------------------------------------------------------------
# 4. Scenario C: Calibration Square Detection (F02)
# ---------------------------------------------------------------------------


def detect_calibration_square(
    image: Any,
    target_square_mm: float = 50.0,
    min_area_px: float = 400.0,
    aspect_ratio_tol: float = 0.05,
    max_cos_angle: float = 0.15,
) -> Optional[ScaleResult]:
    """
    Automatically detects a standard calibration square (default 5.0 x 5.0 cm = 50 mm) in a raster image.
    Uses contour quad approximation, side ratio check (E04), and orthogonality check (E04, E05).

    Returns:
        ScaleResult with method='calibration_square' and confidence=0.95, or None if no square found.
    """
    try:
        ts_mm = float(target_square_mm)
    except (TypeError, ValueError) as err:
        raise TypeError(f"target_square_mm must be numeric, got: {target_square_mm!r}") from err

    if not math.isfinite(ts_mm) or ts_mm <= 0.0:
        raise ValueError(f"target_square_mm must be strictly positive, got {target_square_mm}")

    # Normalize image input
    if isinstance(image, (str, Path)):
        img_mat = cv2.imread(str(image), cv2.IMREAD_UNCHANGED)
    elif isinstance(image, np.ndarray):
        img_mat = image
    else:
        return None

    if img_mat is None or img_mat.size == 0:
        return None

    # Convert to grayscale
    if len(img_mat.shape) == 3:
        if img_mat.shape[2] == 4:
            gray = cv2.cvtColor(img_mat, cv2.COLOR_BGRA2GRAY)
        else:
            gray = cv2.cvtColor(img_mat, cv2.COLOR_BGR2GRAY)
    elif len(img_mat.shape) == 2:
        gray = img_mat
    else:
        return None

    # Ensure gray is uint8
    if gray.dtype != np.uint8:
        gray = np.clip(gray, 0, 255).astype(np.uint8)

    # Binarization
    if float(np.mean(gray)) > 127.0:
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    else:
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Find contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    total_area = float(gray.shape[0] * gray.shape[1])

    candidates: List[Tuple[float, float, float, float, np.ndarray, float]] = []

    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area_px or area > 0.80 * total_area:
            continue

        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)

        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue

        pts = approx.reshape(4, 2).astype(float)
        sides = [float(np.linalg.norm(pts[(i + 1) % 4] - pts[i])) for i in range(4)]
        min_s = min(sides)
        max_s = max(sides)
        if min_s <= 1e-3:
            continue

        aspect = max_s / min_s
        if aspect > (1.0 + aspect_ratio_tol):
            continue  # Rejects non-square rectangles (E04)

        # Check corner angles (orthogonality)
        cosines = []
        for i in range(4):
            v1 = pts[(i - 1) % 4] - pts[i]
            v2 = pts[(i + 1) % 4] - pts[i]
            n1 = np.linalg.norm(v1)
            n2 = np.linalg.norm(v2)
            if n1 <= 1e-3 or n2 <= 1e-3:
                break
            cos_th = abs(float(np.dot(v1, v2) / (n1 * n2)))
            cosines.append(cos_th)

        if len(cosines) != 4 or max(cosines) > max_cos_angle:
            continue  # Rejects trapezoids / non-orthogonal polygons (E04)

        avg_side = float(np.mean(sides))
        # Check area consistency with side length squared
        if not (0.80 <= (area / (avg_side * avg_side)) <= 1.20):
            continue

        distortion_score = (aspect - 1.0) + sum(cosines)
        candidates.append((distortion_score, avg_side, aspect, max(cosines), pts, area))

    if not candidates:
        return None

    # Choose candidate with minimum distortion score
    candidates.sort(key=lambda x: x[0])
    best_score, best_avg_side, best_aspect, best_cos, best_pts, best_area = candidates[0]

    scale = ts_mm / best_avg_side

    center_x = float(np.mean(best_pts[:, 0]))
    center_y = float(np.mean(best_pts[:, 1]))

    return ScaleResult(
        mm_per_px_x=scale,
        mm_per_px_y=scale,
        is_isotropic=True,
        calibration_method="calibration_square",
        confidence=0.95,
        metadata={
            "target_square_mm": ts_mm,
            "avg_side_px": round(best_avg_side, 2),
            "aspect_ratio": round(best_aspect, 4),
            "max_cos_angle": round(best_cos, 4),
            "center_px": (round(center_x, 1), round(center_y, 1)),
            "contour_area_px": round(best_area, 1),
            "quad_vertices": best_pts.tolist(),
        },
    )


# ---------------------------------------------------------------------------
# 5. DPI Metadata Scale Extraction (F03)
# ---------------------------------------------------------------------------


def _parse_png_phys(data: bytes) -> Optional[Tuple[float, float, Dict[str, Any]]]:
    """Parses PNG pHYs chunk if present and specified in physical meters."""
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    offset = 8
    try:
        while offset + 12 <= len(data):
            length = struct.unpack(">I", data[offset : offset + 4])[0]
            ctype = data[offset + 4 : offset + 8]
            if ctype == b"pHYs" and length >= 9:
                cdata = data[offset + 8 : offset + 8 + length]
                if len(cdata) < 9:
                    return None
                ppux, ppuy, unit = struct.unpack(">IIB", cdata[:9])
                if unit == 1 and ppux > 0 and ppuy > 0:
                    mm_px_x = 1000.0 / ppux
                    mm_px_y = 1000.0 / ppuy
                    meta = {
                        "source": "png_phys",
                        "pixels_per_meter_x": ppux,
                        "pixels_per_meter_y": ppuy,
                        "dpi_x": ppux * 0.0254,
                        "dpi_y": ppuy * 0.0254,
                        "unit": "meter",
                    }
                    return (mm_px_x, mm_px_y, meta)
                return None  # unit=0 indicates aspect ratio only
            if ctype in (b"IEND", b"IDAT"):
                break
            offset += 12 + length
    except (struct.error, ValueError, IndexError, OverflowError):
        return None
    return None


def _parse_tiff_resolution(tiff: bytes) -> Optional[Tuple[float, float, Dict[str, Any]]]:
    """Parses TIFF header IFD0 for XResolution (0x011A), YResolution (0x011B), and Unit (0x0128)."""
    if len(tiff) < 8:
        return None
    try:
        endian = tiff[:2]
        bo = "<" if endian == b"II" else ">" if endian == b"MM" else None
        if not bo:
            return None
        magic = struct.unpack(bo + "H", tiff[2:4])[0]
        if magic != 42:
            return None
        ifd0_offset = struct.unpack(bo + "I", tiff[4:8])[0]
        if ifd0_offset + 2 > len(tiff):
            return None
        num_entries = struct.unpack(bo + "H", tiff[ifd0_offset : ifd0_offset + 2])[0]

        tags: Dict[int, Any] = {}
        entry_offset = ifd0_offset + 2
        for _ in range(num_entries):
            if entry_offset + 12 > len(tiff):
                break
            tag, ftype, count, val_or_offset = struct.unpack(
                bo + "HHII", tiff[entry_offset : entry_offset + 12]
            )
            if tag in (0x011A, 0x011B):
                if val_or_offset + 8 <= len(tiff):
                    num, den = struct.unpack(bo + "II", tiff[val_or_offset : val_or_offset + 8])
                    if den > 0:
                        tags[tag] = num / den
            elif tag == 0x0128:
                if bo == ">" and val_or_offset > 0xFFFF:
                    tags[tag] = val_or_offset >> 16
                else:
                    tags[tag] = val_or_offset & 0xFFFF
            entry_offset += 12

        xres = tags.get(0x011A)
        yres = tags.get(0x011B)
        unit = tags.get(0x0128, 2)  # default 2 = inch
        if xres and yres and xres > 0 and yres > 0:
            if unit == 2:  # Inch
                return (
                    25.4 / xres,
                    25.4 / yres,
                    {
                        "source": "jpeg_exif",
                        "dpi_x": float(xres),
                        "dpi_y": float(yres),
                        "unit": "inch",
                    },
                )
            elif unit == 3:  # Centimeter
                return (
                    10.0 / xres,
                    10.0 / yres,
                    {
                        "source": "jpeg_exif",
                        "dpi_x": float(xres) * 2.54,
                        "dpi_y": float(yres) * 2.54,
                        "unit": "cm",
                    },
                )
    except (struct.error, ValueError, IndexError, ZeroDivisionError, OverflowError):
        return None
    return None


def _parse_jpeg_dpi(data: bytes) -> Optional[Tuple[float, float, Dict[str, Any]]]:
    """Parses JPEG JFIF (APP0) or EXIF (APP1) density markers."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    offset = 2
    try:
        while offset < len(data) - 4:
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            offset += 2
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(data):
                break
            seg_len = struct.unpack(">H", data[offset : offset + 2])[0]
            if seg_len < 2:
                break
            seg_data = data[offset + 2 : offset + seg_len]

            # JFIF APP0
            if marker == 0xE0 and seg_data.startswith(b"JFIF\x00") and len(seg_data) >= 12:
                units = seg_data[7]
                x_dens = struct.unpack(">H", seg_data[8:10])[0]
                y_dens = struct.unpack(">H", seg_data[10:12])[0]
                if units == 1 and x_dens > 0 and y_dens > 0:  # DPI
                    return (
                        25.4 / x_dens,
                        25.4 / y_dens,
                        {
                            "source": "jpeg_jfif",
                            "dpi_x": float(x_dens),
                            "dpi_y": float(y_dens),
                            "unit": "inch",
                        },
                    )
                elif units == 2 and x_dens > 0 and y_dens > 0:  # DPCM
                    return (
                        10.0 / x_dens,
                        10.0 / y_dens,
                        {
                            "source": "jpeg_jfif",
                            "dpi_x": x_dens * 2.54,
                            "dpi_y": y_dens * 2.54,
                            "unit": "cm",
                        },
                    )

            # EXIF APP1
            elif marker == 0xE1 and seg_data.startswith(b"Exif\x00\x00"):
                tiff = seg_data[6:]
                exif_res = _parse_tiff_resolution(tiff)
                if exif_res:
                    return exif_res

            offset += seg_len
    except (struct.error, ValueError, IndexError, OverflowError):
        return None
    return None


def extract_dpi_scale(image_path_or_bytes: Any) -> Optional[ScaleResult]:
    """
    Extracts physical resolution scale from image headers (PNG pHYs, JPEG JFIF/EXIF).
    Returns None if no physical DPI metadata exists or metadata is invalid.
    """
    raw_data: bytes
    if isinstance(image_path_or_bytes, (bytes, bytearray)):
        raw_data = bytes(image_path_or_bytes[:65536])
    elif isinstance(image_path_or_bytes, (str, Path)):
        p = Path(image_path_or_bytes)
        try:
            if not p.is_file():
                return None
            with open(p, "rb") as f:
                raw_data = f.read(65536)
        except OSError:
            return None
    else:
        return None

    if len(raw_data) < 8:
        return None

    try:
        # Try PNG pHYs
        png_res = _parse_png_phys(raw_data)
        if png_res is not None:
            sx, sy, meta = png_res
            is_iso = abs(sx - sy) / max(sx, sy) < 1e-3
            return ScaleResult(
                mm_per_px_x=sx,
                mm_per_px_y=sy,
                is_isotropic=is_iso,
                calibration_method="dpi_metadata",
                confidence=0.85,
                metadata=meta,
            )

        # Try JPEG JFIF / EXIF
        jpg_res = _parse_jpeg_dpi(raw_data)
        if jpg_res is not None:
            sx, sy, meta = jpg_res
            is_iso = abs(sx - sy) / max(sx, sy) < 1e-3
            return ScaleResult(
                mm_per_px_x=sx,
                mm_per_px_y=sy,
                is_isotropic=is_iso,
                calibration_method="dpi_metadata",
                confidence=0.85,
                metadata=meta,
            )
    except (struct.error, ValueError, IndexError, OverflowError):
        return None

    return None


# ---------------------------------------------------------------------------
# 6. Fallback Default & Auto-Detect Cascade
# ---------------------------------------------------------------------------


def get_fallback_scale(default_dpi: float = 96.0) -> ScaleResult:
    """
    Returns standard display scale fallback (default 96 DPI = 0.264583 mm/px).
    Confidence is 0.10 to indicate uncalibrated default estimation.
    """
    try:
        dpi = float(default_dpi)
    except (TypeError, ValueError) as err:
        raise TypeError(f"default_dpi must be numeric, got: {default_dpi!r}") from err

    if not math.isfinite(dpi) or dpi <= 0.0:
        raise ValueError(f"default_dpi must be strictly positive, got {default_dpi}")

    scale = 25.4 / dpi
    return ScaleResult(
        mm_per_px_x=scale,
        mm_per_px_y=scale,
        is_isotropic=True,
        calibration_method="fallback_default",
        confidence=0.10,
        metadata={
            "default_dpi": dpi,
            "warning": "No explicit physical calibration found; using default 96 DPI fallback.",
        },
    )


def auto_detect_scale(
    image: Any,
    image_path: Optional[Union[str, Path, bytes]] = None,
    target_square_mm: float = 50.0,
    fallback_dpi: float = 96.0,
) -> ScaleResult:
    """
    Automatically detects physical scale via the tiered cascade:
    1. detect_calibration_square (confidence 0.95)
    2. extract_dpi_scale (confidence 0.85)
    3. get_fallback_scale (confidence 0.10)
    """
    # Tier 1: Calibration square
    square_res = detect_calibration_square(image, target_square_mm=target_square_mm)
    if square_res is not None:
        return square_res

    # Tier 2: DPI metadata from path or bytes
    source = image_path if image_path is not None else image
    try:
        dpi_res = extract_dpi_scale(source)
    except Exception:
        dpi_res = None
    if dpi_res is not None:
        return dpi_res

    # Tier 3: Standard fallback
    return get_fallback_scale(default_dpi=fallback_dpi)
