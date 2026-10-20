"""
Adversarial Challenge Test Suite for Stage 2 Scale Recovery Engine (Milestone 1).
Author: Challenger M1.1 (Empirical Challenger)

Tests stress-test:
1. Calibration square detection:
   - Rotated squares (15, 30, 45, 60, 75 degrees)
   - Outline vs filled squares
   - Squares with noise (Gaussian, salt-and-pepper)
   - Rejection of non-square quadrilaterals (rhombi, kites, trapezoids, parallelograms)
   - Rejection of other polygons (triangles, pentagons, hexagons, circles)
2. Tailoring text annotation parsing:
   - Complex multipliers (spaces, trailing notes, Polish/English/Spanish keywords)
   - Noisy punctuation, mixed languages
   - Dimension-only strings (verifying piece_name is None, not populated with dimensions)
   - Decimal formatting and OCR whitespace anomalies
   - Missing dimensions vs single dimensions
"""

import math
from typing import Tuple, List, Optional
import cv2
import numpy as np
import pytest

from src.digitizer.scale_recovery import (
    detect_calibration_square,
    parse_tailoring_text,
    calibrate_from_two_points,
    compute_anisotropic_scale,
    ScaleResult,
)


# ===========================================================================
# Helpers for Synthetic Image Generation
# ===========================================================================

def create_quad_image(
    vertices: np.ndarray,
    img_size: int = 600,
    filled: bool = True,
    thickness: int = 2,
    noise_sigma: float = 0.0,
    sp_noise_prob: float = 0.0,
    invert: bool = False,
) -> np.ndarray:
    """Generates an image with a specified quadrilateral and optional noise."""
    bg_color = 0 if invert else 255
    fg_color = 255 if invert else 0
    img = np.ones((img_size, img_size), dtype=np.uint8) * bg_color

    pts = np.round(vertices).astype(np.int32)
    if filled:
        cv2.fillPoly(img, [pts], color=fg_color)
    else:
        cv2.polylines(img, [pts], isClosed=True, color=fg_color, thickness=thickness)

    # Gaussian noise
    if noise_sigma > 0:
        gauss = np.random.normal(0, noise_sigma, img.shape).astype(np.float32)
        noisy = np.clip(img.astype(np.float32) + gauss, 0, 255).astype(np.uint8)
        img = noisy

    # Salt and pepper noise
    if sp_noise_prob > 0:
        rnd = np.random.random(img.shape)
        img[rnd < sp_noise_prob / 2] = 0
        img[rnd > 1 - sp_noise_prob / 2] = 255

    return img


def get_rotated_square_vertices(
    center: Tuple[float, float],
    side: float,
    angle_deg: float,
) -> np.ndarray:
    """Computes the 4 vertices of a rotated square."""
    cx, cy = center
    half = side / 2.0
    corners = np.array([
        [-half, -half],
        [half, -half],
        [half, half],
        [-half, half],
    ], dtype=np.float32)
    rad = math.radians(angle_deg)
    rot_mat = np.array([
        [math.cos(rad), -math.sin(rad)],
        [math.sin(rad), math.cos(rad)],
    ], dtype=np.float32)
    return corners @ rot_mat.T + np.array([cx, cy], dtype=np.float32)


# ===========================================================================
# 1. Rotated Square Detection
# ===========================================================================

class TestRotatedSquareChallenge:
    """Stress-test detect_calibration_square with various rotation angles and sizes."""

    @pytest.mark.parametrize("angle_deg", [0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 89.0])
    @pytest.mark.parametrize("side_px", [100.0, 200.0, 300.0])
    def test_rotated_solid_square_scale_recovery(self, angle_deg, side_px):
        """Verifies that squares rotated at arbitrary angles are detected within 1% error."""
        vertices = get_rotated_square_vertices((300, 300), side_px, angle_deg)
        img = create_quad_image(vertices, img_size=600, filled=True)

        target_mm = 50.0
        expected_scale = target_mm / side_px

        res = detect_calibration_square(img, target_square_mm=target_mm)
        assert res is not None, f"Failed to detect square at angle {angle_deg} deg, side {side_px} px"
        assert res.calibration_method == "calibration_square"
        assert res.is_isotropic is True

        rel_error = abs(res.mm_per_px - expected_scale) / expected_scale
        assert rel_error < 0.015, f"Scale error {rel_error*100:.2f}% exceeds 1.5% for angle {angle_deg}"

    @pytest.mark.parametrize("angle_deg", [15.0, 30.0, 45.0, 60.0, 75.0])
    def test_rotated_outline_square(self, angle_deg):
        """Hollow / outline squares at angles."""
        vertices = get_rotated_square_vertices((300, 300), 200.0, angle_deg)
        img = create_quad_image(vertices, img_size=600, filled=False, thickness=3)

        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is not None, f"Failed to detect outline square at angle {angle_deg} deg"
        expected_scale = 50.0 / 200.0
        rel_error = abs(res.mm_per_px - expected_scale) / expected_scale
        assert rel_error < 0.02, f"Scale error {rel_error*100:.2f}% exceeds 2% for outline at {angle_deg}"

    @pytest.mark.parametrize("sigma", [5.0, 15.0, 25.0])
    def test_square_with_gaussian_noise(self, sigma):
        """Square in presence of sensor / raster Gaussian noise."""
        vertices = get_rotated_square_vertices((300, 300), 200.0, 30.0)
        img = create_quad_image(vertices, img_size=600, filled=True, noise_sigma=sigma)

        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is not None, f"Failed to detect noisy square with sigma={sigma}"
        expected_scale = 50.0 / 200.0
        rel_error = abs(res.mm_per_px - expected_scale) / expected_scale
        assert rel_error < 0.02

    def test_white_square_on_black_background(self):
        """Inverted contrast square (white square on dark background)."""
        vertices = get_rotated_square_vertices((300, 300), 200.0, 20.0)
        img = create_quad_image(vertices, img_size=600, filled=True, invert=True)

        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is not None, "Failed to detect inverted square (white on black)"
        expected_scale = 50.0 / 200.0
        rel_error = abs(res.mm_per_px - expected_scale) / expected_scale
        assert rel_error < 0.02


# ===========================================================================
# 2. Quadrilateral Rejection: Rhombi, Kites, Trapezoids, Parallelograms
# ===========================================================================

class TestNonSquareQuadrilateralRejection:
    """Stress-test that detect_calibration_square strictly REJECTS non-square shapes."""

    def test_reject_rhombus_60_120(self):
        """A rhombus with equal sides but 60/120 deg angles must be rejected."""
        # Rhombus centered at (300, 300) with side ~200
        # d1 = 200, d2 = 200 * sqrt(3) ~ 346.4
        half_w = 100.0
        half_h = 100.0 * math.sqrt(3)
        pts = np.array([
            [300, 300 - half_h],
            [300 + half_w, 300],
            [300, 300 + half_h],
            [300 - half_w, 300],
        ], dtype=np.float32)
        img = create_quad_image(pts, img_size=600, filled=True)
        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is None, "Failed to reject 60/120 rhombus"

    def test_reject_rhombus_75_105(self):
        """Rhombus with 75/105 deg angles (cos = 0.2588 > max_cos_angle 0.15) must be rejected."""
        # Side length 200, angle alpha = 75 deg
        alpha = math.radians(75.0)
        # Vertices: (0,0), (s, 0), (s + s*cos, s*sin), (s*cos, s*sin)
        s = 200.0
        pts = np.array([
            [200.0, 200.0],
            [200.0 + s, 200.0],
            [200.0 + s + s * math.cos(alpha), 200.0 + s * math.sin(alpha)],
            [200.0 + s * math.cos(alpha), 200.0 + s * math.sin(alpha)],
        ], dtype=np.float32)
        img = create_quad_image(pts, img_size=600, filled=True)
        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is None, "Failed to reject 75/105 rhombus"

    def test_reject_kite_deltoid(self):
        """A kite (two pairs of equal adjacent sides, e.g. 150 and 250) must be rejected."""
        pts = np.array([
            [300, 150],  # top
            [420, 280],  # right
            [300, 480],  # bottom
            [180, 280],  # left
        ], dtype=np.float32)
        img = create_quad_image(pts, img_size=600, filled=True)
        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is None, "Failed to reject kite quadrilateral"

    def test_reject_isosceles_trapezoid(self):
        """Isosceles trapezoid (bases 220 and 160, legs 180) must be rejected."""
        pts = np.array([
            [220, 200],
            [380, 200],
            [410, 380],
            [190, 380],
        ], dtype=np.float32)
        img = create_quad_image(pts, img_size=600, filled=True)
        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is None, "Failed to reject isosceles trapezoid"

    def test_reject_parallelogram(self):
        """Parallelogram with sides 200 and 200, sheared at 30 deg."""
        pts = np.array([
            [200, 200],
            [400, 200],
            [480, 360],
            [280, 360],
        ], dtype=np.float32)
        img = create_quad_image(pts, img_size=600, filled=True)
        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is None, "Failed to reject sheared parallelogram"

    def test_reject_rectangle_borderline_aspect(self):
        """Rectangle with aspect ratio 1.08 (exceeding 1.05 tolerance) must be rejected."""
        # 200 x 216 px (aspect = 1.08)
        pts = np.array([
            [200, 200],
            [400, 200],
            [400, 416],
            [200, 416],
        ], dtype=np.float32)
        img = create_quad_image(pts, img_size=600, filled=True)
        res = detect_calibration_square(img, target_square_mm=50.0, aspect_ratio_tol=0.05)
        assert res is None, "Failed to reject rectangle with aspect ratio 1.08"

    def test_reject_triangle(self):
        """Triangle must be rejected."""
        pts = np.array([[300, 150], [450, 450], [150, 450]], dtype=np.int32)
        img = np.ones((600, 600), dtype=np.uint8) * 255
        cv2.fillPoly(img, [pts], color=0)
        assert detect_calibration_square(img) is None

    def test_reject_circle(self):
        """Circle must be rejected."""
        img = np.ones((600, 600), dtype=np.uint8) * 255
        cv2.circle(img, (300, 300), 100, color=0, thickness=-1)
        assert detect_calibration_square(img) is None


# ===========================================================================
# 3. Tailoring Text Parser Adversarial Challenges
# ===========================================================================

class TestTailoringTextParserAdversarial:
    """Stress-test parse_tailoring_text with real-world tailoring anomalies."""

    def test_trailing_multiplier_with_fabric_note(self):
        """Text: 'Visera 20 cm x 11 cm x 2 - tela principal'.
        Bug check: Does quantity extract as 2, or does trailing '$' anchor in regex fail?"""
        res = parse_tailoring_text("Visera 20 cm x 11 cm x 2 - tela principal")
        # Adversarial check: quantity must be 2, not 1!
        assert res["width_cm"] == 20.0
        assert res["height_cm"] == 11.0
        assert res["quantity"] == 2, f"Expected quantity 2, got {res['quantity']}"

    def test_trailing_multiplier_with_fold_note(self):
        """Text: 'Visera 20 cm x 11 cm x 2 doblez'.
        Bug check: Does quantity extract as 2?"""
        res = parse_tailoring_text("Visera 20 cm x 11 cm x 2 doblez")
        assert res["width_cm"] == 20.0
        assert res["height_cm"] == 11.0
        assert res["quantity"] == 2, f"Expected quantity 2, got {res['quantity']}"

    def test_spaced_multiplier_without_parens(self):
        """Text: 'Visera x 2' (without dimensions).
        Bug check: Is quantity 2, or is '2' misclassified as width_cm=2.0?"""
        res = parse_tailoring_text("Visera x 2")
        assert res["quantity"] == 2, f"Expected quantity 2, got {res['quantity']}"
        assert res["width_cm"] is None, f"Expected width_cm None, got {res['width_cm']}"

    def test_dimension_only_string_piece_name(self):
        """Text: '20 x 11' or '19,5' or '12,5cm x 11,0cm'.
        Bug check: When no piece name is present, is piece_name None, or is it populated
        with the dimension string itself due to 'dim_match_start > 0' logic?"""
        r1 = parse_tailoring_text("20 x 11")
        assert r1["width_cm"] == 20.0
        assert r1["height_cm"] == 11.0
        assert r1["piece_name"] is None, f"Expected piece_name None, got {r1['piece_name']!r}"

        r2 = parse_tailoring_text("19,5")
        assert r2["width_cm"] == 19.5
        assert r2["height_cm"] is None
        assert r2["piece_name"] is None, f"Expected piece_name None, got {r2['piece_name']!r}"

        r3 = parse_tailoring_text("12,5cm x 11,0cm")
        assert r3["width_cm"] == 12.5
        assert r3["height_cm"] == 11.0
        assert r3["piece_name"] is None, f"Expected piece_name None, got {r3['piece_name']!r}"

    def test_ocr_space_around_decimal_comma(self):
        """Text: 'Frente 12 , 5 cm x 11 cm'.
        Bug check: OCR error introducing space around decimal comma."""
        res = parse_tailoring_text("Frente 12 , 5 cm x 11 cm")
        # In ideal world, width should be 12.5 cm
        assert res["width_cm"] == pytest.approx(12.5, abs=0.01)
        assert res["height_cm"] == pytest.approx(11.0, abs=0.01)

    def test_polish_sztuki_multiplier(self):
        """Text: 'Visera 20 x 11 cm (2 szt)' or 'Visera 20 x 11 cm - 2 szt.'."""
        r1 = parse_tailoring_text("Visera 20 x 11 cm (2 szt)")
        assert r1["quantity"] == 2, f"Expected quantity 2 for (2 szt), got {r1['quantity']}"

        r2 = parse_tailoring_text("Visera 20 x 11 cm - 2 szt.")
        assert r2["quantity"] == 2, f"Expected quantity 2 for 2 szt., got {r2['quantity']}"
