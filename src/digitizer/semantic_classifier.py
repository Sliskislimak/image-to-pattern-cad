"""
Tailoring Line & Semantic Feature Classifier for Stage 2 Pattern Semantic Digitizer (Milestone 3).

Implements tailoring semantic classification on digitized CAD assets:
- F10: Seam Allowance Mode 'net_only' (outward offset via Seam Allowance Engine).
- F11: Seam Allowance Mode 'included' (digitized boundary is cut line, inward offset for net sewing line).
- F12: Seam Allowance Mode 'dual_detected' (pairs concentric outer cut line and inner sewing line).
- F13: Fold Edge Detection & Annotation (detects fold lines from text hints 'doblez'/'fold' or geometry).
- F14: Configurable Auto-Unfold (CAD-native fold edge on Layer 5 vs. full 1:1 unfolded piece).
- F15: Internal Cuts, Darts & Slits Classification (extracts internal markings into InternalLine on Layer 4).
- F16: Tailoring Notch Detection (detects alignment ticks, calculates outward normals, maps to Notch on Layer 2).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
import pyclipper
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.validation import make_valid

from src.core.contract import (
    LINE_TYPE_CUT,
    LINE_TYPE_DART,
    LINE_TYPE_FOLD,
    LINE_TYPE_INTERNAL_CUT,
    LINE_TYPE_SEAM,
    LINE_TYPE_SLIT,
    Contour,
    EdgeRole,
    InternalLine,
    InternalLineType,
    Notch,
    PathSegment,
    PatternPiece,
    Point2D,
)
from src.core.geometry import (
    contour_to_points,
    contour_to_shapely_polygon,
    get_contour_bounds,
    get_contour_dimensions_mm,
    unfold_piece_on_fold,
)
from src.core.seam_allowance import (
    CLIPPER_SCALE,
    apply_seam_allowance_to_piece,
    apply_uniform_seam_allowance,
    apply_variable_seam_allowance,
)
from src.digitizer.scale_recovery import ScaleResult
from src.digitizer.vectorizer import PixelToMMTransform

SeamMode = Literal["net_only", "included", "dual_detected"]
FoldEdgeHint = Literal["auto", "bottom", "left", "right", "top", "longest_straight"]

__all__ = [
    "classify_seam_mode",
    "detect_fold_edge",
    "apply_auto_unfold",
    "extract_internal_features",
    "detect_notches",
    "add_notch",
    "add_notch_at_ratio",
    "add_internal_line",
    "compute_normal_at_point",
]


# ===========================================================================
# 1. Seam Allowance Modes (F10, F11, F12)
# ===========================================================================


def _inward_offset_contour(
    contour: Contour,
    offset_mm: float,
    miter_limit: float = 2.0,
) -> Contour:
    """
    Computes an inward offset of a closed contour using PyclipperOffset.
    Shrinks the polygon by offset_mm.
    """
    if offset_mm <= 0.0:
        return contour

    pts = contour_to_points(contour, steps_per_curve=48)
    if len(pts) < 3:
        raise ValueError(f"Contour has too few points ({len(pts)}) for inward offset.")

    scaled_pts = [
        (int(round(x * CLIPPER_SCALE)), int(round(y * CLIPPER_SCALE)))
        for x, y in pts
    ]
    scaled_pts = pyclipper.CleanPolygon(scaled_pts)

    pco = pyclipper.PyclipperOffset(miter_limit=miter_limit)
    pco.AddPath(scaled_pts, pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
    # Negative delta produces inward offset (shrinking)
    delta = int(round(-offset_mm * CLIPPER_SCALE))
    solution = pco.Execute(delta)

    if not solution:
        raise ValueError(
            f"Inward offset of {offset_mm} mm collapsed the polygon (contour is too narrow)."
        )

    inner_path = max(solution, key=lambda p: abs(pyclipper.Area(p)))
    inner_pts = [(x / CLIPPER_SCALE, y / CLIPPER_SCALE) for x, y in inner_path]

    poly = Polygon(inner_pts)
    if not poly.is_valid:
        poly = make_valid(poly)
        if isinstance(poly, MultiPolygon):
            poly = max(poly.geoms, key=lambda g: g.area)
        inner_pts = list(poly.exterior.coords)[:-1]

    return Contour.from_polyline(inner_pts, is_closed=True)


def classify_seam_mode(
    piece: PatternPiece,
    mode: SeamMode = "net_only",
    seam_allowance_mm: float = 10.0,
    inner_contour: Optional[Contour] = None,
    miter_limit: float = 2.0,
) -> PatternPiece:
    """
    Applies seam allowance semantics based on the input pattern graphics:

    1. 'net_only' (F10):
       The digitized boundary represents the net finished sewing line (outer_contour).
       If seam_allowance_mm > 0, an outer cutting contour (cut_contour) is generated
       via outward offsetting (preserving edge roles, e.g. fold = 0 mm).

    2. 'included' (F11):
       The digitized boundary represents the outer cut line (cut_contour).
       If seam_allowance_mm > 0, the inner sewing line (outer_contour) is derived
       via inward offsetting. If seam_allowance_mm == 0, outer_contour is preserved as-is.

    3. 'dual_detected' (F12):
       Both concentric contours (cut line and sewing line) are provided.
       The contour with larger enclosed area is assigned to cut_contour,
       and the smaller one to outer_contour.
    """
    if seam_allowance_mm < 0.0:
        raise ValueError(f"Seam allowance must be >= 0.0 mm, got {seam_allowance_mm}")

    if mode == "net_only":
        if seam_allowance_mm == 0.0:
            return piece.model_copy(
                update={
                    "cut_contour": None,
                    "seam_allowance_mm": 0.0,
                }
            )
        # Apply outward offset to produce cut_contour
        return apply_seam_allowance_to_piece(
            piece,
            global_seam_allowance_mm=seam_allowance_mm,
            miter_limit=miter_limit,
        )

    elif mode == "included":
        # The digitized boundary IS the cutting line
        cut_line = piece.outer_contour
        if seam_allowance_mm == 0.0:
            return piece.model_copy(
                update={
                    "cut_contour": cut_line,
                    "seam_allowance_mm": 0.0,
                }
            )
        # Derive net sewing line by inward offset
        net_line = _inward_offset_contour(
            cut_line,
            offset_mm=seam_allowance_mm,
            miter_limit=miter_limit,
        )
        return piece.model_copy(
            update={
                "outer_contour": net_line,
                "cut_contour": cut_line,
                "seam_allowance_mm": seam_allowance_mm,
            }
        )

    elif mode == "dual_detected":
        if inner_contour is None:
            if piece.has_cut_contour:
                return piece.model_copy(update={"seam_allowance_mm": seam_allowance_mm})
            raise ValueError(
                "Mode 'dual_detected' requires an inner_contour parameter or existing cut_contour."
            )

        poly_a = contour_to_shapely_polygon(piece.outer_contour)
        poly_b = contour_to_shapely_polygon(inner_contour)

        # Larger polygon is the cut line, smaller is the sewing line
        if poly_a.area >= poly_b.area:
            cut_c = piece.outer_contour
            net_c = inner_contour
        else:
            cut_c = inner_contour
            net_c = piece.outer_contour

        effective_sa = seam_allowance_mm
        if effective_sa <= 0.0:
            # Estimate average distance between bounding boxes
            min_xa, min_ya, max_xa, max_ya = get_contour_bounds(cut_c)
            min_xb, min_yb, max_xb, max_yb = get_contour_bounds(net_c)
            dx = 0.5 * ((max_xa - min_xa) - (max_xb - min_xb))
            dy = 0.5 * ((max_ya - min_ya) - (max_yb - min_yb))
            effective_sa = max(0.0, float(0.5 * (dx + dy)))

        return piece.model_copy(
            update={
                "outer_contour": net_c,
                "cut_contour": cut_c,
                "seam_allowance_mm": effective_sa,
            }
        )

    else:
        raise ValueError(
            f"Unsupported seam mode '{mode}'. Choose from: 'net_only', 'included', 'dual_detected'."
        )


# ===========================================================================
# 2. Fold Edge Detection & Auto-Unfold (F13, F14)
# ===========================================================================

_FOLD_KEYWORDS = [
    "doblez", "doble", "al lomo", "lomo", "pliegue",
    "zgiecie", "zgięcie", "zlozenie", "złożenie", "na zgieciu", "na zgięciu",
    "fold", "cut on fold", "place on fold", "center fold", "lbf"
]


def _has_fold_text(text: Optional[str]) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in _FOLD_KEYWORDS)


def detect_fold_edge(
    piece: PatternPiece,
    text_hint: Optional[str] = None,
    edge_hint: FoldEdgeHint = "auto",
) -> PatternPiece:
    """
    Identifies a fold edge on a pattern piece's outer contour (F13).

    Checks text cues (e.g. 'doblez', 'fold', 'al lomo') and geometric criteria
    (straight edge aligned with a bounding box boundary or longest straight edge).
    Sets edge_role='fold' and seam_allowance_mm=0.0 on the matched segment.

    Args:
        piece: PatternPiece to analyze.
        text_hint: Optional text annotation string (e.g. from OCR or preset notes).
        edge_hint: Geometric alignment hint:
            - 'auto': detects longest straight edge on a bounding box border if fold text is present.
            - 'bottom': straight edge closest to min_y.
            - 'left': straight edge closest to min_x.
            - 'right': straight edge closest to max_x.
            - 'top': straight edge closest to max_y.
            - 'longest_straight': longest straight line segment regardless of side.

    Returns:
        PatternPiece with the identified fold edge marked.
    """
    segs = piece.outer_contour.segments
    if not segs:
        return piece

    # If edge_hint is 'auto', only proceed if text_hint indicates a fold
    if edge_hint == "auto" and not _has_fold_text(text_hint) and not _has_fold_text(piece.fabric_note) and not _has_fold_text(piece.name):
        return piece

    min_x, min_y, max_x, max_y = get_contour_bounds(piece.outer_contour)

    # Collect straight line segment candidates
    line_candidates: List[Tuple[int, float, float, float]] = []  # (index, length, min_dist_to_bbox, alignment_score)
    for idx, seg in enumerate(segs):
        if seg.type != "line":
            continue
        dx = seg.end.x - seg.start.x
        dy = seg.end.y - seg.start.y
        length = math.hypot(dx, dy)
        if length < 5.0:  # ignore tiny corner chamfers
            continue

        mid_x = 0.5 * (seg.start.x + seg.end.x)
        mid_y = 0.5 * (seg.start.y + seg.end.y)

        dist_left = abs(mid_x - min_x)
        dist_right = abs(mid_x - max_x)
        dist_bottom = abs(mid_y - min_y)
        dist_top = abs(mid_y - max_y)

        if edge_hint == "bottom":
            score = -dist_bottom
        elif edge_hint == "top":
            score = -dist_top
        elif edge_hint == "left":
            score = -dist_left
        elif edge_hint == "right":
            score = -dist_right
        elif edge_hint == "longest_straight":
            score = length
        else:  # 'auto'
            # Look for segments hugging one of the bounding box borders
            min_border_dist = min(dist_left, dist_right, dist_bottom, dist_top)
            # Prioritize bottom (common in fronts/visors) and left (center back/front)
            is_horizontal = abs(dy) < 1e-2 * length
            is_vertical = abs(dx) < 1e-2 * length
            bonus = 100.0 if (is_horizontal or is_vertical) else 0.0
            score = bonus + length - 2.0 * min_border_dist

        line_candidates.append((idx, length, min(dist_left, dist_right, dist_bottom, dist_top), score))

    if not line_candidates:
        return piece

    # Pick candidate with highest score
    line_candidates.sort(key=lambda c: c[3], reverse=True)
    best_idx = line_candidates[0][0]

    # Create updated segments with fold role on best_idx
    new_segments: List[PathSegment] = []
    for idx, seg in enumerate(segs):
        if idx == best_idx:
            new_segments.append(
                PathSegment(
                    type=seg.type,
                    start=seg.start,
                    end=seg.end,
                    cp1=seg.cp1,
                    cp2=seg.cp2,
                    seam_allowance_mm=0.0,
                    edge_role="fold",
                )
            )
        else:
            new_segments.append(seg)

    return piece.model_copy(
        update={
            "outer_contour": Contour(segments=new_segments, is_closed=piece.outer_contour.is_closed)
        }
    )


def apply_auto_unfold(
    piece: PatternPiece,
    auto_unfold: bool = False,
) -> PatternPiece:
    """
    Applies configurable auto-unfold logic (F14):
    - When auto_unfold is True and piece has a fold edge:
      Unfolds the piece into a full symmetrical 1:1 piece using unfold_piece_on_fold,
      preserving the fold axis as an InternalLine on Layer 5.
    - When auto_unfold is False or piece has no fold:
      Returns the piece as-is (fold edge preserved on outer_contour for Clo3D export).
    """
    if auto_unfold and piece.has_fold:
        return unfold_piece_on_fold(piece)
    return piece


# ===========================================================================
# 3. Internal Cuts, Darts & Slits Classification (F15)
# ===========================================================================


def extract_internal_features(
    image: np.ndarray,
    piece: PatternPiece,
    scale: ScaleResult,
    transform: Optional[PixelToMMTransform] = None,
    piece_mask: Optional[np.ndarray] = None,
    min_stroke_length_px: float = 8.0,
    min_feature_area_px: int = 12,
    erode_boundary_px: int = 5,
) -> PatternPiece:
    """
    Isolates internal markings inside a piece's polygon mask (F15).
    Detects internal lines, darts, and slits, vectorizing them into InternalLine objects
    mapped to CAD millimetres on AAMA Layer 4.

    Args:
        image: BGR raster image.
        piece: PatternPiece to populate with internal features.
        scale: ScaleResult for converting px to mm.
        transform: Optional PixelToMMTransform mapping pixel coordinates to piece mm.
        piece_mask: Full-image binary mask (255 inside the piece).
        min_stroke_length_px: Minimum length in pixels to be accepted as a valid internal line.
        min_feature_area_px: Minimum connected component area in pixels.
        erode_boundary_px: Pixel erosion to exclude outer contour stroke.

    Returns:
        PatternPiece with detected internal_lines added.
    """
    if piece_mask is None:
        return piece

    # Erode piece mask so outer boundary strokes and nearby text are excluded
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_boundary_px, erode_boundary_px))
    interior_mask = cv2.erode(piece_mask, k)
    if int(interior_mask.sum()) == 0:
        return piece

    # Detect dark lines, red lines, and blue annotations inside interior
    # Convert image to HSV
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    inside = interior_mask > 0

    # 1. Dark strokes (Value < 90)
    dark_strokes = (hsv[..., 2] < 90) & inside

    # 2. Red strokes (Hue in [0..10] or [170..180], Saturation > 100, Value > 80)
    red_strokes = (
        ((hsv[..., 0] <= 10) | (hsv[..., 0] >= 170))
        & (hsv[..., 1] >= 100)
        & (hsv[..., 2] >= 80)
        & inside
    )

    combined_strokes = (dark_strokes | red_strokes).astype(np.uint8) * 255

    # Clean small noise
    combined_strokes = cv2.morphologyEx(
        combined_strokes,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
    )

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(combined_strokes)

    new_internals: List[InternalLine] = list(piece.internal_lines)
    tf = transform
    if tf is None:
        # Fallback transform using piece bounds
        min_x, min_y, _, _ = get_contour_bounds(piece.outer_contour)
        tf = PixelToMMTransform(
            origin_x_px=0.0,
            origin_y_px=0.0,
            mm_per_px_x=scale.mm_per_px_x,
            mm_per_px_y=scale.mm_per_px_y,
            y_up=True,
        )

    feature_counter = len(new_internals) + 1
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        length = math.hypot(w, h)

        if area < min_feature_area_px or length < min_stroke_length_px:
            continue

        comp_mask = (labels == i).astype(np.uint8) * 255
        cnts, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue

        cnt = max(cnts, key=cv2.contourArea)
        # Approximate contour with polygon
        epsilon = 0.04 * cv2.arcLength(cnt, False)
        approx = cv2.approxPolyDP(cnt, epsilon, False)
        pts_px = approx.reshape(-1, 2).astype(np.float64)

        if len(pts_px) < 2:
            continue

        # Classify internal line type: V-shape = 'dart', line = 'slit' or 'internal'
        is_red = bool((red_strokes & (labels == i)).sum() > 0.3 * area)
        if len(pts_px) == 3 and not cv2.isContourConvex(approx):
            ltype: InternalLineType = "dart"
            label = f"Zaszewka {feature_counter} (Dart)"
        elif is_red:
            ltype = "dart"
            label = f"Linia podziału / zaszewka {feature_counter}"
        else:
            ltype = "slit" if length > 20.0 else "internal"
            label = f"Linia wewnętrzna {feature_counter}"

        # Convert to CAD mm
        pts_mm = tf.apply(pts_px)
        segs: List[PathSegment] = []
        for p1, p2 in zip(pts_mm[:-1], pts_mm[1:]):
            segs.append(PathSegment.line((p1[0], p1[1]), (p2[0], p2[1])))

        if segs:
            new_internals.append(
                InternalLine(
                    id=f"{piece.id}_internal_{feature_counter}",
                    label=label,
                    line_type=ltype,
                    contour=Contour(segments=segs, is_closed=False),
                )
            )
            feature_counter += 1

    return piece.model_copy(update={"internal_lines": new_internals})


# ===========================================================================
# 4. Tailoring Notch Detection (F16)
# ===========================================================================


def compute_normal_at_point(
    contour: Contour,
    target_pt: Point2D,
) -> Tuple[Point2D, Point2D, float]:
    """
    Finds the closest point on a contour and computes the outward unit normal vector.
    Returns: (closest_point, outward_unit_normal, distance)
    """
    best_dist = float("inf")
    best_pt = target_pt
    best_normal = Point2D(x=0.0, y=1.0)

    target_shapely = Point(target_pt.x, target_pt.y)

    for seg in contour.segments:
        a = (seg.start.x, seg.start.y)
        b = (seg.end.x, seg.end.y)
        line = LineString([a, b])
        proj = line.interpolate(line.project(target_shapely))
        d = target_shapely.distance(proj)

        if d < best_dist:
            best_dist = d
            best_pt = Point2D(x=float(proj.x), y=float(proj.y))
            # Tangent vector from start to end
            dx = seg.end.x - seg.start.x
            dy = seg.end.y - seg.start.y
            L = math.hypot(dx, dy)
            if L > 1e-6:
                tx = dx / L
                ty = dy / L
                # Outward normal for CCW contour is (-ty, tx)
                best_normal = Point2D(x=-ty, y=tx)

    return best_pt, best_normal, best_dist


def add_notch(
    piece: PatternPiece,
    position: Union[Point2D, Tuple[float, float]],
    direction: Optional[Union[Point2D, Tuple[float, float]]] = None,
    length_mm: float = 5.0,
    notch_type: Literal["slit", "v_notch", "t_notch"] = "slit",
    label: str = "",
) -> PatternPiece:
    """
    Adds an alignment notch to a pattern piece at an exact coordinate.
    If direction is not provided, computes the outward normal vector automatically.
    """
    pos = Point2D(x=position[0], y=position[1]) if isinstance(position, (tuple, list)) else position
    if direction is None:
        closest, normal, _ = compute_normal_at_point(piece.outer_contour, pos)
        dir_pt = normal
    else:
        dir_pt = Point2D(x=direction[0], y=direction[1]) if isinstance(direction, (tuple, list)) else direction

    new_notch = Notch(
        id=f"{piece.id}_notch_{len(piece.notches) + 1}",
        position=pos,
        direction=dir_pt,
        length_mm=max(1.0, length_mm),
        notch_type=notch_type,
        label=label or f"Karbik {len(piece.notches) + 1}",
    )
    return piece.model_copy(update={"notches": list(piece.notches) + [new_notch]})


def add_notch_at_ratio(
    piece: PatternPiece,
    segment_index: int,
    t: float = 0.5,
    length_mm: float = 5.0,
    notch_type: Literal["slit", "v_notch", "t_notch"] = "slit",
    label: str = "",
) -> PatternPiece:
    """
    Places an alignment notch along a specific segment at parameter t in [0.0, 1.0].
    """
    segs = piece.outer_contour.segments
    if segment_index < 0 or segment_index >= len(segs):
        raise IndexError(f"Segment index {segment_index} out of range [0..{len(segs)-1}]")

    seg = segs[segment_index]
    t_clamped = max(0.0, min(1.0, float(t)))

    if seg.type == "line":
        px = seg.start.x + t_clamped * (seg.end.x - seg.start.x)
        py = seg.start.y + t_clamped * (seg.end.y - seg.start.y)
        dx = seg.end.x - seg.start.x
        dy = seg.end.y - seg.start.y
        L = math.hypot(dx, dy)
        normal = Point2D(x=-dy / L, y=dx / L) if L > 1e-6 else Point2D(x=0.0, y=1.0)
    else:
        # Cubic Bézier
        c1 = seg.cp1 or seg.start
        c2 = seg.cp2 or seg.end
        u = t_clamped
        mt = 1.0 - u
        px = (mt**3 * seg.start.x + 3 * mt**2 * u * c1.x + 3 * mt * u**2 * c2.x + u**3 * seg.end.x)
        py = (mt**3 * seg.start.y + 3 * mt**2 * u * c1.y + 3 * mt * u**2 * c2.y + u**3 * seg.end.y)
        d1x = 3 * (mt**2 * (c1.x - seg.start.x) + 2 * mt * u * (c2.x - c1.x) + u**2 * (seg.end.x - c2.x))
        d1y = 3 * (mt**2 * (c1.y - seg.start.y) + 2 * mt * u * (c2.y - c1.y) + u**2 * (seg.end.y - c2.y))
        L = math.hypot(d1x, d1y)
        normal = Point2D(x=-d1y / L, y=d1x / L) if L > 1e-6 else Point2D(x=0.0, y=1.0)

    return add_notch(
        piece=piece,
        position=Point2D(x=px, y=py),
        direction=normal,
        length_mm=length_mm,
        notch_type=notch_type,
        label=label,
    )


def detect_notches(
    piece: PatternPiece,
    tick_candidates: Optional[Sequence[Any]] = None,
    search_radius_mm: float = 8.0,
    notch_length_mm: float = 5.0,
) -> PatternPiece:
    """
    Matches candidate tick marks or points against the piece boundary and creates Notch objects (F16).
    """
    if not tick_candidates:
        return piece

    current_piece = piece
    for cand in tick_candidates:
        if isinstance(cand, (tuple, list)) and len(cand) >= 2:
            cand_pt = Point2D(x=float(cand[0]), y=float(cand[1]))
        elif isinstance(cand, Point2D):
            cand_pt = cand
        elif hasattr(cand, "x") and hasattr(cand, "y"):
            cand_pt = Point2D(x=float(cand.x), y=float(cand.y))
        else:
            continue

        closest_pt, normal_pt, dist = compute_normal_at_point(current_piece.outer_contour, cand_pt)
        if dist <= search_radius_mm:
            current_piece = add_notch(
                current_piece,
                position=closest_pt,
                direction=normal_pt,
                length_mm=notch_length_mm,
            )

    return current_piece


# ===========================================================================
# 5. Helpers
# ===========================================================================


def add_internal_line(
    piece: PatternPiece,
    contour: Contour,
    line_type: InternalLineType = "internal",
    label: str = "",
) -> PatternPiece:
    """Convenience helper to attach an InternalLine to a piece."""
    new_line = InternalLine(
        id=f"{piece.id}_internal_{len(piece.internal_lines) + 1}",
        label=label or f"Linia {len(piece.internal_lines) + 1}",
        line_type=line_type,
        contour=contour,
    )
    return piece.model_copy(update={"internal_lines": list(piece.internal_lines) + [new_line]})
