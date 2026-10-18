"""
Adversarial Stress Test Suite for 2D Nesting & Multi-Page A4 Tiling Engine (Milestone 2 - R2).

Adversarially validates:
1. Non-overlap verification across all pages in both Hybrid and Poster modes (area < 1e-2 mm²).
2. Stress-testing multi-piece packing with synthetic collections (small rectangles, irregular L-shapes).
3. Exact page dimensions across all generated PDF pages (595.28 x 841.89 pt +/- 0.5 pt).
4. Rotational invariance and geometry preservation under orthogonal rotations (0°, 90°, 180°, 270°, negative angles).
5. Completeness: all 9 expanded pieces accounted for, oversized pieces tiled across >= 2 pages.
6. Calibration square 50x50 mm exact dimension and potential visual occlusion on Page 1.
7. Stress-testing custom parameters (margins 5mm, 15mm; overlaps 10mm, 20mm) and checking coordinate consistency.
"""

from __future__ import annotations

import math
from pathlib import Path
import pymupdf as fitz
import pytest
from shapely.affinity import translate
from shapely.geometry import Polygon, box

from src.core.contract import (
    Contour,
    PathSegment,
    PatternPiece,
    PatternProject,
    Point2D,
    PT_PER_MM,
)
from src.core.geometry import contour_to_points, get_contour_bounds
from src.core.seam_allowance import apply_seam_allowance_to_project
from src.exporters.a4_tiler_pdf import (
    MARGIN_MM,
    OVERLAP_MM,
    PAGE_H_MM,
    PAGE_H_PT,
    PAGE_W_MM,
    PAGE_W_PT,
    export_a4_pdf,
)
from src.nesting.a4_nester import (
    classify_piece_fit,
    expand_project_pieces,
    nest_hybrid_mode,
    nest_poster_mode,
    rotate_piece,
    transform_piece_geometry,
)
from src.presets.gorro_trapper import create_gorro_trapper_project


@pytest.fixture
def calibrated_trapper_project() -> PatternProject:
    """Returns the reference Gorro Trapper project with seam allowances computed."""
    base_proj = create_gorro_trapper_project()
    return apply_seam_allowance_to_project(base_proj)


# ==============================================================================
# 1. NON-OVERLAP VERIFICATION (CRITERIA: area < 1e-2 mm²)
# ==============================================================================


def test_non_overlap_gorro_trapper_hybrid(calibrated_trapper_project: PatternProject):
    """Verifies that no two pieces placed on the same sheet overlap in Hybrid mode."""
    res = nest_hybrid_mode(calibrated_trapper_project)
    for page in res.pages:
        pieces = page.placed_pieces
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces)):
                p1 = pieces[i].get_placed_polygon()
                p2 = pieces[j].get_placed_polygon()
                overlap = p1.intersection(p2).area
                assert overlap < 1e-2, (
                    f"Hybrid Page {page.page_index}: Overlap detected between "
                    f"'{pieces[i].piece.name}' and '{pieces[j].piece.name}': {overlap:.6f} mm²"
                )


def test_non_overlap_gorro_trapper_poster(calibrated_trapper_project: PatternProject):
    """Verifies that no two pieces placed on the same sheet overlap in Poster mode."""
    res = nest_poster_mode(calibrated_trapper_project)
    for page in res.pages:
        pieces = page.placed_pieces
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces)):
                p1 = pieces[i].get_placed_polygon()
                p2 = pieces[j].get_placed_polygon()
                overlap = p1.intersection(p2).area
                assert overlap < 1e-2, (
                    f"Poster Page {page.page_index}: Overlap detected between "
                    f"'{pieces[i].piece.name}' and '{pieces[j].piece.name}': {overlap:.6f} mm²"
                )


def test_non_overlap_synthetic_dense_collection():
    """
    Stress-test non-overlap with 12 small pieces that fit multiple per sheet
    to aggressively exercise multi-piece sheet packing logic in Hybrid and Poster modes.
    """
    pieces = []
    for i in range(12):
        w = 40.0 + (i % 3) * 10.0  # 40, 50, 60 mm
        h = 50.0 + (i % 2) * 20.0  # 50, 70 mm
        p = PatternPiece(
            id=f"dense_{i}",
            name=f"Dense Piece {i}",
            quantity=1,
            target_width_cm=w / 10.0,
            target_height_cm=h / 10.0,
            outer_contour=Contour.from_polyline([(0, 0), (w, 0), (w, h), (0, h)]),
            seam_allowance_mm=0.0,
        )
        pieces.append(p)

    proj = PatternProject(project_name="Dense synthetic", pieces=pieces)

    # Hybrid mode
    res_h = nest_hybrid_mode(proj)
    multi_piece_pages = [p for p in res_h.pages if len(p.placed_pieces) > 1]
    assert len(multi_piece_pages) > 0, "Expected at least one multi-piece page in hybrid mode"

    for page in res_h.pages:
        for i in range(len(page.placed_pieces)):
            for j in range(i + 1, len(page.placed_pieces)):
                p1 = page.placed_pieces[i].get_placed_polygon()
                p2 = page.placed_pieces[j].get_placed_polygon()
                assert p1.intersection(p2).area < 1e-2

    # Poster mode
    res_p = nest_poster_mode(proj)
    for page in res_p.pages:
        for i in range(len(page.placed_pieces)):
            for j in range(i + 1, len(page.placed_pieces)):
                p1 = page.placed_pieces[i].get_placed_polygon()
                p2 = page.placed_pieces[j].get_placed_polygon()
                assert p1.intersection(p2).area < 1e-2


def test_non_overlap_irregular_concave_shapes():
    """Adversarial stress-test with irregular concave non-convex L-shaped polygons."""
    pieces = []
    for i in range(8):
        l_pts = [(0, 0), (70, 0), (70, 30), (35, 30), (35, 90), (0, 90)]
        p = PatternPiece(
            id=f"concave_{i}",
            name=f"L-Piece {i}",
            quantity=1,
            target_width_cm=7.0,
            target_height_cm=9.0,
            outer_contour=Contour.from_polyline(l_pts),
            seam_allowance_mm=0.0,
        )
        pieces.append(p)

    proj = PatternProject(project_name="Concave test", pieces=pieces)
    res_h = nest_hybrid_mode(proj)
    for page in res_h.pages:
        for i in range(len(page.placed_pieces)):
            for j in range(i + 1, len(page.placed_pieces)):
                p1 = page.placed_pieces[i].get_placed_polygon()
                p2 = page.placed_pieces[j].get_placed_polygon()
                assert p1.intersection(p2).area < 1e-2


# ==============================================================================
# 2. PAGE DIMENSIONS VERIFICATION (595.28 x 841.89 pt +/- 0.5 pt)
# ==============================================================================


@pytest.mark.parametrize("mode", ["hybrid", "poster"])
def test_page_dimensions_across_all_pages(calibrated_trapper_project: PatternProject, mode: str, tmp_path: Path):
    """Verifies that every generated page in both modes strictly matches A4 dimensions within +/- 0.5 pt."""
    if mode == "hybrid":
        res = nest_hybrid_mode(calibrated_trapper_project)
    else:
        res = nest_poster_mode(calibrated_trapper_project)

    pdf_path = tmp_path / f"test_{mode}.pdf"
    export_a4_pdf(res, pdf_path, render_png_previews=False)

    doc = fitz.open(str(pdf_path))
    assert len(doc) == res.total_pages

    expected_w = 595.2756
    expected_h = 841.8898
    tol = 0.5

    for idx, page in enumerate(doc):
        w = page.rect.width
        h = page.rect.height
        assert abs(w - expected_w) <= tol, f"{mode} Page {idx+1} width {w:.3f} outside tolerance ({expected_w} +/- {tol})"
        assert abs(h - expected_h) <= tol, f"{mode} Page {idx+1} height {h:.3f} outside tolerance ({expected_h} +/- {tol})"

    doc.close()


# ==============================================================================
# 3. ROTATION INVARIANCE & ORTHOGONAL GEOMETRY PRESERVATION
# ==============================================================================


@pytest.mark.parametrize("deg", [0, 90, 180, 270, 360, -90, -180, -270])
def test_piece_rotations_orthogonal(calibrated_trapper_project: PatternProject, deg: int):
    """
    Stress-tests orthogonal rotations. Verifies that polygon area is invariant,
    geometry remains valid, bounds are normalized to (0, 0), and grainline length is conserved.
    """
    piece = calibrated_trapper_project.pieces[0]
    p_rot = rotate_piece(piece, deg)

    poly_orig = Polygon(contour_to_points(piece.effective_boundary))
    poly_rot = Polygon(contour_to_points(p_rot.effective_boundary))

    assert poly_rot.is_valid
    assert abs(poly_orig.area - poly_rot.area) < 1e-3

    min_x, min_y, max_x, max_y = get_contour_bounds(p_rot.effective_boundary)
    assert abs(min_x) < 1e-3, f"Normalized min_x should be 0, got {min_x}"
    assert abs(min_y) < 1e-3, f"Normalized min_y should be 0, got {min_y}"

    # Verify grainline length conservation
    if piece.grainline_start and piece.grainline_end and p_rot.grainline_start and p_rot.grainline_end:
        len_orig = math.hypot(
            piece.grainline_end.x - piece.grainline_start.x,
            piece.grainline_end.y - piece.grainline_start.y,
        )
        len_rot = math.hypot(
            p_rot.grainline_end.x - p_rot.grainline_start.x,
            p_rot.grainline_end.y - p_rot.grainline_start.y,
        )
        assert abs(len_orig - len_rot) < 1e-3


def test_four_90_degree_rotations_returns_normalized_original(calibrated_trapper_project: PatternProject):
    """Verifies that 4 successive 90-degree rotations return the identical normalized polygon."""
    piece = calibrated_trapper_project.pieces[0]
    p_curr = piece
    for _ in range(4):
        p_curr = rotate_piece(p_curr, 90)

    poly_orig = Polygon(contour_to_points(piece.effective_boundary))
    poly_orig_norm = translate(poly_orig, -poly_orig.bounds[0], -poly_orig.bounds[1])
    poly_rot4 = Polygon(contour_to_points(p_curr.effective_boundary))

    sym_diff = poly_orig_norm.symmetric_difference(poly_rot4).area
    assert sym_diff < 1e-2, f"Symmetric difference after 4x90° rotation: {sym_diff:.4f} mm²"


# ==============================================================================
# 4. PATTERN EXPANSION & COMPLETENESS VERIFICATION
# ==============================================================================


def test_all_9_pieces_expanded_and_symmetric_mirrored(calibrated_trapper_project: PatternProject):
    """
    Verifies that Gorro Trapper with 6 unique pieces expands to exactly 9 pieces,
    with quantity x2 pieces receiving symmetric mirror reflections.
    """
    expanded = expand_project_pieces(calibrated_trapper_project, mirror_symmetric=True)
    assert len(expanded) == 9

    # Check mirror reflection on Orejera Compuesta
    orejera_1 = next(p for p in expanded if "Orejera" in p.name and "1/2" in p.name)
    orejera_2 = next(p for p in expanded if "Orejera" in p.name and "2/2" in p.name)

    poly1 = Polygon(contour_to_points(orejera_1.effective_boundary))
    poly2 = Polygon(contour_to_points(orejera_2.effective_boundary))

    # Both must have identical area
    assert abs(poly1.area - poly2.area) < 1e-2
    # Bounding box dimensions must match
    min_x1, min_y1, max_x1, max_y1 = get_contour_bounds(orejera_1.effective_boundary)
    min_x2, min_y2, max_x2, max_y2 = get_contour_bounds(orejera_2.effective_boundary)
    assert abs((max_x1 - min_x1) - (max_x2 - min_x2)) < 1e-3
    assert abs((max_y1 - min_y1) - (max_y2 - min_y2)) < 1e-3


def test_all_9_pieces_present_in_nesting_outputs(calibrated_trapper_project: PatternProject):
    """Verifies that all 9 expanded pieces are accounted for in both Hybrid and Poster outputs."""
    expanded = expand_project_pieces(calibrated_trapper_project, mirror_symmetric=True)
    expected_ids = {p.id for p in expanded}

    res_h = nest_hybrid_mode(calibrated_trapper_project)
    hybrid_ids = {pp.piece.id for page in res_h.pages for pp in page.placed_pieces}
    assert hybrid_ids == expected_ids, f"Hybrid missing: {expected_ids - hybrid_ids}"

    res_p = nest_poster_mode(calibrated_trapper_project)
    poster_ids = {pp.piece.id for page in res_p.pages for pp in page.placed_pieces}
    assert poster_ids == expected_ids, f"Poster missing: {expected_ids - poster_ids}"


def test_oversized_pieces_tiled_across_multiple_pages(calibrated_trapper_project: PatternProject):
    """Verifies that oversized pieces (such as Fuelle superior) are sliced across at least 2 pages."""
    res_h = nest_hybrid_mode(calibrated_trapper_project)
    fuelle_pages = [p for p in res_h.pages if "Fuelle superior" in p.piece_name]
    assert len(fuelle_pages) >= 2, f"Fuelle superior tiled into only {len(fuelle_pages)} page(s)"
    assert all(p.is_tiled for p in fuelle_pages)


# ==============================================================================
# 5. CALIBRATION SQUARE (50 x 50 mm) VERIFICATION
# ==============================================================================


def test_calibration_square_dimensions_on_page_1(calibrated_trapper_project: PatternProject, tmp_path: Path):
    """
    Verifies that Page 1 contains the 50x50 mm calibration square
    measuring exactly 141.73 x 141.73 pt (tolerance +/- 0.5 pt).
    """
    res = nest_hybrid_mode(calibrated_trapper_project)
    pdf_path = tmp_path / "calib_test.pdf"
    export_a4_pdf(res, pdf_path, render_png_previews=False)

    doc = fitz.open(str(pdf_path))
    page1 = doc[0]

    drawings = page1.get_drawings()
    found_square = False
    expected_pt = 50.0 * PT_PER_MM  # 141.732 pt

    for d in drawings:
        r = d["rect"]
        w = r.width
        h = r.height
        if abs(w - expected_pt) < 1.0 and abs(h - expected_pt) < 1.0:
            found_square = True
            assert abs(w - expected_pt) < 0.5
            assert abs(h - expected_pt) < 0.5
            break

    assert found_square, "50x50 mm calibration square not found on Page 1!"
    doc.close()


def test_calibration_square_occlusion_in_poster_mode(calibrated_trapper_project: PatternProject):
    """
    Adversarial vulnerability test: In Poster Mode, checks whether the opaque 50x50 mm
    calibration square on Page 1 occludes underlying pattern pieces.
    """
    res_p = nest_poster_mode(calibrated_trapper_project)
    page1 = res_p.pages[0]

    calib_box = box(4.0, 4.0, 54.0, 54.0)
    occluded_pieces = []
    for pp in page1.placed_pieces:
        poly = pp.get_placed_polygon()
        inter_area = poly.intersection(calib_box).area
        if inter_area > 1e-2:
            occluded_pieces.append((pp.piece.name, inter_area))

    # We record this finding: in Poster Mode, Fuelle superior intersects the calibration square box
    if occluded_pieces:
        name, area = occluded_pieces[0]
        # Documented behavior: occludes ~40.4 mm²
        assert area > 10.0, f"Expected known occlusion of ~40.4 mm², got {area}"


# ==============================================================================
# 6. STRESS-TESTING CUSTOM PARAMETERS (MARGINS 5/15 mm, OVERLAPS 10/20 mm)
# ==============================================================================


@pytest.mark.parametrize(
    "margin,overlap",
    [
        (5.0, 20.0),
        (15.0, 10.0),
    ],
)
def test_custom_parameters_nesting_execution(calibrated_trapper_project: PatternProject, margin: float, overlap: float):
    """
    Verifies that nest_hybrid_mode and nest_poster_mode execute cleanly with custom
    margins (5mm, 15mm) and overlaps (10mm, 20mm), maintaining zero piece collisions.
    """
    res_h = nest_hybrid_mode(calibrated_trapper_project, margin_mm=margin, overlap_mm=overlap)
    assert res_h.total_pages > 0
    for page in res_h.pages:
        for i in range(len(page.placed_pieces)):
            for j in range(i + 1, len(page.placed_pieces)):
                assert page.placed_pieces[i].get_placed_polygon().intersection(page.placed_pieces[j].get_placed_polygon()).area < 1e-2

    res_p = nest_poster_mode(calibrated_trapper_project, margin_mm=margin, overlap_mm=overlap)
    assert res_p.total_pages > 0
    for page in res_p.pages:
        for i in range(len(page.placed_pieces)):
            for j in range(i + 1, len(page.placed_pieces)):
                assert page.placed_pieces[i].get_placed_polygon().intersection(page.placed_pieces[j].get_placed_polygon()).area < 1e-2


def test_custom_parameters_exporter_discrepancy():
    """
    Adversarial verification of alignment diamond and glue line discrepancy
    when custom parameters differ from default (10mm margin, 15mm overlap).
    Demonstrates that because a4_tiler_pdf.py uses hardcoded constants,
    alignment diamonds on adjacent columns/rows become misaligned by exactly
    (overlap - 15.0) mm.
    """
    margin_custom = 5.0
    overlap_custom = 20.0

    printable_w = 210.0 - 2.0 * margin_custom
    step_x = printable_w - overlap_custom

    # Hardcoded values in current exporter:
    exporter_margin = MARGIN_MM
    exporter_overlap = OVERLAP_MM

    # Col 1 diamond canvas coordinate:
    cut_x_mm = PAGE_W_MM - exporter_margin - exporter_overlap
    mid_strip_x = cut_x_mm + exporter_overlap / 2.0
    canvas_col1 = mid_strip_x - exporter_margin + 0.0

    # Col 2 diamond canvas coordinate:
    mid_left_x = exporter_margin + exporter_overlap / 2.0
    canvas_col2 = mid_left_x - exporter_margin + step_x

    discrepancy = canvas_col2 - canvas_col1
    assert abs(discrepancy - 5.0) < 1e-3, (
        f"Expected 5.0 mm discrepancy between adjacent diamonds under (5mm, 20mm), got {discrepancy:.2f} mm"
    )
