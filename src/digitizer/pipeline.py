"""
End-to-End Pipeline & Visual Diagnostic Suite for Stage 2 Pattern Semantic Digitizer (Milestone 4).

Coordinates:
1. Scale Recovery (auto_detect_scale, dimension matching, or 2-point reference).
2. Raster Segmentation (segment_pattern_pieces_detailed).
3. Schneider Bézier Vectorization (vectorize_contour_detailed).
4. Semantic Feature Classification (classify_seam_mode, detect_fold_edge, apply_auto_unfold,
   extract_internal_features, detect_notches).
5. Visual Diagnostics Generation:
   - Unified diagnostic contact sheet (diagnostic_contact_sheet.png).
   - Optional export of separate individual PNG overlays on demand (separate_png=True):
     * 01_segmentation_masks.png
     * 02_corners_detected.png
     * 03_bezier_curves.png
     * 04_tailoring_semantics.png
     * 05_cad_calibrated_overlay.png
6. Exporters:
   - DXF-AAMA AutoCAD R12 (Layers 1, 2, 4, 5, 7, 8).
   - Clo3D 1:1 Vector PDF (solid cut, dashed sewing).
   - A4 Multi-Page Tiler (Hybrid & Poster printables with 50x50 mm calibration square).
   - PatternProject JSON schema serialization.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

import cv2
import numpy as np

from src.core.contract import (
    LINE_TYPE_CUT,
    LINE_TYPE_DART,
    LINE_TYPE_FOLD,
    LINE_TYPE_INTERNAL_CUT,
    LINE_TYPE_SEAM,
    LINE_TYPE_SLIT,
    Contour,
    InternalLine,
    Notch,
    PathSegment,
    PatternPiece,
    PatternProject,
    Point2D,
)
from src.core.geometry import (
    contour_to_points,
    contour_to_shapely_polygon,
    get_contour_bounds,
    get_contour_dimensions_mm,
    unfold_piece_on_fold,
)
from src.digitizer.roi import (
    ROIZone,
    apply_roi_to_image,
    draw_zones_overlay,
    parse_zones,
    rasterize_zones,
)
from src.digitizer.scale_recovery import (
    ScaleResult,
    auto_detect_scale,
    calibrate_piece_by_dimensions,
    compute_anisotropic_scale,
)
from src.digitizer.semantic_classifier import (
    SeamMode,
    apply_auto_unfold,
    classify_seam_mode,
    compute_normal_at_point,
    detect_fold_edge,
    detect_notches,
    extract_internal_features,
)
from src.digitizer.vectorizer import (
    PixelToMMTransform,
    SegmentedPiece,
    VectorizationResult,
    load_image,
    segment_pattern_pieces_detailed,
    vectorize_contour_detailed,
)
from src.exporters.a4_tiler_pdf import export_a4_pdf
from src.exporters.dxf_exporter import export_clo3d_dxf
from src.exporters.pdf_exporter import (
    export_clo3d_vector_pdf_1to1,
    export_technical_sheet_pdf_1to1,
)
from src.nesting.a4_nester import nest_hybrid_mode, nest_poster_mode

# Reference specifications for Gorro Trapper pieces (matched by aspect ratio)
GORRO_TRAPPER_SPECS = [
    {
        "id": "fuelle_superior",
        "name": "Fuelle superior",
        "width_cm": 13.0,
        "height_cm": 35.0,
        "quantity": 1,
        "aspect_ratio": 13.0 / 35.0,  # ~0.371
        "fabric_note": "En tela exterior y forro (x1)",
        "fold_hint": None,
    },
    {
        "id": "cubre_boca",
        "name": "Cubre boca",
        "width_cm": 28.0,
        "height_cm": 14.0,
        "quantity": 1,
        "aspect_ratio": 28.0 / 14.0,  # 2.000
        "fabric_note": "En tela exterior (x1, elastizar)",
        "fold_hint": None,
    },
    {
        "id": "orejera_compuesta",
        "name": "Orejera Compuesta",
        "width_cm": 25.0,
        "height_cm": 19.5,
        "quantity": 2,
        "aspect_ratio": 25.0 / 19.5,  # ~1.282
        "fabric_note": "En tela exterior y forro (x2)",
        "fold_hint": None,
    },
    {
        "id": "lateral",
        "name": "Lateral",
        "width_cm": 10.0,
        "height_cm": 20.0,
        "quantity": 2,
        "aspect_ratio": 10.0 / 20.0,  # 0.500
        "fabric_note": "En tela exterior y forro (x2)",
        "fold_hint": None,
    },
    {
        "id": "frente",
        "name": "Frente",
        "width_cm": 12.5,
        "height_cm": 11.0,
        "quantity": 1,
        "aspect_ratio": 12.5 / 11.0,  # ~1.136
        "fabric_note": "En tela exterior y forro (x1, doblez)",
        "fold_hint": "bottom",
    },
    {
        "id": "visera",
        "name": "Visera",
        "width_cm": 20.0,
        "height_cm": 11.0,
        "quantity": 2,
        "aspect_ratio": 20.0 / 11.0,  # ~1.818
        "fabric_note": "En tela exterior (x2)",
        "fold_hint": None,
    },
]


SegmentationStyle = Literal["auto", "color", "line_art"]
ReferenceSpecsMode = Literal["auto", "gorro_trapper", "none"]

_AUTO_SPEC_TOLERANCE = 0.12
_AUTO_SPEC_MIN_DISTINCT = 5


def should_use_reference_specs(
    mode: ReferenceSpecsMode,
    image_path: Union[str, Path],
    bboxes_px: Sequence[Tuple[int, int, int, int]] = (),
) -> bool:
    """
    Decides whether the Gorro Trapper reference specs (names, cm dimensions, quantities) may be
    applied to the detected pieces, so that unrelated patterns keep their own measured geometry.

    In 'auto' mode the specs are used when the file name says 'gorro'/'trapper', or when the detected
    pieces genuinely look like that sheet (at least 5 of its 6 reference pieces matched by a tight
    aspect-ratio tolerance), which also covers renamed copies of the sample image.
    """
    if mode == "gorro_trapper":
        return True
    if mode == "none":
        return False
    stem = Path(image_path).stem.lower()
    if "gorro" in stem or "trapper" in stem:
        return True
    matched = {
        s["id"]
        for bbox in bboxes_px
        if (s := match_piece_spec(bbox, GORRO_TRAPPER_SPECS, tolerance=_AUTO_SPEC_TOLERANCE)) is not None
    }
    return len(matched) >= _AUTO_SPEC_MIN_DISTINCT


def match_piece_spec(
    bbox_px: Tuple[int, int, int, int],
    specs: Sequence[Dict[str, Any]],
    tolerance: float = 0.25,
) -> Optional[Dict[str, Any]]:
    """Matches a detected bounding box to a reference piece specification by aspect ratio (W/H)."""
    w = bbox_px[2]
    h = bbox_px[3]
    if h <= 0:
        return None
    ar = w / float(h)
    best_spec = None
    best_diff = float("inf")
    for s in specs:
        diff = abs(ar - s["aspect_ratio"])
        if diff < tolerance and diff < best_diff:
            best_diff = diff
            best_spec = s
    return best_spec


# ===========================================================================
# Visual Diagnostic Generator
# ===========================================================================


def generate_diagnostic_overlays(
    image: np.ndarray,
    segmented_pieces: List[SegmentedPiece],
    vectorization_results: List[VectorizationResult],
    pieces: List[PatternPiece],
    scale: ScaleResult,
    output_dir: Path,
    separate_png: bool = False,
) -> Tuple[Path, List[Path]]:
    """
    Renders visual diagnostic overlays showing each stage of the perception pipeline:
    1. 01_segmentation_masks: Piece masks, bounding boxes, pixel areas, reading order.
    2. 02_corners_detected: Discrete turning-angle corners and smooth breakpoints.
    3. 03_bezier_curves: Schneider Bézier control polygons, line/curve segments, C0 nodes.
    4. 04_tailoring_semantics: Cut lines, sewing lines, fold edges, darts/slits, notches.
    5. 05_cad_calibrated_overlay: True 1:1 CAD millimeter layout with dimension callouts and ruler.

    By default produces a unified publication-quality contact sheet (diagnostic_contact_sheet.png).
    When separate_png=True, also exports each panel as a standalone high-resolution PNG.

    Returns:
        (contact_sheet_path, list_of_separate_png_paths)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    img_h, img_w = image.shape[:2]

    # Panel 1: Segmentation Masks
    p1 = image.copy()
    overlay1 = p1.copy()
    colors = [
        (255, 120, 0), (0, 200, 255), (0, 140, 255), (50, 220, 50),
        (220, 50, 220), (220, 180, 0), (140, 100, 255), (0, 180, 140),
    ]
    for idx, sp in enumerate(segmented_pieces):
        c_color = colors[idx % len(colors)]
        cv2.drawContours(overlay1, [sp.contour_px.astype(np.int32)], -1, c_color, thickness=-1)
        cv2.drawContours(p1, [sp.contour_px.astype(np.int32)], -1, (0, 255, 0), thickness=2)
        bx, by, bw, bh = sp.bbox_px
        cv2.rectangle(p1, (bx, by), (bx + bw, by + bh), (200, 200, 200), thickness=1)
        label = f"#{idx+1} ({bw}x{bh}px, {int(sp.area_px):,}px)"
        cv2.putText(p1, label, (bx, max(15, by - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2)
        cv2.putText(p1, label, (bx, max(15, by - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2.addWeighted(overlay1, 0.35, p1, 0.65, 0, p1)
    cv2.putText(p1, "STAGE 2.1: RASTER SEGMENTATION & PIECE MASKS", (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 180), 2)

    # Panel 2: Corner Detection
    p2 = image.copy()
    for idx, vr in enumerate(vectorization_results):
        # Draw boundary line
        b_pts = vr.boundary_px.astype(np.int32)
        cv2.polylines(p2, [b_pts], isClosed=True, color=(180, 180, 180), thickness=1, lineType=cv2.LINE_AA)
        # Draw smooth breakpoints in cyan
        for bp in vr.breakpoint_indices:
            if bp not in vr.corner_indices:
                pt = vr.boundary_px[bp].astype(np.int32)
                cv2.circle(p2, (pt[0], pt[1]), 3, (255, 200, 0), -1)
        # Draw true corners in magenta diamonds
        for ci in vr.corner_indices:
            pt = vr.boundary_px[ci].astype(np.int32)
            cv2.drawMarker(p2, (pt[0], pt[1]), (255, 0, 255), cv2.MARKER_DIAMOND, 9, 2)
    cv2.putText(p2, "STAGE 2.2: SHARP CORNER & BREAKPOINT DETECTION", (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (180, 0, 180), 2)

    # Panel 3: Schneider Bézier Curve Fitting
    p3 = np.full((img_h, img_w, 3), 250, dtype=np.uint8)
    for idx, vr in enumerate(vectorization_results):
        tf = vr.transform
        for seg in vr.contour.segments:
            # Map CAD mm back to pixel space for visual overlay
            p_start = tf.inverse(np.array([[seg.start.x, seg.start.y]]))[0].astype(np.int32)
            p_end = tf.inverse(np.array([[seg.end.x, seg.end.y]]))[0].astype(np.int32)

            if seg.type == "line":
                cv2.line(p3, tuple(p_start), tuple(p_end), (200, 50, 0), 2, cv2.LINE_AA)
            else:
                # Cubic Bézier: evaluate dense polyline
                p_cp1 = tf.inverse(np.array([[seg.cp1.x, seg.cp1.y]]))[0].astype(np.int32)
                p_cp2 = tf.inverse(np.array([[seg.cp2.x, seg.cp2.y]]))[0].astype(np.int32)
                # Control polygon in light gray
                cv2.line(p3, tuple(p_start), tuple(p_cp1), (180, 180, 180), 1)
                cv2.line(p3, tuple(p_cp2), tuple(p_end), (180, 180, 180), 1)
                cv2.circle(p3, tuple(p_cp1), 2, (0, 180, 255), -1)
                cv2.circle(p3, tuple(p_cp2), 2, (0, 180, 255), -1)

                t = np.linspace(0.0, 1.0, 32)[:, None]
                c_pts = (
                    (1 - t) ** 3 * p_start
                    + 3 * (1 - t) ** 2 * t * p_cp1
                    + 3 * (1 - t) * t ** 2 * p_cp2
                    + t ** 3 * p_end
                ).astype(np.int32)
                cv2.polylines(p3, [c_pts], isClosed=False, color=(0, 140, 0), thickness=2, lineType=cv2.LINE_AA)

            # Node endpoints
            cv2.circle(p3, tuple(p_start), 3, (0, 0, 220), -1)
    cv2.putText(p3, "STAGE 2.3: SCHNEIDER CUBIC BEZIER & CAD CURVES", (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 120, 0), 2)

    # Panel 4: Tailoring Semantics (Fold, Seams, Darts, Notches)
    p4 = image.copy()
    for idx, (piece, vr) in enumerate(zip(pieces, vectorization_results)):
        tf = vr.transform
        # Cut line in solid green/blue
        if piece.has_cut_contour:
            cut_pts = [tf.inverse(np.array([[pt[0], pt[1]]]))[0].astype(np.int32)
                       for pt in contour_to_points(piece.cut_contour, steps_per_curve=24)]
            if len(cut_pts) > 2:
                cv2.polylines(p4, [np.array(cut_pts)], isClosed=True, color=(0, 180, 0), thickness=2, lineType=cv2.LINE_AA)

        # Sewing line in blue
        net_pts = [tf.inverse(np.array([[pt[0], pt[1]]]))[0].astype(np.int32)
                   for pt in contour_to_points(piece.outer_contour, steps_per_curve=24)]
        if len(net_pts) > 2:
            cv2.polylines(p4, [np.array(net_pts)], isClosed=True, color=(220, 80, 0), thickness=1, lineType=cv2.LINE_AA)

        # Fold segment in bold magenta
        for seg in piece.outer_contour.segments:
            if seg.edge_role == "fold":
                f_s = tf.inverse(np.array([[seg.start.x, seg.start.y]]))[0].astype(np.int32)
                f_e = tf.inverse(np.array([[seg.end.x, seg.end.y]]))[0].astype(np.int32)
                cv2.line(p4, tuple(f_s), tuple(f_e), (255, 0, 200), 4, cv2.LINE_AA)
                mid_x = (f_s[0] + f_e[0]) // 2
                mid_y = (f_s[1] + f_e[1]) // 2
                cv2.putText(p4, "FOLD (0mm)", (mid_x - 40, mid_y - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 200), 2)

        # Internal lines (darts / slits)
        for il in piece.internal_lines:
            i_color = (0, 0, 255) if il.line_type == "dart" else (255, 100, 0)
            i_pts = [tf.inverse(np.array([[pt[0], pt[1]]]))[0].astype(np.int32)
                     for pt in contour_to_points(il.contour, steps_per_curve=16)]
            if len(i_pts) >= 2:
                cv2.polylines(p4, [np.array(i_pts)], isClosed=False, color=i_color, thickness=2, lineType=cv2.LINE_AA)

        # Notches with outward normal vectors
        for notch in piece.notches:
            n_pos = tf.inverse(np.array([[notch.position.x, notch.position.y]]))[0].astype(np.int32)
            cv2.circle(p4, tuple(n_pos), 4, (0, 255, 255), -1)
            if notch.direction:
                n_tip_mm = np.array([[
                    notch.position.x + notch.direction.x * notch.length_mm,
                    notch.position.y + notch.direction.y * notch.length_mm
                ]])
                n_tip = tf.inverse(n_tip_mm)[0].astype(np.int32)
                cv2.arrowedLine(p4, tuple(n_pos), tuple(n_tip), (0, 255, 255), 2, tipLength=0.3)
    cv2.putText(p4, "STAGE 2.4: TAILORING SEMANTICS (SEAMS, FOLDS, NOTCHES)", (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 100), 2)

    # Panel 5: Calibrated 1:1 CAD Sheet
    p5 = np.full((img_h, img_w, 3), 255, dtype=np.uint8)
    # Light millimeter grid
    for gx in range(0, img_w, 50):
        cv2.line(p5, (gx, 0), (gx, img_h), (240, 240, 240), 1)
    for gy in range(0, img_h, 50):
        cv2.line(p5, (0, gy), (img_w, gy), (240, 240, 240), 1)

    # Draw 50x50 mm calibration square reference box in corner
    sq_px_w = int(round(50.0 / scale.mm_per_px_x))
    sq_px_h = int(round(50.0 / scale.mm_per_px_y))
    cv2.rectangle(p5, (30, img_h - 30 - sq_px_h), (30 + sq_px_w, img_h - 30), (50, 50, 50), 2)
    cv2.putText(p5, "5.0 x 5.0 cm (50x50 mm)", (32, img_h - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)

    for idx, (piece, vr) in enumerate(zip(pieces, vectorization_results)):
        tf = vr.transform
        net_pts = [tf.inverse(np.array([[pt[0], pt[1]]]))[0].astype(np.int32)
                   for pt in contour_to_points(piece.outer_contour, steps_per_curve=24)]
        if len(net_pts) > 2:
            cv2.polylines(p5, [np.array(net_pts)], isClosed=True, color=(50, 50, 50), thickness=2, lineType=cv2.LINE_AA)

        w_mm, h_mm = get_contour_dimensions_mm(piece.outer_contour)
        c_x = int(vr.boundary_px[:, 0].mean())
        c_y = int(vr.boundary_px[:, 1].mean())
        dim_text = f"{piece.name} [{w_mm/10.0:.1f} x {h_mm/10.0:.1f} cm] (x{piece.quantity})"
        cv2.putText(p5, dim_text, (c_x - 60, c_y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 150), 1)

    cv2.putText(p5, f"STAGE 2.5: CALIBRATED 1:1 CAD SHEET (SCALE: {scale.mm_per_px:.4f} mm/px)",
                (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)

    # Build Unified Contact Sheet (Grid: 2 rows x 3 columns)
    panel_w = 640
    panel_h = int(round(img_h * (panel_w / float(img_w))))
    p1_s = cv2.resize(p1, (panel_w, panel_h))
    p2_s = cv2.resize(p2, (panel_w, panel_h))
    p3_s = cv2.resize(p3, (panel_w, panel_h))
    p4_s = cv2.resize(p4, (panel_w, panel_h))
    p5_s = cv2.resize(p5, (panel_w, panel_h))

    # Header canvas
    header_h = 70
    contact_w = panel_w * 3
    contact_h = header_h + panel_h * 2
    contact = np.full((contact_h, contact_w, 3), 30, dtype=np.uint8)

    # Title header
    cv2.putText(contact, "IMAGE-TO-PATTERN CAD — PATTERN SEMANTIC DIGITIZER (STAGE 2 DIAGNOSTIC SHEET)",
                (20, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2)
    meta = f"Calibrated scale: {scale.mm_per_px:.4f} mm/px ({scale.calibration_method}) | Pieces: {len(pieces)} | Unit: mm"
    cv2.putText(contact, meta, (20, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (180, 220, 255), 1)

    # Row 1
    contact[header_h:header_h + panel_h, 0:panel_w] = p1_s
    contact[header_h:header_h + panel_h, panel_w:panel_w * 2] = p2_s
    contact[header_h:header_h + panel_h, panel_w * 2:panel_w * 3] = p3_s

    # Row 2
    contact[header_h + panel_h:contact_h, 0:panel_w] = p4_s
    contact[header_h + panel_h:contact_h, panel_w:panel_w * 2] = p5_s

    # Panel 6 (Summary Card in Row 2, Col 3)
    p6_s = np.full((panel_h, panel_w, 3), 245, dtype=np.uint8)
    cv2.putText(p6_s, "PROJECT SUMMARY & TAILORING CONTRACT", (20, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    y_off = 65
    for idx, p in enumerate(pieces, start=1):
        w_mm, h_mm = get_contour_dimensions_mm(p.outer_contour)
        f_tag = "[FOLD]" if p.has_fold else ""
        text = f"{idx}. {p.name:<18} : {w_mm:5.1f} x {h_mm:5.1f} mm {f_tag} (x{p.quantity})"
        cv2.putText(p6_s, text, (20, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (30, 30, 30), 1)
        y_off += 25
    contact[header_h + panel_h:contact_h, panel_w * 2:panel_w * 3] = p6_s

    contact_sheet_path = output_dir / "diagnostic_contact_sheet.png"
    cv2.imwrite(str(contact_sheet_path), contact)

    separate_paths: List[Path] = []
    if separate_png:
        # Save individual panels as standalone high-resolution files
        paths_and_images = [
            (output_dir / "01_segmentation_masks.png", p1),
            (output_dir / "02_corners_detected.png", p2),
            (output_dir / "03_bezier_curves.png", p3),
            (output_dir / "04_tailoring_semantics.png", p4),
            (output_dir / "05_cad_calibrated_overlay.png", p5),
        ]
        for path, p_img in paths_and_images:
            cv2.imwrite(str(path), p_img)
            separate_paths.append(path)

    return contact_sheet_path, separate_paths


# ===========================================================================
# Digitizer Pipeline Engine
# ===========================================================================


class DigitizerPipeline:
    """
    End-to-end Pattern Semantic Digitizer pipeline:
    Raster Image -> Scale Recovery -> Segmentation -> Schneider Bézier CAD ->
    Tailoring Semantics -> Diagnostic Visualizations -> Clo3D DXF-AAMA / 1:1 PDF / A4 Tiling.
    """

    def __init__(
        self,
        scale_method: Literal[
            "auto", "dimension_anisotropic", "two_point_reference", "calibration_square", "dpi_metadata", "fallback_default"
        ] = "auto",
        seam_mode: SeamMode = "net_only",
        seam_allowance_mm: float = 10.0,
        auto_unfold: bool = False,
        output_dir: Optional[Union[str, Path]] = None,
        separate_png: bool = False,
        export_clo3d: bool = True,
        export_a4: bool = True,
        max_bezier_error_mm: float = 0.5,
        segmentation_mode: SegmentationStyle = "auto",
        reference_specs: ReferenceSpecsMode = "auto",
    ):
        self.segmentation_mode = segmentation_mode
        self.reference_specs = reference_specs
        self.scale_method = scale_method
        self.seam_mode = seam_mode
        self.seam_allowance_mm = max(0.0, float(seam_allowance_mm))
        self.auto_unfold = auto_unfold
        self.output_dir = Path(output_dir) if output_dir else Path("output/stage2_digitizer")
        self.separate_png = separate_png
        self.export_clo3d = export_clo3d
        self.export_a4 = export_a4
        self.max_bezier_error_mm = max_bezier_error_mm

    def process_image(
        self,
        image_path: Union[str, Path],
        project_name: Optional[str] = None,
        declared_dimensions: Optional[Dict[str, Tuple[float, float]]] = None,
        zones: Optional[Sequence[Union[ROIZone, Dict[str, Any]]]] = None,
    ) -> PatternProject:
        """
        Executes the full perception, scale, vectorization, and tailoring semantics pipeline.

        Args:
            zones: Optional user-drawn ROI zones (normalized polygons). 'include' zones restrict the
                search area for pattern pieces, 'exclude' zones are ignored (see src/digitizer/roi.py).
        """
        path = Path(image_path)
        img = load_image(path)
        proj_name = project_name or path.stem

        # 1. Scale Recovery (runs on the full image so a calibration square outside the ROI still works)
        scale = auto_detect_scale(img, image_path=path)

        # 2. ROI zones + Raster Segmentation
        zone_list = parse_zones(zones)
        seg_img = img
        diag_img = img
        if zone_list:
            allowed = rasterize_zones(zone_list, img.shape)
            seg_img = apply_roi_to_image(img, allowed)
            diag_img = draw_zones_overlay(img, zone_list)

        if self.segmentation_mode == "color":
            segmented_pieces = segment_pattern_pieces_detailed(
                seg_img, min_area_px=1000.0, hsv_tan_filter=True, auto_fallback=False
            )
        elif self.segmentation_mode == "line_art":
            segmented_pieces = segment_pattern_pieces_detailed(
                seg_img, min_area_px=1000.0, hsv_tan_filter=False, auto_fallback=False
            )
        else:
            segmented_pieces = segment_pattern_pieces_detailed(seg_img, min_area_px=1000.0)
        if not segmented_pieces:
            raise ValueError(f"No pattern pieces could be segmented from {path}.")

        use_specs = should_use_reference_specs(
            self.reference_specs, path, [sp.bbox_px for sp in segmented_pieces]
        )

        # 3. Vectorization into CAD contours
        vectorization_results: List[VectorizationResult] = []
        raw_pieces: List[PatternPiece] = []

        for idx, sp in enumerate(segmented_pieces):
            # Vectorize boundary using Schneider cubic Bézier fitter
            v_res = vectorize_contour_detailed(
                sp.contour_px,
                scale=scale,
                max_error_mm=self.max_bezier_error_mm,
                y_up=True,
                normalize_origin=True,
            )
            vectorization_results.append(v_res)

            # Match against known specs if applicable
            spec = match_piece_spec(sp.bbox_px, GORRO_TRAPPER_SPECS) if use_specs else None
            if spec:
                p_id = spec["id"]
                p_name = spec["name"]
                t_w_cm = spec["width_cm"]
                t_h_cm = spec["height_cm"]
                qty = spec["quantity"]
                fab_note = spec["fabric_note"]
                fold_hint = spec.get("fold_hint")
            else:
                p_id = f"piece_{idx+1}"
                p_name = f"Pattern Piece {idx+1}"
                w_mm, h_mm = get_contour_dimensions_mm(v_res.contour)
                t_w_cm = w_mm / 10.0
                t_h_cm = h_mm / 10.0
                qty = 1
                fab_note = ""
                fold_hint = "auto"

            piece = PatternPiece(
                id=p_id,
                name=p_name,
                quantity=qty,
                target_width_cm=t_w_cm,
                target_height_cm=t_h_cm,
                outer_contour=v_res.contour,
                fabric_note=fab_note,
            )

            # 4. Semantic Feature Classification (prior to anisotropic scaling)
            # Fold edge detection
            piece = detect_fold_edge(piece, text_hint=fab_note, edge_hint=(fold_hint or "auto"))

            # Internal features (slits, darts, division lines)
            piece = extract_internal_features(
                image=seg_img,
                piece=piece,
                scale=scale,
                transform=v_res.transform,
                piece_mask=sp.mask,
            )

            # 5. Anisotropic Calibration if target dimensions exist (scales outer contour + internal lines)
            if declared_dimensions and p_id in declared_dimensions:
                dw, dh = declared_dimensions[p_id]
                piece = calibrate_piece_by_dimensions(piece, dw, dh)
            elif spec:
                piece = calibrate_piece_by_dimensions(piece, spec["width_cm"], spec["height_cm"])

            # Seam allowance mode handling
            piece = classify_seam_mode(
                piece=piece,
                mode=self.seam_mode,
                seam_allowance_mm=self.seam_allowance_mm,
            )

            # Configurable auto-unfold
            piece = apply_auto_unfold(piece, auto_unfold=self.auto_unfold)

            raw_pieces.append(piece)

        # Build PatternProject
        project = PatternProject(
            project_name=proj_name,
            source_image=str(path),
            units="mm",
            has_seam_allowance=(self.seam_allowance_mm > 0.0),
            pieces=raw_pieces,
        )

        # 6. Generate Diagnostic Overlays
        self.output_dir.mkdir(parents=True, exist_ok=True)
        generate_diagnostic_overlays(
            image=diag_img,
            segmented_pieces=segmented_pieces,
            vectorization_results=vectorization_results,
            pieces=raw_pieces,
            scale=scale,
            output_dir=self.output_dir,
            separate_png=self.separate_png,
        )

        # Save JSON schema
        json_path = self.output_dir / f"{proj_name}_digitized_project.json"
        project.save_json(json_path)

        # 7. Exporters Integration
        if self.export_clo3d:
            # DXF-AAMA (AutoCAD R12) with Layers 1, 2, 4, 5, 7, 8
            dxf_path = self.output_dir / f"{proj_name}_clo3d_mm.dxf"
            export_clo3d_dxf(project, dxf_path, unit="mm", expand_quantities=False)

            # 1:1 Vector PDF for Clo3D
            pdf_path = self.output_dir / f"{proj_name}_clo3d_import_1to1.pdf"
            export_clo3d_vector_pdf_1to1(project, pdf_path, expand_quantities=False)

            # Technical sheet 1:1
            tech_path = self.output_dir / f"{proj_name}_technical_sheet_1to1.pdf"
            export_technical_sheet_pdf_1to1(project, tech_path, expand_quantities=False)

        if self.export_a4:
            # A4 multi-page printable PDF (Smart Hybrid nesting)
            hybrid_nesting = nest_hybrid_mode(
                project, page_w_mm=210.0, page_h_mm=297.0, margin_mm=10.0, overlap_mm=15.0
            )
            a4_pdf_path = self.output_dir / f"{proj_name}_a4_print_hybrid.pdf"
            export_a4_pdf(
                hybrid_nesting,
                a4_pdf_path,
                render_png_previews=False,
            )

        return project
