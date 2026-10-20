"""
Test suite for Stage 2 Milestone 2: Raster Segmentation & Schneider Bézier Vectorizer.

Verifies:
- F06: Raster Segmentation & Background Suppression (segment_pattern_pieces, segment_pattern_pieces_detailed)
- F07: Sharp Corner / Vertex Detection (detect_corners)
- F08: Schneider Least-Squares Cubic Bézier Curve Fitting (fit_cubic_bezier)
- F09: Collinear Straight Edge Detection (PathSegment.line vs PathSegment.bezier)
- Vectorization to valid CAD Contours & Shapely Polygons (vectorize_contour, vectorize_contour_detailed)
- Coordinate Transforms (PixelToMMTransform, Y-up convention)
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List

import cv2
import numpy as np
import pytest
from shapely.geometry import Polygon

from src.core.contract import Contour, PathSegment, Point2D
from src.core.geometry import (
    contour_to_shapely_polygon,
    get_contour_bounds,
    get_contour_dimensions_mm,
)
from src.digitizer.scale_recovery import ScaleResult
from src.digitizer.vectorizer import (
    PixelToMMTransform,
    SegmentedPiece,
    VectorizationResult,
    build_piece_mask,
    detect_corners,
    fit_cubic_bezier,
    load_image,
    max_deviation_to_contour,
    resample_closed_polyline,
    segment_pattern_pieces,
    segment_pattern_pieces_detailed,
    vectorize_contour,
    vectorize_contour_detailed,
)

SAMPLE_IMAGE_PATH = Path("samples/gorro_trapper.png")


# ===========================================================================
# 1. Image Loading & Preprocessing
# ===========================================================================


class TestImageLoading:
    """Verifies load_image across diverse input formats and edge cases."""

    def test_load_image_from_path(self):
        if not SAMPLE_IMAGE_PATH.exists():
            pytest.skip("Sample image not found on disk")
        img = load_image(SAMPLE_IMAGE_PATH)
        assert isinstance(img, np.ndarray)
        assert img.ndim == 3 and img.shape[2] == 3
        assert img.dtype == np.uint8

    def test_load_image_from_string_path(self):
        if not SAMPLE_IMAGE_PATH.exists():
            pytest.skip("Sample image not found on disk")
        img = load_image(str(SAMPLE_IMAGE_PATH))
        assert isinstance(img, np.ndarray)
        assert img.ndim == 3 and img.shape[2] == 3

    def test_load_image_from_bgr_array(self):
        arr = np.zeros((100, 100, 3), dtype=np.uint8)
        arr[20:80, 20:80] = [150, 200, 220]
        loaded = load_image(arr)
        assert np.array_equal(loaded, arr)

    def test_load_image_from_grayscale_array(self):
        gray = np.full((50, 60), 128, dtype=np.uint8)
        loaded = load_image(gray)
        assert loaded.shape == (50, 60, 3)
        assert np.all(loaded == 128)

    def test_load_image_from_rgba_composites_alpha(self):
        rgba = np.zeros((40, 40, 4), dtype=np.uint8)
        # 50% transparent red
        rgba[:, :] = [0, 0, 255, 128]
        loaded = load_image(rgba)
        assert loaded.shape == (40, 40, 3)
        # Red blended over white background: ~ [128, 128, 255]
        assert 110 <= loaded[0, 0, 0] <= 140
        assert 110 <= loaded[0, 0, 1] <= 140
        assert 240 <= loaded[0, 0, 2] <= 255

    def test_load_image_nonexistent_path_raises(self):
        with pytest.raises(FileNotFoundError):
            load_image("nonexistent_path_to_image_xyz123.png")

    def test_load_image_empty_array_raises(self):
        with pytest.raises(ValueError, match="empty"):
            load_image(np.zeros((0, 0, 3), dtype=np.uint8))

    def test_load_image_invalid_type_raises(self):
        with pytest.raises(TypeError):
            load_image(12345)  # type: ignore[arg-type]


# ===========================================================================
# 2. Resampling & Polylines
# ===========================================================================


class TestPolylineResampling:
    """Verifies arc-length resampling of closed boundaries."""

    def test_resample_square(self):
        # 100x100 square
        pts = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]])
        resampled = resample_closed_polyline(pts, step=5.0)
        # Perimeter = 400 mm, step = 5 mm -> ~80 points
        assert 78 <= len(resampled) <= 82
        # Bounds must be preserved
        assert pytest.approx(resampled[:, 0].min(), abs=0.5) == 0.0
        assert pytest.approx(resampled[:, 0].max(), abs=0.5) == 100.0
        assert pytest.approx(resampled[:, 1].min(), abs=0.5) == 0.0
        assert pytest.approx(resampled[:, 1].max(), abs=0.5) == 100.0

    def test_resample_degenerate_points(self):
        pts = np.array([[10.0, 20.0]])
        out = resample_closed_polyline(pts, step=1.0)
        assert len(out) == 1


# ===========================================================================
# 3. Corner Detection (F07)
# ===========================================================================


class TestCornerDetection:
    """Verifies discrete turning-angle corner detection on geometric shapes."""

    def test_detect_corners_rectangle(self):
        # 200x100 rectangle resampled at 1 px step
        corners = np.array([[10.0, 10.0], [210.0, 10.0], [210.0, 110.0], [10.0, 110.0]])
        dense = resample_closed_polyline(corners, step=2.0)
        found_idx = detect_corners(dense, angle_thresh_deg=40.0, window_k=5)
        assert len(found_idx) == 4
        found_pts = dense[found_idx]
        for c in corners:
            dist = np.linalg.norm(found_pts - c, axis=1).min()
            assert dist < 3.0

    def test_detect_corners_triangle(self):
        tri = np.array([[50.0, 10.0], [150.0, 150.0], [10.0, 120.0]])
        dense = resample_closed_polyline(tri, step=2.0)
        found_idx = detect_corners(dense, angle_thresh_deg=35.0, window_k=5)
        assert len(found_idx) == 3

    def test_detect_corners_smooth_circle_has_no_corners(self):
        # Circle of radius 50: turning angle per 5 px step is only ~5.7 degrees
        theta = np.linspace(0, 2 * np.pi, 200, endpoint=False)
        circle = np.column_stack([50.0 + 50.0 * np.cos(theta), 50.0 + 50.0 * np.sin(theta)])
        found = detect_corners(circle, angle_thresh_deg=35.0, window_k=5)
        assert len(found) == 0

    def test_detect_corners_l_shape(self):
        # L-shape polygon with 6 vertices (5 convex, 1 reflex)
        l_shape = np.array([
            [0.0, 0.0],
            [100.0, 0.0],
            [100.0, 40.0],
            [40.0, 40.0],
            [40.0, 100.0],
            [0.0, 100.0],
        ])
        dense = resample_closed_polyline(l_shape, step=2.0)
        found = detect_corners(dense, angle_thresh_deg=45.0, window_k=5)
        assert len(found) == 6


# ===========================================================================
# 4. Schneider Least-Squares Bézier Fitting (F08, F09)
# ===========================================================================


class TestSchneiderBezierFitting:
    """Verifies Philip J. Schneider's least-squares curve fitting algorithm."""

    def test_collinear_chain_returns_single_line_segment(self):
        """F09: Points sampled along a straight line must return a PathSegment.line."""
        t = np.linspace(0.0, 1.0, 50)
        # Line from (10, 20) to (110, 120) with small noise < 0.05 mm
        pts = np.column_stack([10.0 + 100.0 * t, 20.0 + 100.0 * t])
        segs = fit_cubic_bezier(pts, max_error_mm=0.5, collinear_tol_mm=0.3)
        assert len(segs) == 1
        assert segs[0].type == "line"
        assert pytest.approx(segs[0].start.x, abs=1e-3) == 10.0
        assert pytest.approx(segs[0].start.y, abs=1e-3) == 20.0
        assert pytest.approx(segs[0].end.x, abs=1e-3) == 110.0
        assert pytest.approx(segs[0].end.y, abs=1e-3) == 120.0

    def test_two_points_returns_line(self):
        pts = np.array([[5.0, 10.0], [55.0, 60.0]])
        segs = fit_cubic_bezier(pts)
        assert len(segs) == 1
        assert segs[0].type == "line"

    def test_quarter_circle_arc_fits_within_tolerance(self):
        """
        Acceptance Criteria: 200-point sampled arc fits in <= 4 segments with max error < 0.5 mm.
        """
        theta = np.linspace(0.0, np.pi / 2.0, 200)
        radius = 80.0
        pts = np.column_stack([radius * np.cos(theta), radius * np.sin(theta)])
        segs = fit_cubic_bezier(pts, max_error_mm=0.5, max_subdivision=4)

        # A 90-degree circular arc is readily represented by 1 or 2 cubic Béziers
        assert len(segs) <= 4
        # Verify C0 continuity
        for i in range(len(segs) - 1):
            assert pytest.approx(segs[i].end.x, abs=1e-5) == segs[i + 1].start.x
            assert pytest.approx(segs[i].end.y, abs=1e-5) == segs[i + 1].start.y

        # Measure maximum error across points
        cont = Contour(segments=segs, is_closed=False)
        max_err = max_deviation_to_contour(pts, cont)
        assert max_err < 0.5

    def test_s_curve_subdivision(self):
        """S-shaped curve with inflection point requires subdivision."""
        t = np.linspace(0.0, 1.0, 150)
        # S-curve: x = 100*t, y = 30*sin(2*pi*t)
        pts = np.column_stack([100.0 * t, 30.0 * np.sin(2 * np.pi * t)])
        segs = fit_cubic_bezier(pts, max_error_mm=0.5, max_subdivision=6)
        assert len(segs) >= 2  # inflection requires at least 2 cubic pieces
        cont = Contour(segments=segs, is_closed=False)
        max_err = max_deviation_to_contour(pts, cont)
        assert max_err < 0.5

    def test_invalid_arguments_raise_error(self):
        with pytest.raises(ValueError, match="positive finite"):
            fit_cubic_bezier(np.array([[0.0, 0.0], [10.0, 10.0]]), max_error_mm=-1.0)

        with pytest.raises(ValueError, match="At least two distinct"):
            fit_cubic_bezier(np.array([[5.0, 5.0]]))


# ===========================================================================
# 5. Coordinate Transform (PixelToMMTransform)
# ===========================================================================


class TestPixelToMMTransform:
    """Verifies coordinate conversion between OpenCV pixels and CAD millimeters."""

    def test_y_up_transform_and_inverse(self):
        # Image size 800x600, piece bounding box: x in [100, 300], y in [200, 500]
        # In Y-up mode, CAD origin is (x_min, y_max) = (100, 500)
        tf = PixelToMMTransform(
            origin_x_px=100.0,
            origin_y_px=500.0,
            mm_per_px_x=0.5,
            mm_per_px_y=0.5,
            y_up=True,
        )
        # Bottom-left in pixels is (100, 500) -> should map to (0, 0) mm
        p_bl = tf.apply(np.array([[100.0, 500.0]]))[0]
        assert pytest.approx(p_bl[0], abs=1e-5) == 0.0
        assert pytest.approx(p_bl[1], abs=1e-5) == 0.0

        # Top-right in pixels is (300, 200) -> dx = 200 px * 0.5 = 100 mm; dy = (500 - 200) * 0.5 = 150 mm
        p_tr = tf.apply(np.array([[300.0, 200.0]]))[0]
        assert pytest.approx(p_tr[0], abs=1e-5) == 100.0
        assert pytest.approx(p_tr[1], abs=1e-5) == 150.0

        # Round-trip through inverse
        inv = tf.inverse(np.array([[100.0, 150.0]]))[0]
        assert pytest.approx(inv[0], abs=1e-5) == 300.0
        assert pytest.approx(inv[1], abs=1e-5) == 200.0

    def test_apply_point_helper(self):
        tf = PixelToMMTransform(10.0, 10.0, 2.0, 3.0, y_up=False)
        pt = tf.apply_point(20.0, 20.0)
        assert isinstance(pt, Point2D)
        assert pt.x == 20.0
        assert pt.y == 30.0


# ===========================================================================
# 6. Raster Segmentation (F06)
# ===========================================================================


class TestRasterSegmentation:
    """Verifies segmentation on synthetic images and sample pattern assets."""

    def test_segment_synthetic_tan_pieces(self):
        # Create white canvas with two tan-colored filled patches
        canvas = np.full((300, 400, 3), 255, dtype=np.uint8)
        # Piece 1: 80x80 square at (50, 50)
        canvas[50:130, 50:130] = [158, 203, 225]  # tan in BGR (Hue ~20)
        # Piece 2: 60x100 rectangle at (200, 150)
        canvas[150:250, 200:260] = [158, 203, 225]

        pieces = segment_pattern_pieces_detailed(canvas, min_area_px=500.0, hsv_tan_filter=True)
        assert len(pieces) == 2
        # Reading order: piece 1 (y=50) should come before piece 2 (y=150)
        assert pieces[0].bbox_px[1] < pieces[1].bbox_px[1]
        assert pieces[0].coverage > 0.8
        assert pieces[0].flat_fill_ratio > 0.8

    def test_segment_line_art_mode(self):
        # Black line drawing on white canvas
        canvas = np.full((200, 200, 3), 255, dtype=np.uint8)
        cv2.rectangle(canvas, (40, 40), (160, 160), (0, 0, 0), thickness=3)

        pieces = segment_pattern_pieces_detailed(canvas, min_area_px=1000.0, hsv_tan_filter=False)
        assert len(pieces) >= 1
        p = pieces[0]
        assert p.mode == "line_art"
        # Bounding box should approximate 40, 40, 120, 120
        assert abs(p.bbox_px[2] - 120) < 6
        assert abs(p.bbox_px[3] - 120) < 6

    def test_segment_sample_gorro_trapper(self):
        if not SAMPLE_IMAGE_PATH.exists():
            pytest.skip("Sample image not found on disk")
        pieces = segment_pattern_pieces_detailed(SAMPLE_IMAGE_PATH, min_area_px=1000.0)
        # In gorro_trapper.png there are 11 distinct tan-filled piece shapes
        assert len(pieces) >= 6  # at least the 6 primary pattern pieces
        # Verify piece properties
        for p in pieces:
            assert p.area_px >= 1000.0
            assert p.coverage >= 0.7
            assert len(p.contour_px) >= 20
            assert p.flat_fill_ratio >= 0.2


# ===========================================================================
# 7. Contour Vectorization (vectorize_contour & vectorize_contour_detailed)
# ===========================================================================


class TestContourVectorization:
    """Verifies end-to-end vectorization from pixel boundaries to closed CAD contours."""

    def test_vectorize_synthetic_rectangle(self):
        # 100 x 50 pixel rectangle
        rect_px = np.array([
            [10.0, 10.0],
            [110.0, 10.0],
            [110.0, 60.0],
            [10.0, 60.0],
        ])
        scale = ScaleResult.create_isotropic(1.0, "two_point_reference", 1.0)
        res = vectorize_contour_detailed(rect_px, scale, max_error_mm=0.5)

        assert isinstance(res, VectorizationResult)
        assert res.contour.is_closed is True
        assert len(res.corner_indices) == 4
        assert res.n_lines >= 4
        # Shapely polygon must be valid
        poly = contour_to_shapely_polygon(res.contour)
        assert poly.is_valid is True
        assert not poly.is_empty
        w, h = get_contour_dimensions_mm(res.contour)
        assert pytest.approx(w, abs=1.5) == 100.0
        assert pytest.approx(h, abs=1.5) == 50.0

    def test_vectorize_synthetic_ellipse(self):
        # Ellipse with major axis 120 px, minor axis 60 px
        t = np.linspace(0, 2 * np.pi, 160, endpoint=False)
        ellipse_px = np.column_stack([100.0 + 60.0 * np.cos(t), 100.0 + 30.0 * np.sin(t)])
        scale = ScaleResult.create_isotropic(1.0, "two_point_reference", 1.0)
        res = vectorize_contour_detailed(ellipse_px, scale, max_error_mm=0.5)

        poly = contour_to_shapely_polygon(res.contour)
        assert poly.is_valid is True
        assert res.n_beziers >= 4
        assert res.max_fit_error_mm < 0.8

    def test_vectorize_pieces_from_sample_image(self):
        if not SAMPLE_IMAGE_PATH.exists():
            pytest.skip("Sample image not found on disk")

        pieces = segment_pattern_pieces_detailed(SAMPLE_IMAGE_PATH, min_area_px=3000.0)
        scale = ScaleResult.create_isotropic(1.0, "fallback_default", 0.1)

        for p in pieces[:5]:
            res = vectorize_contour_detailed(p.contour_px, scale, max_error_mm=0.75)
            # Must yield closed contour
            assert res.contour.is_closed is True
            # Must be a valid Shapely polygon
            poly = contour_to_shapely_polygon(res.contour)
            assert poly.is_valid is True, f"Piece {p.index} polygon is invalid: {poly}"
            assert poly.area > 500.0
            # Fitting error within effective tolerance
            assert res.max_fit_error_mm <= res.effective_tolerance_mm + 0.1

    def test_vectorize_contour_simple_wrapper(self):
        rect_px = np.array([[0.0, 0.0], [50.0, 0.0], [50.0, 50.0], [0.0, 50.0]])
        scale = ScaleResult.create_isotropic(2.0, "fallback_default", 1.0)
        contour = vectorize_contour(rect_px, scale, max_error_mm=0.5)
        assert isinstance(contour, Contour)
        assert contour.is_closed is True
        poly = contour_to_shapely_polygon(contour)
        assert poly.is_valid is True
        # Scaled by 2.0 mm/px: 50 px -> ~100 mm (+ 0.5 px edge correction = 51 px * 2 = 102 mm)
        w, h = get_contour_dimensions_mm(contour)
        assert 98.0 <= w <= 104.0
        assert 98.0 <= h <= 104.0
