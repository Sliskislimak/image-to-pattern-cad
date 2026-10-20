"""
Raster Segmentation & Cubic Bézier Vectorizer for Stage 2 Pattern Semantic Digitizer (Milestone 2).

Pipeline (F06 → F07 → F09 → F08):

1. **Segmentation (F06)** – :func:`segment_pattern_pieces` /
   :func:`segment_pattern_pieces_detailed` separate filled pattern pieces from the background,
   text labels, dimension arrows and watermarks:

   * ``hsv_tan_filter=True`` (colour mode): auto-detects the dominant fill hue (e.g. the tan/beige
     of ``samples/gorro_trapper.png``), thresholds in HSV, closes small gaps left by text/watermark
     strokes, opens away thin dimension lines and fills holes. Candidates are filtered by area,
     mask coverage and *flat-fill ratio* (pattern pieces are flat colour fills, photographs are not).
   * ``hsv_tan_filter=False`` (line-art mode): Otsu luminance threshold of dark strokes, gap closing,
     hole filling and erosion by half the estimated stroke width so the result follows the stroke
     centre line.
   * In colour mode the function automatically falls back to line-art mode if nothing is found.

2. **Corner detection (F07)** – :func:`detect_corners` computes the discrete turning angle between
   the chords ``P[i-k]→P[i]`` and ``P[i]→P[i+k]`` on a uniformly resampled closed contour and keeps
   non-maximum-suppressed peaks above ``angle_thresh_deg``.

3. **Straight edges (F09) & Schneider fitting (F08)** – :func:`fit_cubic_bezier` turns an ordered
   point chain into ``PathSegment.line`` (collinear chains) or a minimal chain of
   ``PathSegment.bezier`` segments using Philip J. Schneider's least-squares algorithm
   ("An Algorithm for Automatically Fitting Digitized Curves", Graphics Gems I, 1990):
   chord-length parameterisation → least-squares solve of the two tangent magnitudes →
   Newton–Raphson re-parameterisation → recursive subdivision at the point of maximum error
   with a shared (G1-continuous) centre tangent.

4. **Vectorization (F06–F09 combined)** – :func:`vectorize_contour` /
   :func:`vectorize_contour_detailed` convert one raw OpenCV pixel boundary into a closed
   CAD :class:`~src.core.contract.Contour` in millimetres (Y axis up, origin at the lower-left
   corner of the bounding box – the same convention as ``src/presets/gorro_trapper.py``).

Units: pixel coordinates follow OpenCV (x right, y down, pixel centres at integer coordinates).
All fitted CAD output is expressed in millimetres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d
from shapely.geometry import Polygon

from src.core.contract import Contour, PathSegment, Point2D
from src.digitizer.scale_recovery import ScaleResult

ImageInput = Union[np.ndarray, str, Path]
SegmentationMode = Literal["color", "line_art"]

# Line-art mode: faint strokes are pixels at least this many gray levels darker than the paper...
_LINE_ART_PAPER_MARGIN = 35.0
# ...and thinner than this many pixels (wider pale regions are fills, not outlines).
_LINE_ART_THIN_MAX_PX = 7

__all__ = [
    "SegmentedPiece",
    "PixelToMMTransform",
    "VectorizationResult",
    "load_image",
    "build_piece_mask",
    "segment_pattern_pieces",
    "segment_pattern_pieces_detailed",
    "resample_closed_polyline",
    "detect_corners",
    "fit_cubic_bezier",
    "vectorize_contour",
    "vectorize_contour_detailed",
    "max_deviation_to_contour",
]


# ---------------------------------------------------------------------------
# 0. Data structures
# ---------------------------------------------------------------------------


@dataclass
class SegmentedPiece:
    """One pattern piece candidate segmented from a raster image (pixel space)."""

    index: int
    contour_px: np.ndarray  # (N, 2) float64, OpenCV boundary pixel centres, closed (no repeat)
    bbox_px: Tuple[int, int, int, int]  # x, y, w, h
    area_px: float
    coverage: float  # fraction of raw colour/stroke mask pixels inside the filled contour
    flat_fill_ratio: float  # fraction of pixels close to the modal fill colour (1.0 in line-art mode)
    mean_bgr: Tuple[float, float, float]
    mode: SegmentationMode
    mask: np.ndarray = field(repr=False)  # full-image uint8 mask (255 inside the piece)

    @property
    def centroid_px(self) -> Tuple[float, float]:
        m = cv2.moments(self.contour_px.astype(np.float32).reshape(-1, 1, 2))
        if abs(m["m00"]) < 1e-9:
            c = self.contour_px.mean(axis=0)
            return (float(c[0]), float(c[1]))
        return (m["m10"] / m["m00"], m["m01"] / m["m00"])


@dataclass(frozen=True)
class PixelToMMTransform:
    """
    Affine map from image pixels (x right, y down) to piece-local CAD millimetres.

    ``X_mm = (x_px - origin_x_px) * mm_per_px_x``
    ``Y_mm = (origin_y_px - y_px) * mm_per_px_y``   when ``y_up=True`` (origin = bottom edge)
    ``Y_mm = (y_px - origin_y_px) * mm_per_px_y``   when ``y_up=False`` (origin = top edge)

    Reused by the semantic classifier (Milestone 3) to map internal lines and notches detected
    in pixel space onto the same piece-local coordinate frame as the fitted contour.
    """

    origin_x_px: float
    origin_y_px: float
    mm_per_px_x: float
    mm_per_px_y: float
    y_up: bool = True

    def apply(self, pts_px: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts_px, dtype=np.float64).reshape(-1, 2)
        out = np.empty_like(pts)
        out[:, 0] = (pts[:, 0] - self.origin_x_px) * self.mm_per_px_x
        if self.y_up:
            out[:, 1] = (self.origin_y_px - pts[:, 1]) * self.mm_per_px_y
        else:
            out[:, 1] = (pts[:, 1] - self.origin_y_px) * self.mm_per_px_y
        return out

    def apply_point(self, x_px: float, y_px: float) -> Point2D:
        p = self.apply(np.array([[x_px, y_px]]))[0]
        return Point2D(x=float(p[0]), y=float(p[1]))

    def inverse(self, pts_mm: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts_mm, dtype=np.float64).reshape(-1, 2)
        out = np.empty_like(pts)
        out[:, 0] = pts[:, 0] / self.mm_per_px_x + self.origin_x_px
        if self.y_up:
            out[:, 1] = self.origin_y_px - pts[:, 1] / self.mm_per_px_y
        else:
            out[:, 1] = pts[:, 1] / self.mm_per_px_y + self.origin_y_px
        return out


@dataclass
class VectorizationResult:
    """Full diagnostic output of :func:`vectorize_contour_detailed`."""

    contour: Contour  # closed CAD contour in mm
    transform: PixelToMMTransform
    boundary_px: np.ndarray  # resampled, edge-corrected boundary in px (N, 2)
    smoothed_px: np.ndarray  # smoothed boundary used for fitting (N, 2)
    corner_indices: List[int]  # true geometric corners (indices into boundary_px)
    breakpoint_indices: List[int]  # corners + smooth breakpoints (indices into boundary_px)
    effective_tolerance_mm: float
    effective_collinear_tol_mm: float
    max_fit_error_mm: float  # max deviation of the smoothed boundary from the fitted contour
    n_lines: int
    n_beziers: int

    @property
    def n_segments(self) -> int:
        return len(self.contour.segments)

    @property
    def corners_px(self) -> np.ndarray:
        return self.boundary_px[self.corner_indices] if self.corner_indices else np.zeros((0, 2))


# ---------------------------------------------------------------------------
# 1. Helpers
# ---------------------------------------------------------------------------


def load_image(image: ImageInput) -> np.ndarray:
    """Loads an image path (unicode-safe on Windows) or validates an in-memory array; returns BGR uint8."""
    if isinstance(image, (str, Path)):
        path = Path(image)
        if not path.is_file():
            raise FileNotFoundError(f"Image file not found: {path}")
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"Unable to decode image: {path}")
    elif isinstance(image, np.ndarray):
        img = image
    else:
        raise TypeError(f"Expected np.ndarray, str or Path, got {type(image).__name__}")

    if img.size == 0:
        raise ValueError("Image is empty.")
    if img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.ndim == 3 and img.shape[2] == 4:
        # Composite transparency onto white (transparent PNG exports of patterns).
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        rgb = img[:, :, :3].astype(np.float32)
        return (rgb * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
    if img.ndim == 3 and img.shape[2] == 3:
        return img
    if img.ndim == 3 and img.shape[2] == 1:
        return cv2.cvtColor(img[:, :, 0], cv2.COLOR_GRAY2BGR)
    raise ValueError(f"Unsupported image shape: {img.shape}")


def _as_points(pts: Any) -> np.ndarray:
    """Normalises (N,2), (N,1,2) or list-of-tuples into a float64 (N,2) array."""
    arr = np.asarray(pts, dtype=np.float64)
    if arr.ndim == 3 and arr.shape[1] == 1:
        arr = arr[:, 0, :]
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"Expected point array of shape (N, 2), got {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ValueError("Point array contains non-finite values.")
    return arr


def _dedupe_closed(pts: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """Removes consecutive duplicates and an explicit closing point."""
    if len(pts) == 0:
        return pts
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(pts, axis=0), axis=1) > eps
    out = pts[keep]
    if len(out) > 1 and np.linalg.norm(out[0] - out[-1]) <= eps:
        out = out[:-1]
    return out


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return np.zeros(2)
    return v / n


def resample_closed_polyline(pts: np.ndarray, step: float = 1.0) -> np.ndarray:
    """Uniformly resamples a closed polyline by arc length (spacing ≈ ``step``)."""
    p = _dedupe_closed(_as_points(pts))
    if len(p) < 3:
        return p
    closed = np.vstack([p, p[:1]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if total <= 1e-9:
        return p[:1]
    n = max(8, int(round(total / max(step, 1e-6))))
    s = np.linspace(0.0, total, n, endpoint=False)
    x = np.interp(s, cum, closed[:, 0])
    y = np.interp(s, cum, closed[:, 1])
    return np.column_stack([x, y])


# ---------------------------------------------------------------------------
# 2. Segmentation (F06)
# ---------------------------------------------------------------------------


def _dominant_fill_hue(hsv: np.ndarray, min_s: int = 30, min_v: int = 90) -> Optional[Tuple[int, float]]:
    """Returns (dominant hue, median saturation at that hue) among moderately saturated pixels."""
    h = hsv[..., 0]
    s = hsv[..., 1]
    v = hsv[..., 2]
    sel = (s >= min_s) & (v >= min_v)
    if int(sel.sum()) < 200:
        return None
    hist = np.bincount(h[sel].ravel(), minlength=180).astype(np.float64)
    # Circular smoothing (hue wraps at 180 in OpenCV).
    hist_s = (np.roll(hist, 1) + 2 * hist + np.roll(hist, -1)) / 4.0
    h0 = int(np.argmax(hist_s))
    near = sel & (np.minimum(np.abs(h.astype(int) - h0), 180 - np.abs(h.astype(int) - h0)) <= 3)
    s_med = float(np.median(s[near])) if near.any() else float(np.median(s[sel]))
    return h0, s_med


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    if cnts:
        cv2.drawContours(filled, cnts, -1, 255, thickness=cv2.FILLED)
    return filled


def _estimate_stroke_width(stroke_mask: np.ndarray) -> float:
    """Estimates line stroke width (px) as 2 × median ridge value of the distance transform."""
    if not stroke_mask.any():
        return 1.0
    dt = cv2.distanceTransform((stroke_mask > 0).astype(np.uint8), cv2.DIST_L2, 3)
    dil = cv2.dilate(dt, np.ones((3, 3), np.uint8))
    ridge = (dt > 0) & (dt >= dil - 1e-6)
    vals = dt[ridge]
    if vals.size == 0:
        return 1.0
    return float(max(1.0, 2.0 * np.median(vals)))


def build_piece_mask(
    image: ImageInput,
    hsv_tan_filter: bool = True,
    hue_tolerance: int = 8,
    close_kernel_px: int = 5,
    close_iterations: int = 2,
    open_iterations: int = 1,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Builds a binary piece mask.

    Returns:
        (filled_mask, raw_mask, info) – ``filled_mask`` is the cleaned, hole-filled piece mask;
        ``raw_mask`` the un-morphed colour (or stroke) mask used for coverage statistics.
    """
    img = load_image(image)
    info: Dict[str, Any] = {"mode": "color" if hsv_tan_filter else "line_art"}
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_kernel_px, close_kernel_px))

    if hsv_tan_filter:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        dom = _dominant_fill_hue(hsv)
        if dom is None:
            empty = np.zeros(img.shape[:2], np.uint8)
            info["dominant_hue"] = None
            return empty, empty, info
        h0, s_med = dom
        h = hsv[..., 0].astype(np.int32)
        dh = np.minimum(np.abs(h - h0), 180 - np.abs(h - h0))
        s = hsv[..., 1].astype(np.float64)
        v = hsv[..., 2]
        s_lo = max(25.0, 0.45 * s_med)
        s_hi = min(255.0, 2.0 * s_med)
        raw = ((dh <= hue_tolerance) & (s >= s_lo) & (s <= s_hi) & (v >= 100)).astype(np.uint8) * 255
        info.update({"dominant_hue": h0, "median_saturation": s_med, "s_range": (s_lo, s_hi)})
        m = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, k, iterations=close_iterations)
        if open_iterations > 0:
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k, iterations=open_iterations)
        filled = _fill_holes(m)
        return filled, raw, info

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    otsu_t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    dark = gray < otsu_t
    # Faint, anti-aliased / JPEG-compressed outlines (gray ~190-210 on a white sheet) fall above the
    # Otsu threshold of text-heavy sheets and leave gaps in piece contours. Also accept pixels that
    # are noticeably darker than the paper, but only where they form THIN structures: wide pale
    # regions (light fills, photos) must not turn into one fat "stroke".
    paper_level = float(np.percentile(gray, 90))
    faint = ((gray < paper_level - _LINE_ART_PAPER_MARGIN) * 255).astype(np.uint8)
    wide_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_LINE_ART_THIN_MAX_PX, _LINE_ART_THIN_MAX_PX))
    wide = cv2.dilate(cv2.morphologyEx(faint, cv2.MORPH_OPEN, wide_k), np.ones((3, 3), np.uint8))
    thin_faint = (faint > 0) & (wide == 0)
    strokes = ((dark | thin_faint) * 255).astype(np.uint8)
    stroke_w = _estimate_stroke_width(strokes)
    closed = cv2.morphologyEx(strokes, cv2.MORPH_CLOSE, k, iterations=1)
    filled = _fill_holes(closed)
    # Remove open strokes (lines that enclose nothing): keep only regions that are wider than strokes.
    half = int(round(stroke_w / 2.0))
    if half >= 1:
        ek = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * half + 1, 2 * half + 1))
        filled = cv2.erode(filled, ek, iterations=1)
    info.update({"stroke_width_px": stroke_w, "erosion_px": half})
    return filled, strokes, info


def _flat_fill_ratio(img: np.ndarray, sel: np.ndarray, tol: float = 12.0) -> Tuple[float, Tuple[float, float, float]]:
    """Fraction of selected pixels within ``tol`` (BGR L2) of the modal (quantised) colour."""
    vals = img[sel].astype(np.int32)
    if len(vals) == 0:
        return 0.0, (0.0, 0.0, 0.0)
    q = vals // 4
    key = (q[:, 0] << 12) | (q[:, 1] << 6) | q[:, 2]
    uniq, cnt = np.unique(key, return_counts=True)
    mk = int(uniq[np.argmax(cnt)])
    modal = np.array([(mk >> 12) & 63, (mk >> 6) & 63, mk & 63], dtype=np.float64) * 4 + 2
    d = np.linalg.norm(vals - modal, axis=1)
    mean = vals.mean(axis=0)
    return float((d <= tol).mean()), (float(mean[0]), float(mean[1]), float(mean[2]))


def segment_pattern_pieces_detailed(
    image: ImageInput,
    min_area_px: float = 1000.0,
    hsv_tan_filter: bool = True,
    min_coverage: float = 0.7,
    min_flat_fill_ratio: float = 0.2,
    auto_fallback: bool = True,
) -> List[SegmentedPiece]:
    """
    Segments pattern pieces and returns rich per-piece diagnostics, sorted in reading order
    (top-to-bottom rows, then left-to-right).
    """
    img = load_image(image)
    filled, raw, info = build_piece_mask(img, hsv_tan_filter=hsv_tan_filter)
    mode: SegmentationMode = "color" if hsv_tan_filter else "line_art"

    cnts, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    pieces: List[SegmentedPiece] = []
    for c in cnts:
        area = float(cv2.contourArea(c))
        if area < min_area_px or len(c) < 8:
            continue
        piece_mask = np.zeros(img.shape[:2], np.uint8)
        cv2.drawContours(piece_mask, [c], -1, 255, thickness=cv2.FILLED)
        inside = piece_mask > 0
        n_inside = int(inside.sum())
        if n_inside == 0:
            continue
        if mode == "color":
            coverage = float((raw[inside] > 0).mean())
            flat, mean_bgr = _flat_fill_ratio(img, inside & (raw > 0))
            if coverage < min_coverage or flat < min_flat_fill_ratio:
                continue
        else:
            coverage = 1.0
            flat = 1.0
            m = img[inside].mean(axis=0)
            mean_bgr = (float(m[0]), float(m[1]), float(m[2]))
        pts = _dedupe_closed(c[:, 0, :].astype(np.float64))
        pieces.append(
            SegmentedPiece(
                index=-1,
                contour_px=pts,
                bbox_px=tuple(int(v) for v in cv2.boundingRect(c)),  # type: ignore[arg-type]
                area_px=area,
                coverage=coverage,
                flat_fill_ratio=flat,
                mean_bgr=mean_bgr,
                mode=mode,
                mask=piece_mask,
            )
        )

    if not pieces and mode == "color" and auto_fallback:
        return segment_pattern_pieces_detailed(
            img,
            min_area_px=min_area_px,
            hsv_tan_filter=False,
            min_coverage=min_coverage,
            min_flat_fill_ratio=min_flat_fill_ratio,
            auto_fallback=False,
        )

    # Reading order: group into rows by vertical overlap of bbox centres.
    if pieces:
        med_h = float(np.median([p.bbox_px[3] for p in pieces]))
        row_h = max(20.0, 0.5 * med_h)
        pieces.sort(key=lambda p: (int((p.bbox_px[1]) // row_h), p.bbox_px[0]))
    for i, p in enumerate(pieces):
        p.index = i
    return pieces


def segment_pattern_pieces(
    image: ImageInput,
    min_area_px: float = 1000.0,
    hsv_tan_filter: bool = True,
) -> List[np.ndarray]:
    """
    Segments filled pattern pieces from a raster image.

    Returns:
        List of closed pixel boundaries, each a float64 array of shape (N, 2) (OpenCV pixel
        centres, x right / y down), in reading order.
    """
    return [p.contour_px for p in segment_pattern_pieces_detailed(image, min_area_px, hsv_tan_filter)]


# ---------------------------------------------------------------------------
# 3. Corner detection (F07)
# ---------------------------------------------------------------------------


def _turning_angles_deg(pts: np.ndarray, k: int) -> np.ndarray:
    prev = np.roll(pts, k, axis=0)
    nxt = np.roll(pts, -k, axis=0)
    v1 = pts - prev
    v2 = nxt - pts
    n1 = np.linalg.norm(v1, axis=1)
    n2 = np.linalg.norm(v2, axis=1)
    denom = np.maximum(n1 * n2, 1e-12)
    cosang = np.clip(np.einsum("ij,ij->i", v1, v2) / denom, -1.0, 1.0)
    return np.degrees(np.arccos(cosang))


def detect_corners(
    contour_pts: np.ndarray,
    angle_thresh_deg: float = 35.0,
    window_k: int = 5,
) -> List[int]:
    """
    Detects sharp corners on a closed contour via the discrete turning angle.

    For each point ``P[i]`` the angle between ``P[i]-P[i-k]`` and ``P[i+k]-P[i]`` is computed
    (0° = straight, 180° = full reversal). Points with angle ≥ ``angle_thresh_deg`` are candidates;
    non-maximum suppression keeps only the strongest candidate within ±``window_k`` points.

    The contour should be (approximately) uniformly sampled – see :func:`resample_closed_polyline`.

    Returns:
        Sorted list of corner indices into ``contour_pts``.
    """
    pts = _dedupe_closed(_as_points(contour_pts))
    n = len(pts)
    k = max(1, int(window_k))
    if n < 2 * k + 1:
        return []
    ang = _turning_angles_deg(pts, k)
    cand = np.where(ang >= angle_thresh_deg)[0]
    if cand.size == 0:
        return []
    order = cand[np.argsort(-ang[cand], kind="stable")]
    accepted: List[int] = []
    for i in order:
        ok = True
        for j in accepted:
            d = abs(int(i) - j)
            if min(d, n - d) <= k:
                ok = False
                break
        if ok:
            accepted.append(int(i))
    return sorted(accepted)


# ---------------------------------------------------------------------------
# 4. Schneider least-squares cubic Bézier fitting (F08) & straight edges (F09)
# ---------------------------------------------------------------------------


def _bez_eval(ctrl: np.ndarray, t: np.ndarray) -> np.ndarray:
    t = np.asarray(t, dtype=np.float64)
    mt = 1.0 - t
    return (
        (mt**3)[:, None] * ctrl[0]
        + (3.0 * mt**2 * t)[:, None] * ctrl[1]
        + (3.0 * mt * t**2)[:, None] * ctrl[2]
        + (t**3)[:, None] * ctrl[3]
    )


def _bez_d1(ctrl: np.ndarray, t: np.ndarray) -> np.ndarray:
    mt = 1.0 - t
    return 3.0 * (
        (mt**2)[:, None] * (ctrl[1] - ctrl[0])
        + (2.0 * mt * t)[:, None] * (ctrl[2] - ctrl[1])
        + (t**2)[:, None] * (ctrl[3] - ctrl[2])
    )


def _bez_d2(ctrl: np.ndarray, t: np.ndarray) -> np.ndarray:
    mt = 1.0 - t
    return 6.0 * (
        mt[:, None] * (ctrl[2] - 2.0 * ctrl[1] + ctrl[0]) + t[:, None] * (ctrl[3] - 2.0 * ctrl[2] + ctrl[1])
    )


def _chord_length_parameterize(pts: np.ndarray) -> np.ndarray:
    d = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    if d[-1] <= 1e-12:
        return np.linspace(0.0, 1.0, len(pts))
    return d / d[-1]


def _left_tangent(pts: np.ndarray, span: int = 3) -> np.ndarray:
    m = min(span, len(pts) - 1)
    t = _normalize(pts[1 : m + 1].mean(axis=0) - pts[0])
    if not t.any():
        t = _normalize(pts[-1] - pts[0])
    return t


def _right_tangent(pts: np.ndarray, span: int = 3) -> np.ndarray:
    m = min(span, len(pts) - 1)
    t = _normalize(pts[-m - 1 : -1].mean(axis=0) - pts[-1])
    if not t.any():
        t = _normalize(pts[0] - pts[-1])
    return t


def _center_tangent(pts: np.ndarray, i: int, span: int = 2) -> np.ndarray:
    a = max(0, i - span)
    b = min(len(pts) - 1, i + span)
    t = _normalize(pts[a] - pts[b])
    if not t.any():
        t = _normalize(pts[max(0, i - 1)] - pts[min(len(pts) - 1, i + 1)])
    return t


def _generate_bezier(pts: np.ndarray, u: np.ndarray, t1: np.ndarray, t2: np.ndarray) -> np.ndarray:
    """Least-squares solve for the tangent magnitudes alpha1/alpha2 (Schneider eqs.)."""
    p0, p3 = pts[0], pts[-1]
    mt = 1.0 - u
    b0 = mt**3
    b1 = 3.0 * mt**2 * u
    b2 = 3.0 * mt * u**2
    b3 = u**3
    a1 = b1[:, None] * t1
    a2 = b2[:, None] * t2
    c00 = float(np.sum(a1 * a1))
    c01 = float(np.sum(a1 * a2))
    c11 = float(np.sum(a2 * a2))
    tmp = pts - (b0 + b1)[:, None] * p0 - (b2 + b3)[:, None] * p3
    x0 = float(np.sum(a1 * tmp))
    x1 = float(np.sum(a2 * tmp))
    det = c00 * c11 - c01 * c01
    seg_len = float(np.linalg.norm(p3 - p0))
    if seg_len <= 1e-12:
        seg_len = float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1))) / 2.0
    alpha1 = alpha2 = 0.0
    if abs(det) > 1e-12 * max(1.0, c00 * c11):
        alpha1 = (x0 * c11 - x1 * c01) / det
        alpha2 = (c00 * x1 - c01 * x0) / det
    eps = 1e-6 * max(seg_len, 1e-9)
    # Fallback (Wu/Barsky heuristic) for degenerate or non-physical solutions; also cap wild overshoots.
    if not (math.isfinite(alpha1) and math.isfinite(alpha2)) or alpha1 < eps or alpha2 < eps:
        alpha1 = alpha2 = seg_len / 3.0
    cap = 2.0 * max(seg_len, 1e-9)
    alpha1 = min(alpha1, cap)
    alpha2 = min(alpha2, cap)
    return np.array([p0, p0 + alpha1 * t1, p3 + alpha2 * t2, p3])


def _max_error(pts: np.ndarray, ctrl: np.ndarray, u: np.ndarray) -> Tuple[float, int]:
    d = np.linalg.norm(_bez_eval(ctrl, u) - pts, axis=1)
    i = int(np.argmax(d))
    return float(d[i]), i


def _reparameterize(pts: np.ndarray, ctrl: np.ndarray, u: np.ndarray) -> np.ndarray:
    """One Newton–Raphson step per sample: u ← u − f(u)/f'(u), f = (Q(u)−P)·Q'(u)."""
    q = _bez_eval(ctrl, u)
    q1 = _bez_d1(ctrl, u)
    q2 = _bez_d2(ctrl, u)
    diff = q - pts
    num = np.einsum("ij,ij->i", diff, q1)
    den = np.einsum("ij,ij->i", q1, q1) + np.einsum("ij,ij->i", diff, q2)
    safe = np.abs(den) > 1e-12
    new_u = u.copy()
    new_u[safe] = u[safe] - num[safe] / den[safe]
    new_u = np.clip(new_u, 0.0, 1.0)
    new_u[0], new_u[-1] = 0.0, 1.0
    # Keep parameters monotone (robustness on noisy pixel data).
    return np.maximum.accumulate(new_u)


def _fit_cubic_recursive(
    pts: np.ndarray,
    t1: np.ndarray,
    t2: np.ndarray,
    error: float,
    depth: int,
    max_depth: int,
    max_iterations: int = 20,
) -> List[np.ndarray]:
    n = len(pts)
    if n == 2:
        dist = float(np.linalg.norm(pts[1] - pts[0])) / 3.0
        return [np.array([pts[0], pts[0] + t1 * dist, pts[1] + t2 * dist, pts[1]])]

    u = _chord_length_parameterize(pts)
    ctrl = _generate_bezier(pts, u, t1, t2)
    max_err, split = _max_error(pts, ctrl, u)
    if max_err < error:
        return [ctrl]

    best_ctrl, best_err, best_split = ctrl, max_err, split
    if max_err < error * 4.0:
        for _ in range(max_iterations):
            u = _reparameterize(pts, ctrl, u)
            ctrl = _generate_bezier(pts, u, t1, t2)
            max_err, split = _max_error(pts, ctrl, u)
            if max_err < best_err:
                best_ctrl, best_err, best_split = ctrl, max_err, split
            if max_err < error:
                return [ctrl]

    if depth >= max_depth or n <= 3:
        return [best_ctrl]

    split = int(min(max(best_split, 1), n - 2))
    tc = _center_tangent(pts, split)
    left = _fit_cubic_recursive(pts[: split + 1], t1, tc, error, depth + 1, max_depth, max_iterations)
    right = _fit_cubic_recursive(pts[split:], -tc, t2, error, depth + 1, max_depth, max_iterations)
    return left + right


def _point_line_distances(pts: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    L = float(np.linalg.norm(ab))
    if L <= 1e-12:
        return np.linalg.norm(pts - a, axis=1)
    return np.abs(ab[0] * (pts[:, 1] - a[1]) - ab[1] * (pts[:, 0] - a[0])) / L


def _is_collinear_chain(pts: np.ndarray, tol: float) -> bool:
    a, b = pts[0], pts[-1]
    ab = b - a
    L = float(np.linalg.norm(ab))
    if L <= max(tol, 1e-9):
        return False
    if float(np.max(_point_line_distances(pts, a, b))) > tol:
        return False
    # Reject U-turns / back-tracking: projections must stay within the chord (with tolerance).
    proj = (pts - a) @ ab / L
    return bool(proj.min() >= -tol and proj.max() <= L + tol)


def _ctrl_is_straight(ctrl: np.ndarray, tol: float) -> bool:
    a, b = ctrl[0], ctrl[3]
    L = float(np.linalg.norm(b - a))
    if L <= 1e-9:
        return False
    if float(np.max(_point_line_distances(ctrl[1:3], a, b))) > tol:
        return False
    proj = (ctrl[1:3] - a) @ (b - a) / L
    return bool(proj.min() >= -tol and proj.max() <= L + tol)


def _ctrl_to_segment(ctrl: np.ndarray, collinear_tol: float) -> PathSegment:
    s, c1, c2, e = (tuple(map(float, p)) for p in ctrl)
    if _ctrl_is_straight(ctrl, collinear_tol):
        return PathSegment.line(s, e)
    return PathSegment.bezier(s, c1, c2, e)


def fit_cubic_bezier(
    points: np.ndarray,
    max_error_mm: float = 0.5,
    max_subdivision: int = 8,
    collinear_tol_mm: float = 0.3,
    left_tangent: Optional[Sequence[float]] = None,
    right_tangent: Optional[Sequence[float]] = None,
) -> List[PathSegment]:
    """
    Fits an ordered, open point chain with lines / cubic Bézier segments (Schneider, Graphics Gems I).

    Args:
        points: (N, 2) ordered samples, first → last. Tolerances are in the same unit as the
            points (millimetres for CAD data, hence the ``_mm`` suffix).
        max_error_mm: Maximum allowed distance between any sample and its fitted curve point.
        max_subdivision: Maximum recursion depth (≤ 2**max_subdivision Bézier segments).
        collinear_tol_mm: If every sample lies within this distance of the chord, a single
            ``PathSegment.line`` is returned (F09). Near-straight Bézier pieces are also demoted to lines.
        left_tangent: Optional unit tangent at the first point, pointing *into* the chain.
        right_tangent: Optional unit tangent at the last point, pointing *back into* the chain
            (Schneider's convention). Supplying shared tangents yields G1 joints between chains.

    Returns:
        Contiguous list of ``PathSegment`` from ``points[0]`` to ``points[-1]``.
    """
    pts = _as_points(points)
    if max_error_mm <= 0 or not math.isfinite(max_error_mm):
        raise ValueError("max_error_mm must be a positive finite number.")
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(pts, axis=0), axis=1) > 1e-12
    pts = pts[keep]
    if len(pts) < 2:
        raise ValueError("At least two distinct points are required for fitting.")
    if len(pts) == 2 or _is_collinear_chain(pts, collinear_tol_mm):
        return [PathSegment.line(tuple(map(float, pts[0])), tuple(map(float, pts[-1])))]

    t1 = _normalize(np.asarray(left_tangent, dtype=np.float64)) if left_tangent is not None else _left_tangent(pts)
    t2 = _normalize(np.asarray(right_tangent, dtype=np.float64)) if right_tangent is not None else _right_tangent(pts)
    if not t1.any():
        t1 = _left_tangent(pts)
    if not t2.any():
        t2 = _right_tangent(pts)

    ctrls = _fit_cubic_recursive(pts, t1, t2, float(max_error_mm), 0, int(max_subdivision))
    segs = [_ctrl_to_segment(c, collinear_tol_mm) for c in ctrls]
    # Exact C0 continuity between consecutive segments.
    for a, b in zip(segs[:-1], segs[1:]):
        b.start = a.end
    return segs


# ---------------------------------------------------------------------------
# 5. Contour vectorization (F06–F09 combined)
# ---------------------------------------------------------------------------


def _edge_corrected_boundary(pts_px: np.ndarray, offset_px: float) -> np.ndarray:
    """
    OpenCV boundaries trace pixel *centres*; the true region edge lies 0.5 px further out.
    A mitred polygon buffer moves the boundary outward so measured sizes are not 1 px short.
    """
    if offset_px <= 0 or len(pts_px) < 3:
        return pts_px
    poly = Polygon(pts_px)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return pts_px
    buf = poly.buffer(offset_px, join_style=2, mitre_limit=2.0)
    if buf.geom_type == "MultiPolygon":
        buf = max(buf.geoms, key=lambda g: g.area)
    if buf.is_empty:
        return pts_px
    return np.asarray(buf.exterior.coords, dtype=np.float64)[:-1]


def _smooth_spans(pts: np.ndarray, corners: List[int], sigma: float) -> np.ndarray:
    """Gaussian smoothing between consecutive corners (corners stay pinned)."""
    if sigma <= 0:
        return pts.copy()
    n = len(pts)
    if not corners:
        return np.column_stack(
            [gaussian_filter1d(pts[:, 0], sigma, mode="wrap"), gaussian_filter1d(pts[:, 1], sigma, mode="wrap")]
        )
    out = pts.copy()
    for a, b in zip(corners, corners[1:] + [corners[0] + n]):
        idx = np.arange(a, b + 1) % n
        span = pts[idx]
        if len(span) < 4:
            continue
        sm = np.column_stack(
            [gaussian_filter1d(span[:, 0], sigma, mode="nearest"), gaussian_filter1d(span[:, 1], sigma, mode="nearest")]
        )
        sm[0], sm[-1] = span[0], span[-1]
        out[idx[1:-1]] = sm[1:-1]
    return out


def _smooth_breakpoints(pts: np.ndarray, corners: List[int], min_gap: int) -> List[int]:
    """Adds G1 (smooth) breakpoints so every closed contour is split into ≥ 2 open chains."""
    n = len(pts)
    if len(corners) >= 2:
        return []
    if len(corners) == 1:
        c = corners[0]
        far = int(np.argmax(np.linalg.norm(pts - pts[c], axis=1)))
        return [far] if min(abs(far - c), n - abs(far - c)) > min_gap else [(c + n // 2) % n]
    # No corners (ellipse/circle-like): split at the four axis extremes.
    cand = [int(np.argmin(pts[:, 0])), int(np.argmax(pts[:, 0])), int(np.argmin(pts[:, 1])), int(np.argmax(pts[:, 1]))]
    out: List[int] = []
    for c in sorted(set(cand)):
        if all(min(abs(c - o), n - abs(c - o)) > min_gap for o in out):
            out.append(c)
    if len(out) < 2:
        out = [0, n // 2]
    return out


def vectorize_contour_detailed(
    contour_pts: np.ndarray,
    scale: ScaleResult,
    max_error_mm: float = 0.5,
    collinear_tol_mm: float = 0.3,
    angle_thresh_deg: float = 35.0,
    window_k: int = 5,
    smooth_sigma_px: float = 1.0,
    min_pixel_tolerance: float = 0.75,
    edge_correction_px: float = 0.5,
    resample_step_px: float = 1.0,
    max_subdivision: int = 8,
    y_up: bool = True,
    normalize_origin: bool = True,
) -> VectorizationResult:
    """
    Converts one raw pixel boundary into a closed CAD contour in millimetres, with diagnostics.

    Steps: dedupe → 0.5 px edge correction → uniform resampling → corner detection (F07) →
    span-wise Gaussian smoothing with pinned corners → smooth G1 breakpoints → px→mm transform
    (anisotropic, Y-up) → per-chain straight-line test (F09) / Schneider fitting (F08) →
    exact C0 closure.

    Pixel quantisation limits achievable accuracy, so the effective fitting tolerance is
    ``max(max_error_mm, min_pixel_tolerance × mm_per_px)``.
    """
    raw = _dedupe_closed(_as_points(contour_pts))
    if len(raw) < 3:
        raise ValueError("A closed contour needs at least 3 distinct points.")
    if Polygon(raw).area <= 1e-9:
        raise ValueError("Contour encloses zero area.")

    boundary = _edge_corrected_boundary(raw, edge_correction_px)
    boundary = resample_closed_polyline(boundary, step=resample_step_px)
    n = len(boundary)
    corners = detect_corners(boundary, angle_thresh_deg=angle_thresh_deg, window_k=window_k)
    smoothed = _smooth_spans(boundary, corners, smooth_sigma_px)
    extra = _smooth_breakpoints(smoothed, corners, min_gap=max(2, window_k))
    breakpoints = sorted(set(corners) | set(extra))

    sx, sy = scale.mm_per_px_x, scale.mm_per_px_y
    if normalize_origin:
        ox = float(boundary[:, 0].min())
        oy = float(boundary[:, 1].max()) if y_up else float(boundary[:, 1].min())
    else:
        ox = 0.0
        oy = 0.0
    transform = PixelToMMTransform(ox, oy, sx, sy, y_up)
    pts_mm = transform.apply(smoothed)

    mean_mm_px = 0.5 * (sx + sy)
    tol = max(float(max_error_mm), min_pixel_tolerance * mean_mm_px)
    col_tol = max(float(collinear_tol_mm), 0.6 * min_pixel_tolerance * mean_mm_px)

    corner_set = set(corners)
    smooth_tangent: Dict[int, np.ndarray] = {}
    for b in breakpoints:
        if b not in corner_set:
            a_i, c_i = (b - window_k) % n, (b + window_k) % n
            smooth_tangent[b] = _normalize(pts_mm[c_i] - pts_mm[a_i])  # forward direction

    segments: List[PathSegment] = []
    for a, b in zip(breakpoints, breakpoints[1:] + [breakpoints[0] + n]):
        idx = np.arange(a, b + 1) % n
        chain = pts_mm[idx]
        lt = smooth_tangent.get(a % n)
        rt = smooth_tangent.get(b % n)
        segments.extend(
            fit_cubic_bezier(
                chain,
                max_error_mm=tol,
                max_subdivision=max_subdivision,
                collinear_tol_mm=col_tol,
                left_tangent=lt,
                right_tangent=(-rt if rt is not None else None),
            )
        )

    for prev, nxt in zip(segments, segments[1:] + segments[:1]):
        nxt.start = prev.end
    contour = Contour(segments=segments, is_closed=True)

    max_err = max_deviation_to_contour(pts_mm, contour)
    n_lines = sum(1 for s in segments if s.type == "line")
    return VectorizationResult(
        contour=contour,
        transform=transform,
        boundary_px=boundary,
        smoothed_px=smoothed,
        corner_indices=corners,
        breakpoint_indices=breakpoints,
        effective_tolerance_mm=tol,
        effective_collinear_tol_mm=col_tol,
        max_fit_error_mm=max_err,
        n_lines=n_lines,
        n_beziers=len(segments) - n_lines,
    )


def vectorize_contour(
    contour_pts: np.ndarray,
    scale: ScaleResult,
    max_error_mm: float = 0.5,
) -> Contour:
    """Converts a raw pixel boundary into a closed CAD :class:`Contour` in mm (Y-up, origin at bbox min)."""
    return vectorize_contour_detailed(contour_pts, scale, max_error_mm=max_error_mm).contour


# ---------------------------------------------------------------------------
# 6. Verification helpers
# ---------------------------------------------------------------------------


def _sample_contour(contour: Contour, samples_per_curve: int = 64) -> np.ndarray:
    out: List[np.ndarray] = []
    for seg in contour.segments:
        if seg.type == "cubic_bezier" and seg.cp1 is not None and seg.cp2 is not None:
            ctrl = np.array([seg.start.to_tuple(), seg.cp1.to_tuple(), seg.cp2.to_tuple(), seg.end.to_tuple()])
            out.append(_bez_eval(ctrl, np.linspace(0.0, 1.0, samples_per_curve)))
        else:
            a = np.array(seg.start.to_tuple())
            b = np.array(seg.end.to_tuple())
            L = float(np.linalg.norm(b - a))
            m = max(2, int(math.ceil(L / 0.25)) + 1)
            t = np.linspace(0.0, 1.0, m)[:, None]
            out.append(a + (b - a) * t)
    return np.vstack(out) if out else np.zeros((0, 2))


def max_deviation_to_contour(points: np.ndarray, contour: Contour, samples_per_curve: int = 96) -> float:
    """Maximum distance from each point to the densely sampled contour (point → polyline)."""
    pts = _as_points(points)
    poly = _sample_contour(contour, samples_per_curve)
    if len(poly) < 2:
        return float("inf")
    a = poly[:-1]
    ab = poly[1:] - a
    ab2 = np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-18)
    best = np.full(len(pts), np.inf)
    chunk = 512
    for s in range(0, len(pts), chunk):
        p = pts[s : s + chunk][:, None, :]
        t = np.clip(np.einsum("ijk,jk->ij", p - a[None], ab) / ab2[None], 0.0, 1.0)
        proj = a[None] + t[..., None] * ab[None]
        d = np.linalg.norm(p - proj, axis=2).min(axis=1)
        best[s : s + chunk] = d
    return float(best.max())
