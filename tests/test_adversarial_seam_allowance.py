"""
Adversarial Stress Test Suite for Seam Allowance Engine & Data Contract (Milestone 1).

Evaluates:
1. Extreme geometry: sharp acute triangles (< 30°, < 10°, < 1°), stars, concave polygons,
   narrow channels, serpentine corridors.
2. Miter limit clamping: verifying that JT_MITER with MiterLimit=2.0 clamps spike vertices.
3. Winding order: testing both CCW and CW polygons to ensure offsetting always dilates outward and never shrinks.
4. Large and zero offsets: 0 mm, 10 mm, 50 mm, 100 mm.
5. Zero-length and collinear edges: handling duplicate vertices, zero-length segments, collinear splits.
6. Data contract validation: schema constraints, edge roles, negative values, JSON roundtrip.
7. Gorro Trapper reference pattern verification.
"""

from __future__ import annotations

import math
from pathlib import Path
import pydantic
import pytest
from shapely.geometry import Polygon

from src.core.contract import (
    Contour,
    PathSegment,
    PatternPiece,
    PatternProject,
    Point2D,
)
from src.core.geometry import contour_to_points
from src.core.seam_allowance import (
    apply_seam_allowance_to_piece,
    apply_seam_allowance_to_project,
    apply_uniform_seam_allowance,
    apply_variable_seam_allowance,
)
from src.presets.gorro_trapper import create_gorro_trapper_project


def _get_polygon_from_contour(contour: Contour, steps_per_curve: int = 48) -> Polygon:
    pts = contour_to_points(contour, steps_per_curve=steps_per_curve)
    return Polygon(pts)


def _assert_valid_dilation(
    piece_orig: PatternPiece,
    piece_offset: PatternPiece,
    tol: float = 1e-2,
) -> None:
    """Verifies that the offset contour is valid, simple, and strictly contains the original contour."""
    poly_orig = _get_polygon_from_contour(piece_orig.outer_contour)
    poly_cut = _get_polygon_from_contour(piece_offset.cut_contour)

    assert poly_cut.is_valid, f"Offset polygon is not valid: {poly_cut}"
    assert poly_cut.area > poly_orig.area, f"Offset area ({poly_cut.area}) not strictly greater than original ({poly_orig.area})"

    # Outward dilation check: original polygon must be contained in cut polygon
    diff_area = poly_orig.difference(poly_cut).area
    assert diff_area < tol, f"Dilation failed: original not contained in offset contour! diff_area = {diff_area}"


# ==============================================================================
# 1. ACUTE TRIANGLES & MITER LIMIT CLAMPING (< 30°, < 10°, < 1°)
# ==============================================================================

@pytest.mark.parametrize("angle_deg", [25.0, 15.0, 5.0, 1.0])
def test_acute_triangles_miter_limit_clamping(angle_deg: float) -> None:
    """
    Evaluates acute triangles with apex angles < 30° down to 1°.
    Without miter limit clamping, an unconstrained miter at angle theta
    extends by d / sin(theta / 2).
    For theta = 5°, d = 10mm -> unconstrained extension ~ 229 mm!
    With miter_limit = 2.0, the maximum tip distance must be clamped.
    """
    h = 200.0
    half_angle_rad = math.radians(angle_deg / 2.0)
    w = 2.0 * h * math.tan(half_angle_rad)

    pts = [(0.0, 0.0), (w, 0.0), (w / 2.0, h)]
    piece = PatternPiece(
        id=f"acute_{int(angle_deg)}",
        name=f"Acute {angle_deg}deg",
        target_width_cm=max(w / 10.0, 0.1),
        target_height_cm=h / 10.0,
        outer_contour=Contour.from_polyline(pts, is_closed=True),
    )

    # 1. Uniform offset with miter limit 2.0
    offset_piece = apply_uniform_seam_allowance(piece, seam_mm=10.0, miter_limit=2.0)
    poly_cut = _get_polygon_from_contour(offset_piece.cut_contour)
    assert poly_cut.is_valid

    # Maximum tip extension check
    max_y = max(p[1] for p in poly_cut.exterior.coords)
    unconstrained_tip_y = h + 10.0 / math.sin(half_angle_rad)
    # The miter limit = 2.0 must clamp the tip significantly below unconstrained miter
    assert max_y < unconstrained_tip_y or angle_deg >= 25.0
    assert max_y <= h + 2.0 * 10.0 + 1.0, f"Miter tip exceeded miter limit clamp: max_y={max_y}"

    _assert_valid_dilation(piece, offset_piece)

    # 2. Variable offset: base fold (0mm), sides 10mm
    piece_var = piece.model_copy(deep=True)
    piece_var.outer_contour.segments[0].edge_role = "fold"
    offset_var = apply_variable_seam_allowance(piece_var, default_seam_mm=10.0, miter_limit=2.0)
    poly_var = _get_polygon_from_contour(offset_var.cut_contour)
    assert poly_var.is_valid
    _assert_valid_dilation(piece, offset_var)


# ==============================================================================
# 2. WINDING ORDER INVARIANCE: CCW vs CW POLYGONS
# ==============================================================================

def test_winding_order_ccw_and_cw_dilation() -> None:
    """
    Tests that both CCW and CW polygons expand outwards and never shrink inwards.
    """
    pts_ccw = [(0.0, 0.0), (100.0, 0.0), (100.0, 80.0), (0.0, 80.0)]
    pts_cw = [(0.0, 0.0), (0.0, 80.0), (100.0, 80.0), (100.0, 0.0)]

    p_ccw = PatternPiece(
        id="box_ccw",
        name="Box CCW",
        target_width_cm=10.0,
        target_height_cm=8.0,
        outer_contour=Contour.from_polyline(pts_ccw, is_closed=True),
    )
    p_cw = PatternPiece(
        id="box_cw",
        name="Box CW",
        target_width_cm=10.0,
        target_height_cm=8.0,
        outer_contour=Contour.from_polyline(pts_cw, is_closed=True),
    )

    # Uniform 10mm
    u_ccw = apply_uniform_seam_allowance(p_ccw, seam_mm=10.0)
    u_cw = apply_uniform_seam_allowance(p_cw, seam_mm=10.0)

    poly_u_ccw = _get_polygon_from_contour(u_ccw.cut_contour)
    poly_u_cw = _get_polygon_from_contour(u_cw.cut_contour)

    assert poly_u_ccw.is_valid
    assert poly_u_cw.is_valid
    assert abs(poly_u_ccw.area - poly_u_cw.area) < 1e-3
    assert abs(poly_u_ccw.area - (120.0 * 100.0)) < 1e-2

    _assert_valid_dilation(p_ccw, u_ccw)
    _assert_valid_dilation(p_cw, u_cw)

    # Variable: bottom edge 0mm, rest 10mm
    p_ccw_v = p_ccw.model_copy(deep=True)
    p_ccw_v.outer_contour.segments[0].seam_allowance_mm = 0.0
    v_ccw = apply_variable_seam_allowance(p_ccw_v, default_seam_mm=10.0)

    p_cw_v = p_cw.model_copy(deep=True)
    # In CW: edge (100,0)->(0,0) is index 3
    p_cw_v.outer_contour.segments[3].seam_allowance_mm = 0.0
    v_cw = apply_variable_seam_allowance(p_cw_v, default_seam_mm=10.0)

    poly_v_ccw = _get_polygon_from_contour(v_ccw.cut_contour)
    poly_v_cw = _get_polygon_from_contour(v_cw.cut_contour)

    assert poly_v_ccw.is_valid
    assert poly_v_cw.is_valid
    assert abs(poly_v_ccw.area - poly_v_cw.area) < 1e-2
    _assert_valid_dilation(p_ccw, v_ccw)
    _assert_valid_dilation(p_cw, v_cw)


# ==============================================================================
# 3. RE-ENTRANT STARS & CONCAVE POLYGONS WITH MULTIPLE INNER CORNERS
# ==============================================================================

@pytest.mark.parametrize("num_points", [5, 8, 12])
def test_star_polygons_self_intersection_avoidance(num_points: int) -> None:
    """
    Stress-tests concave star shapes with sharp re-entrant corners.
    Offsets often create self-intersections at concave corners that require
    topological cleaning (Pyclipper CT_UNION).
    """
    cx, cy = 100.0, 100.0
    r_outer = 60.0
    r_inner = 20.0

    pts = []
    total_vertices = num_points * 2
    for i in range(total_vertices):
        angle = i * 2.0 * math.pi / total_vertices
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.sin(angle), cy + r * math.cos(angle)))

    star_piece = PatternPiece(
        id=f"star_{num_points}",
        name=f"Star {num_points}",
        target_width_cm=12.0,
        target_height_cm=12.0,
        outer_contour=Contour.from_polyline(pts, is_closed=True),
    )

    # Test uniform offsetting
    u = apply_uniform_seam_allowance(star_piece, seam_mm=10.0)
    _assert_valid_dilation(star_piece, u)

    # Test variable offsetting: alternating 15mm elastic and 10mm seam
    star_var = star_piece.model_copy(deep=True)
    for i, seg in enumerate(star_var.outer_contour.segments):
        if i % 2 == 0:
            seg.edge_role = "elastic"
            seg.seam_allowance_mm = 15.0
        else:
            seg.edge_role = "seam"
            seg.seam_allowance_mm = 10.0

    v = apply_variable_seam_allowance(star_var, default_seam_mm=10.0)
    _assert_valid_dilation(star_piece, v)


# ==============================================================================
# 4. COLLAPSING CONCAVE CHANNELS (U-SHAPE & SERPENTINE)
# ==============================================================================

def test_narrow_channel_slot_collapse() -> None:
    """
    A narrow U-shaped slot (e.g. 10 mm wide gap) offset by 10 mm or 50 mm.
    When offset distance >= half slot width, the opposite sides intersect and
    must merge cleanly without leaving self-intersections or invalid holes.
    """
    u_pts = [
        (0.0, 100.0),
        (0.0, 0.0),
        (30.0, 0.0),
        (30.0, 100.0),
        (20.0, 100.0),
        (20.0, 10.0),
        (10.0, 10.0),
        (10.0, 100.0),
    ]
    piece = PatternPiece(
        id="narrow_u",
        name="Narrow U",
        target_width_cm=3.0,
        target_height_cm=10.0,
        outer_contour=Contour.from_polyline(u_pts, is_closed=True),
    )

    for offset in [5.0, 10.0, 25.0, 50.0]:
        u = apply_uniform_seam_allowance(piece, seam_mm=offset)
        _assert_valid_dilation(piece, u)

        piece_var = piece.model_copy(deep=True)
        piece_var.outer_contour.segments[1].edge_role = "fold"  # bottom edge fold
        v = apply_variable_seam_allowance(piece_var, default_seam_mm=offset)
        _assert_valid_dilation(piece, v)


# ==============================================================================
# 5. ZERO OFFSET & LARGE OFFSETS (0mm, 50mm, 100mm)
# ==============================================================================

def test_zero_offset_preserves_net_contour() -> None:
    """
    Zero offset (0 mm) must create cut_contour identical to outer_contour.
    """
    pts = [(0.0, 0.0), (120.0, 0.0), (120.0, 70.0), (0.0, 70.0)]
    piece = PatternPiece(
        id="rect",
        name="Rect",
        target_width_cm=12.0,
        target_height_cm=7.0,
        outer_contour=Contour.from_polyline(pts, is_closed=True),
    )

    u0 = apply_uniform_seam_allowance(piece, seam_mm=0.0)
    p_orig = _get_polygon_from_contour(piece.outer_contour)
    p_u0 = _get_polygon_from_contour(u0.cut_contour)
    assert p_u0.is_valid
    assert abs(p_orig.area - p_u0.area) < 1e-4
    assert p_orig.difference(p_u0).area < 1e-4

    # Variable all fold (0mm)
    piece_fold = piece.model_copy(deep=True)
    for seg in piece_fold.outer_contour.segments:
        seg.edge_role = "fold"
    v0 = apply_variable_seam_allowance(piece_fold, default_seam_mm=10.0)
    p_v0 = _get_polygon_from_contour(v0.cut_contour)
    assert p_v0.is_valid
    assert abs(p_orig.area - p_v0.area) < 1e-4


@pytest.mark.parametrize("large_offset", [50.0, 100.0])
def test_large_offsets_extreme_expansion(large_offset: float) -> None:
    """
    Offsets larger than the piece itself (50mm, 100mm).
    Must remain valid, single-polygon (or dominant outer exterior), non-self-intersecting.
    """
    pts = [(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)]
    piece = PatternPiece(
        id="small_box",
        name="Small Box",
        target_width_cm=4.0,
        target_height_cm=3.0,
        outer_contour=Contour.from_polyline(pts, is_closed=True),
    )

    u = apply_uniform_seam_allowance(piece, seam_mm=large_offset)
    _assert_valid_dilation(piece, u)

    piece_var = piece.model_copy(deep=True)
    piece_var.outer_contour.segments[0].edge_role = "fold"
    v = apply_variable_seam_allowance(piece_var, default_seam_mm=large_offset)
    _assert_valid_dilation(piece, v)


# ==============================================================================
# 6. ZERO-LENGTH EDGES & COLLINEAR SEGMENTS WITH VARIABLE ALLOWANCE
# ==============================================================================

def test_zero_length_segments_and_duplicate_vertices() -> None:
    """
    Polygons with duplicate vertices or zero-length segments must not crash or divide by zero.
    """
    segs = [
        PathSegment.line((0.0, 0.0), (100.0, 0.0), seam_allowance_mm=10.0),
        PathSegment.line((100.0, 0.0), (100.0, 0.0), seam_allowance_mm=0.0),  # zero-length
        PathSegment.line((100.0, 0.0), (100.0, 80.0), seam_allowance_mm=15.0),
        PathSegment.line((100.0, 80.0), (0.0, 80.0), seam_allowance_mm=10.0),
        PathSegment.line((0.0, 80.0), (0.0, 0.0), seam_allowance_mm=10.0),
    ]
    piece = PatternPiece(
        id="zero_edge",
        name="Zero Edge Test",
        target_width_cm=10.0,
        target_height_cm=8.0,
        outer_contour=Contour(segments=segs, is_closed=True),
    )

    u = apply_uniform_seam_allowance(piece, seam_mm=10.0)
    assert _get_polygon_from_contour(u.cut_contour).is_valid

    v = apply_variable_seam_allowance(piece, default_seam_mm=10.0)
    assert _get_polygon_from_contour(v.cut_contour).is_valid


def test_collinear_adjacent_segments_differing_allowance() -> None:
    """
    Adjacent collinear segments that have different seam allowances (e.g. 10mm and 0mm fold)
    must form a valid step transition without self-intersection.
    """
    segs = [
        PathSegment.line((0.0, 0.0), (50.0, 0.0), seam_allowance_mm=10.0),
        PathSegment.line((50.0, 0.0), (100.0, 0.0), seam_allowance_mm=0.0),  # collinear split
        PathSegment.line((100.0, 0.0), (100.0, 100.0), seam_allowance_mm=10.0),
        PathSegment.line((100.0, 100.0), (0.0, 100.0), seam_allowance_mm=10.0),
        PathSegment.line((0.0, 100.0), (0.0, 0.0), seam_allowance_mm=10.0),
    ]
    piece = PatternPiece(
        id="collinear_step",
        name="Collinear Step",
        target_width_cm=10.0,
        target_height_cm=10.0,
        outer_contour=Contour(segments=segs, is_closed=True),
    )

    v = apply_variable_seam_allowance(piece, default_seam_mm=10.0)
    poly = _get_polygon_from_contour(v.cut_contour)
    assert poly.is_valid
    assert poly.bounds == (-10.0, -10.0, 110.0, 110.0)
    assert abs(poly.area - 13800.0) < 1.0


# ==============================================================================
# 7. DATA CONTRACT RIGOROUS ADVERSARIAL VALIDATION
# ==============================================================================

def test_data_contract_rejects_negative_or_invalid_values() -> None:
    """
    Ensures Pydantic schema rejects negative dimensions, invalid roles, and bad units.
    """
    dummy_contour = Contour.from_polyline([(0, 0), (10, 0), (10, 10)])

    # Negative width
    with pytest.raises(pydantic.ValidationError):
        PatternPiece(
            id="bad",
            name="Bad",
            target_width_cm=-5.0,
            target_height_cm=10.0,
            outer_contour=dummy_contour,
        )

    # Zero or negative quantity
    with pytest.raises(pydantic.ValidationError):
        PatternPiece(
            id="bad",
            name="Bad",
            quantity=0,
            target_width_cm=5.0,
            target_height_cm=10.0,
            outer_contour=dummy_contour,
        )

    # Negative segment seam allowance
    with pytest.raises(pydantic.ValidationError):
        PathSegment.line((0, 0), (10, 0), seam_allowance_mm=-1.0)

    # Invalid edge role
    with pytest.raises(pydantic.ValidationError):
        PathSegment.line((0, 0), (10, 0), edge_role="non_existent")  # type: ignore

    # Invalid units
    with pytest.raises(pydantic.ValidationError):
        PatternProject(project_name="P", units="inches")  # type: ignore


def test_data_contract_json_serialization_roundtrip(tmp_path: Path) -> None:
    """
    Ensures that a PatternProject with seam allowances, cut contours, and internal lines
    round-trips to JSON and back without loss of data or precision.
    """
    project = create_gorro_trapper_project()
    project_with_seams = apply_seam_allowance_to_project(project)

    json_path = tmp_path / "project_with_seams.json"
    project_with_seams.save_json(json_path)

    loaded = PatternProject.load_json(json_path)
    assert loaded.project_name == project_with_seams.project_name
    assert loaded.has_seam_allowance is True
    assert len(loaded.pieces) == len(project_with_seams.pieces)

    for orig, res in zip(project_with_seams.pieces, loaded.pieces):
        assert orig.id == res.id
        assert orig.has_cut_contour == res.has_cut_contour
        assert len(orig.cut_contour.segments) == len(res.cut_contour.segments)


# ==============================================================================
# 8. GORRO TRAPPER 6-PIECE REFERENCE PRESET VALIDATION
# ==============================================================================

def test_gorro_trapper_all_pieces_seam_allowance_outward_dilation() -> None:
    """
    Applies seam allowance engine to the entire Gorro Trapper project and verifies
    that every single pattern piece has a valid cut contour that strictly encloses
    the net sewing line.
    """
    project = create_gorro_trapper_project()
    project_seams = apply_seam_allowance_to_project(project)

    assert project_seams.has_seam_allowance is True
    assert len(project_seams.pieces) == 6

    for piece in project_seams.pieces:
        assert piece.has_cut_contour
        _assert_valid_dilation(piece, piece, tol=1e-3)
