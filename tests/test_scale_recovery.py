"""
Unit tests for Stage 2 Multi-Method Scale Recovery Engine.
Covers:
- Common Pydantic model: ScaleResult
- Scenario A: Tailoring text parser (F04), Anisotropic scaling (F05)
- Scenario B: Two-point reference calibration (F01)
- Scenario C: Calibration square detection (F02), DPI metadata extraction (F03), Fallback & Auto-detect cascade
- Edge cases: E01 to E08
"""

import math
import struct
from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from src.core.contract import Contour, PathSegment, PatternPiece, Point2D
from src.core.geometry import get_contour_dimensions_mm
from src.digitizer.scale_recovery import (
    ScaleResult,
    auto_detect_scale,
    calibrate_from_two_points,
    calibrate_piece_by_dimensions,
    compute_anisotropic_scale,
    detect_calibration_square,
    extract_dpi_scale,
    get_fallback_scale,
    parse_tailoring_text,
    scale_contour,
)
from src.presets.gorro_trapper import build_frente


# ===========================================================================
# 1. ScaleResult Model Tests
# ===========================================================================


class TestScaleResultModel:
    def test_scale_result_valid_isotropic(self):
        res = ScaleResult(
            mm_per_px_x=0.5,
            mm_per_px_y=0.5,
            is_isotropic=True,
            calibration_method="two_point_reference",
            confidence=1.0,
            metadata={"test": "val"},
        )
        assert res.mm_per_px == 0.5
        assert res.px_per_mm == 2.0
        assert res.is_isotropic is True
        assert res.calibration_method == "two_point_reference"
        assert res.confidence == 1.0
        assert res.metadata == {"test": "val"}

    def test_scale_result_create_isotropic_factory(self):
        res = ScaleResult.create_isotropic(
            mm_per_px=0.25,
            method="calibration_square",
            confidence=0.95,
            metadata={"side": 200},
        )
        assert res.mm_per_px_x == 0.25
        assert res.mm_per_px_y == 0.25
        assert res.mm_per_px == 0.25
        assert res.px_per_mm == 4.0
        assert res.is_isotropic is True

    def test_scale_result_anisotropic(self):
        res = ScaleResult(
            mm_per_px_x=1.0,
            mm_per_px_y=2.0,
            is_isotropic=False,
            calibration_method="dimension_anisotropic",
            confidence=1.0,
        )
        assert res.mm_per_px == 1.5
        assert res.px_per_mm == pytest.approx(1.0 / 1.5)
        assert res.is_isotropic is False

    def test_scale_result_validation_constraints(self):
        # Non-positive mm_per_px_x
        with pytest.raises(ValidationError):
            ScaleResult(
                mm_per_px_x=0.0,
                mm_per_px_y=1.0,
                calibration_method="two_point_reference",
                confidence=1.0,
            )
        with pytest.raises(ValidationError):
            ScaleResult(
                mm_per_px_x=-0.5,
                mm_per_px_y=1.0,
                calibration_method="two_point_reference",
                confidence=1.0,
            )
        # Invalid confidence
        with pytest.raises(ValidationError):
            ScaleResult(
                mm_per_px_x=1.0,
                mm_per_px_y=1.0,
                calibration_method="two_point_reference",
                confidence=1.5,
            )
        with pytest.raises(ValidationError):
            ScaleResult(
                mm_per_px_x=1.0,
                mm_per_px_y=1.0,
                calibration_method="two_point_reference",
                confidence=-0.1,
            )


# ===========================================================================
# 2. Scenario A: Tailoring Text Parser (F04) & Edge Cases E06, E07
# ===========================================================================


class TestTailoringTextParser:
    @pytest.mark.parametrize(
        "text, expected",
        [
            (
                "Fuelle superior 13 cm x 35 cm (x1)",
                {
                    "piece_name": "Fuelle superior",
                    "width_cm": 13.0,
                    "height_cm": 35.0,
                    "quantity": 1,
                    "has_fold": False,
                    "has_elastic": False,
                },
            ),
            (
                "Frente 12,5 cm x 11 cm - doblez (x1)",
                {
                    "piece_name": "Frente",
                    "width_cm": 12.5,
                    "height_cm": 11.0,
                    "quantity": 1,
                    "has_fold": True,
                    "has_elastic": False,
                },
            ),
            (
                "Visera 20 cm x 11 cm (x2)",
                {
                    "piece_name": "Visera",
                    "width_cm": 20.0,
                    "height_cm": 11.0,
                    "quantity": 2,
                    "has_fold": False,
                    "has_elastic": False,
                },
            ),
            (
                "Cubre boca 28 cm x 14 cm - elastizar arriba y abajo (x1)",
                {
                    "piece_name": "Cubre boca",
                    "width_cm": 28.0,
                    "height_cm": 14.0,
                    "quantity": 1,
                    "has_fold": False,
                    "has_elastic": True,
                },
            ),
            (
                "Lateral 10 cm x 20 cm (x2)",
                {
                    "piece_name": "Lateral",
                    "width_cm": 10.0,
                    "height_cm": 20.0,
                    "quantity": 2,
                    "has_fold": False,
                    "has_elastic": False,
                },
            ),
            (
                "Orejera Compuesta 25 cm x 19,5 cm (x2)",
                {
                    "piece_name": "Orejera Compuesta",
                    "width_cm": 25.0,
                    "height_cm": 19.5,
                    "quantity": 2,
                    "has_fold": False,
                    "has_elastic": False,
                },
            ),
        ],
    )
    def test_parse_tailoring_text_presets(self, text, expected):
        res = parse_tailoring_text(text)
        assert res["piece_name"] == expected["piece_name"]
        assert res["width_cm"] == pytest.approx(expected["width_cm"], abs=1e-4)
        assert res["height_cm"] == pytest.approx(expected["height_cm"], abs=1e-4)
        assert res["quantity"] == expected["quantity"]
        assert res["has_fold"] is expected["has_fold"]
        assert res["has_elastic"] is expected["has_elastic"]

    # Edge Case E06: Multiple 'x' characters in text
    @pytest.mark.parametrize(
        "text, exp_w, exp_h, exp_q",
        [
            ("Visera 20 cm x 11 cm (x2)", 20.0, 11.0, 2),
            ("Visera 20x11 x2", 20.0, 11.0, 2),
            ("Fuelle superior 13 x 35 x1", 13.0, 35.0, 1),
            ("Visera 20 cm x 11 cm x 2", 20.0, 11.0, 2),
            ("Visera (x2) 20 cm x 11 cm", 20.0, 11.0, 2),
            ("Visera 20 x 11 cm - cut 2", 20.0, 11.0, 2),
            ("Visera 20 × 11 cm (2x)", 20.0, 11.0, 2),
        ],
    )
    def test_parse_tailoring_text_e06_multi_x(self, text, exp_w, exp_h, exp_q):
        res = parse_tailoring_text(text)
        assert res["width_cm"] == pytest.approx(exp_w, abs=1e-4)
        assert res["height_cm"] == pytest.approx(exp_h, abs=1e-4)
        assert res["quantity"] == exp_q

    # Edge Case E07: Comma decimals, missing units, single dimensions
    def test_parse_tailoring_text_e07_comma_and_units(self):
        r1 = parse_tailoring_text("12,5 cm x 11 cm")
        assert r1["width_cm"] == 12.5 and r1["height_cm"] == 11.0

        r2 = parse_tailoring_text("19,5")
        assert r2["width_cm"] == 19.5 and r2["height_cm"] is None

        r3 = parse_tailoring_text("19.5 cm")
        assert r3["width_cm"] == 19.5 and r3["height_cm"] is None

        r4 = parse_tailoring_text("15 in x 4 in")
        assert r4["width_cm"] == pytest.approx(38.1, abs=1e-2)
        assert r4["height_cm"] == pytest.approx(10.16, abs=1e-2)

        r5 = parse_tailoring_text("150 mm x 100 mm")
        assert r5["width_cm"] == pytest.approx(15.0, abs=1e-2)
        assert r5["height_cm"] == pytest.approx(10.0, abs=1e-2)

        r6 = parse_tailoring_text("20 x 11")
        assert r6["width_cm"] == 20.0 and r6["height_cm"] == 11.0

        r7 = parse_tailoring_text("12,5 x 11 cm")
        assert r7["width_cm"] == 12.5 and r7["height_cm"] == 11.0

    def test_parse_tailoring_text_multilingual_keywords(self):
        pl = parse_tailoring_text("Przód 12,5 cm x 11 cm zgięcie (x1)")
        assert pl["has_fold"] is True and pl["width_cm"] == 12.5

        en = parse_tailoring_text("Front 12.5 cm x 11 cm fold cut 2")
        assert en["has_fold"] is True and en["quantity"] == 2

        el = parse_tailoring_text("Maska 28 cm x 14 cm ściągacz")
        assert el["has_elastic"] is True

        es = parse_tailoring_text("Pieza 15 cm x 10 cm tela exterior")
        assert "tela exterior" in es["fabric_note"].lower()


# ===========================================================================
# 3. Scenario A: Anisotropic Calibration (F05) & Edge Case E08
# ===========================================================================


class TestAnisotropicCalibration:
    def test_compute_anisotropic_scale(self):
        res_iso = compute_anisotropic_scale(250.0, 100.0, 25.0, 10.0)
        assert res_iso.mm_per_px_x == pytest.approx(1.0, abs=1e-4)
        assert res_iso.mm_per_px_y == pytest.approx(1.0, abs=1e-4)
        assert res_iso.is_isotropic is True
        assert res_iso.calibration_method == "dimension_anisotropic"
        assert res_iso.confidence == 1.0

        res_aniso = compute_anisotropic_scale(250.0, 100.0, 25.0, 20.0)
        assert res_aniso.mm_per_px_x == pytest.approx(1.0, abs=1e-4)
        assert res_aniso.mm_per_px_y == pytest.approx(2.0, abs=1e-4)
        assert res_aniso.is_isotropic is False

    def test_compute_anisotropic_scale_validation(self):
        with pytest.raises(ValueError, match="Pixel dimensions must be strictly positive"):
            compute_anisotropic_scale(0.0, 100.0, 25.0, 10.0)

        with pytest.raises(ValueError, match="Target dimensions must be strictly positive"):
            compute_anisotropic_scale(100.0, 100.0, -5.0, 10.0)

    def test_calibrate_piece_by_dimensions_success(self):
        frente = build_frente()
        calibrated = calibrate_piece_by_dimensions(frente, target_width_cm=15.0, target_height_cm=12.0)
        assert calibrated.target_width_cm == 15.0
        assert calibrated.target_height_cm == 12.0
        assert calibrated.cut_contour is None

        # Verify exact bounding box dimensions match target in mm
        w_mm, h_mm = get_contour_dimensions_mm(calibrated.outer_contour)
        assert pytest.approx(w_mm, abs=1e-2) == 150.0
        assert pytest.approx(h_mm, abs=1e-2) == 120.0

        # Verify edge attributes and fold edge preserved
        assert calibrated.has_fold is True
        assert calibrated.fold_segment_index is not None
        assert calibrated.outer_contour.segments[calibrated.fold_segment_index].edge_role == "fold"

    def test_calibrate_piece_by_dimensions_e08_degeneracy(self):
        frente = build_frente()
        # Non-positive target dimensions
        with pytest.raises(ValueError, match="strictly positive"):
            calibrate_piece_by_dimensions(frente, 0.0, 10.0)
        with pytest.raises(ValueError, match="strictly positive"):
            calibrate_piece_by_dimensions(frente, 10.0, -1.0)

        # Degenerate horizontal line
        flat_segs = [PathSegment.line((0, 0), (100, 0))]
        flat_piece = PatternPiece(
            id="flat",
            name="Flat",
            target_width_cm=10.0,
            target_height_cm=5.0,
            outer_contour=Contour(segments=flat_segs, is_closed=False),
        )
        with pytest.raises(ValueError, match="zerowe wymiary konturu"):
            calibrate_piece_by_dimensions(flat_piece, 10.0, 5.0)

        # Empty segments
        empty_piece = PatternPiece(
            id="empty",
            name="Empty",
            target_width_cm=10.0,
            target_height_cm=5.0,
            outer_contour=Contour(segments=[], is_closed=False),
        )
        with pytest.raises(ValueError, match="has no contour segments"):
            calibrate_piece_by_dimensions(empty_piece, 10.0, 5.0)


# ===========================================================================
# 4. Scenario B: Two-Point Reference Calibration (F01) & Edge Cases E01, E02, E03
# ===========================================================================


class TestTwoPointCalibration:
    def test_calibrate_from_two_points_benchmark_e03(self):
        """E03: 250 px separated with declared 25.0 cm yields 1.0000 mm/px and scales 250x100 px contour."""
        p1 = (0.0, 0.0)
        p2 = (250.0, 0.0)
        res = calibrate_from_two_points(p1, p2, real_distance_cm=25.0)

        assert res.is_isotropic is True
        assert res.calibration_method == "two_point_reference"
        assert res.confidence == 1.0
        assert pytest.approx(res.mm_per_px, abs=1e-4) == 1.0000
        assert pytest.approx(res.mm_per_px_x, abs=1e-4) == 1.0000
        assert pytest.approx(res.mm_per_px_y, abs=1e-4) == 1.0000

        # Scale test contour 250 x 100 px
        box_contour = Contour.from_polyline(
            [(0, 0), (250, 0), (250, 100), (0, 100)], is_closed=True
        )
        scaled = scale_contour(box_contour, res)
        w_mm, h_mm = get_contour_dimensions_mm(scaled)
        assert pytest.approx(w_mm, abs=0.05) == 250.0
        assert pytest.approx(h_mm, abs=0.05) == 100.0

    def test_calibrate_from_two_points_point2d_objects(self):
        p1 = Point2D(x=10.0, y=20.0)
        p2 = Point2D(x=260.0, y=20.0)
        res = calibrate_from_two_points(p1, p2, real_distance_cm=25.0)
        assert pytest.approx(res.mm_per_px, abs=1e-4) == 1.0

    def test_calibrate_from_two_points_diagonal_and_symmetry(self):
        """Rotational invariance: 300x400 diagonal gives 500 px distance."""
        p1 = (100.0, 100.0)
        p2 = (400.0, 500.0)
        res_fwd = calibrate_from_two_points(p1, p2, real_distance_cm=50.0)
        res_rev = calibrate_from_two_points(p2, p1, real_distance_cm=50.0)

        assert pytest.approx(res_fwd.mm_per_px, abs=1e-4) == 1.0000
        assert pytest.approx(res_rev.mm_per_px, abs=1e-4) == 1.0000
        assert res_fwd.metadata["pixel_distance"] == 500.0

    def test_calibrate_from_two_points_subpixel_coordinates(self):
        p1 = (12.345, 67.890)
        p2 = (112.345, 67.890)
        res = calibrate_from_two_points(p1, p2, real_distance_cm=10.0)
        assert pytest.approx(res.mm_per_px, abs=1e-4) == 1.0000

    def test_calibrate_from_two_points_zero_distance_error_e01(self):
        """E01: Coincident points raise ValueError."""
        with pytest.raises(
            ValueError, match="Pixel distance between reference points must be greater than zero"
        ):
            calibrate_from_two_points((100, 100), (100, 100), real_distance_cm=10.0)

        with pytest.raises(
            ValueError, match="Pixel distance between reference points must be greater than zero"
        ):
            calibrate_from_two_points((0, 0), (1e-7, 0), real_distance_cm=10.0)

    @pytest.mark.parametrize("invalid_cm", [0.0, -1.0, -0.001, float("nan"), float("inf")])
    def test_calibrate_from_two_points_invalid_real_distance_e02(self, invalid_cm):
        """E02: Non-positive or non-finite real distance raises ValueError."""
        with pytest.raises(
            ValueError, match="Declared real-world distance must be strictly positive"
        ):
            calibrate_from_two_points((0, 0), (100, 0), real_distance_cm=invalid_cm)

    def test_calibrate_from_two_points_invalid_input_types(self):
        with pytest.raises(TypeError):
            calibrate_from_two_points("invalid", (100, 0), real_distance_cm=10.0)
        with pytest.raises(ValueError):
            calibrate_from_two_points((100,), (100, 200), real_distance_cm=10.0)
        with pytest.raises(ValueError, match="finite real numbers"):
            calibrate_from_two_points((float("nan"), 0), (100, 0), real_distance_cm=10.0)

    def test_scale_contour_preserves_semantics(self):
        seg = PathSegment.line((0, 0), (100, 0), seam_allowance_mm=12.0, edge_role="fold")
        c = Contour(segments=[seg], is_closed=False)
        scale = ScaleResult.create_isotropic(mm_per_px=2.0, method="two_point_reference", confidence=1.0)
        scaled = scale_contour(c, scale)
        assert scaled.segments[0].start.x == 0.0
        assert scaled.segments[0].end.x == 200.0
        assert scaled.segments[0].seam_allowance_mm == 12.0
        assert scaled.segments[0].edge_role == "fold"


# ===========================================================================
# 5. Scenario C: Calibration Square Detection (F02) & Edge Cases E04, E05
# ===========================================================================


class TestCalibrationSquareDetection:
    def _create_image_with_square(
        self,
        size_px: int = 200,
        img_size: int = 500,
        angle_deg: float = 0.0,
        center: tuple[int, int] = (250, 250),
    ) -> np.ndarray:
        """Helper to create a synthetic grayscale image with a black square on white background."""
        img = np.ones((img_size, img_size), dtype=np.uint8) * 255
        cx, cy = center
        half = size_px / 2.0
        corners = np.array(
            [
                [-half, -half],
                [half, -half],
                [half, half],
                [-half, half],
            ],
            dtype=np.float32,
        )

        rad = math.radians(angle_deg)
        rot_mat = np.array(
            [
                [math.cos(rad), -math.sin(rad)],
                [math.sin(rad), math.cos(rad)],
            ],
            dtype=np.float32,
        )
        rotated_corners = corners @ rot_mat.T + np.array([cx, cy], dtype=np.float32)
        pts = np.round(rotated_corners).astype(np.int32)
        cv2.fillPoly(img, [pts], color=0)
        return img

    def test_detect_calibration_square_upright(self):
        """200x200 px square with target 50 mm -> 0.25 mm/px."""
        img = self._create_image_with_square(size_px=200, angle_deg=0.0)
        res = detect_calibration_square(img, target_square_mm=50.0)

        assert res is not None
        assert res.calibration_method == "calibration_square"
        assert res.confidence == 0.95
        assert res.is_isotropic is True
        assert pytest.approx(res.mm_per_px, rel=0.01) == 0.2500

    def test_detect_calibration_square_rotated_45deg_e05(self):
        """E05: Square rotated by 45 deg -> accurate scale recovery within 1.0% error."""
        img = self._create_image_with_square(size_px=200, angle_deg=45.0)
        res = detect_calibration_square(img, target_square_mm=50.0)

        assert res is not None
        assert res.calibration_method == "calibration_square"
        true_scale = 50.0 / 200.0  # 0.25 mm/px
        error = abs(res.mm_per_px - true_scale) / true_scale
        assert error < 0.01  # < 1.0% error requirement

    def test_detect_calibration_square_rotated_30deg(self):
        img = self._create_image_with_square(size_px=200, angle_deg=30.0)
        res = detect_calibration_square(img, target_square_mm=50.0)

        assert res is not None
        true_scale = 50.0 / 200.0
        error = abs(res.mm_per_px - true_scale) / true_scale
        assert error < 0.01

    def test_reject_rectangle_non_square_e04(self):
        """E04: Rejects non-square rectangles (aspect ratio > 1.05)."""
        img = np.ones((500, 500), dtype=np.uint8) * 255
        # 200 x 150 px rectangle (aspect ratio = 1.33)
        cv2.rectangle(img, (150, 150), (350, 300), color=0, thickness=-1)
        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is None

    def test_reject_trapezoid_e04(self):
        """E04: Rejects trapezoid with non-orthogonal angles."""
        img = np.ones((500, 500), dtype=np.uint8) * 255
        # Non-orthogonal quad (trapezoid)
        pts = np.array([[100, 100], [400, 100], [350, 300], [150, 300]], dtype=np.int32)
        cv2.fillPoly(img, [pts], color=0)
        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is None

    def test_detect_hollow_square_with_text(self):
        """Square with outline border and text inside."""
        img = np.ones((500, 500), dtype=np.uint8) * 255
        # Draw 200x200 outline square with thickness 4
        cv2.rectangle(img, (150, 150), (350, 350), color=0, thickness=4)
        cv2.putText(
            img,
            "5x5 cm",
            (200, 260),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            color=0,
            thickness=2,
        )
        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is not None
        assert pytest.approx(res.mm_per_px, rel=0.02) == 0.25

    def test_detect_square_invalid_inputs(self):
        with pytest.raises(ValueError, match="strictly positive"):
            detect_calibration_square(np.zeros((100, 100)), target_square_mm=-10.0)

        assert detect_calibration_square(np.ones((100, 100)) * 255) is None
        assert detect_calibration_square(None) is None
        assert detect_calibration_square("non_existent_file.png") is None


# ===========================================================================
# 6. DPI Metadata Extraction (F03)
# ===========================================================================


class TestDpiMetadataExtraction:
    def _create_png_bytes_with_phys(self, ppm_x: int, ppm_y: int, unit: int = 1) -> bytes:
        """Synthesizes a minimal valid PNG with a pHYs chunk."""
        signature = b"\x89PNG\r\n\x1a\n"
        # IHDR chunk: 1x1 image, 8-bit grayscale
        ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
        ihdr_crc = struct.pack(">I", 0x4CE7C474)  # placeholder crc
        ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + ihdr_crc

        # pHYs chunk
        phys_data = struct.pack(">IIB", ppm_x, ppm_y, unit)
        phys_crc = struct.pack(">I", 0x12345678)
        phys = struct.pack(">I", len(phys_data)) + b"pHYs" + phys_data + phys_crc

        return signature + ihdr + phys

    def test_png_phys_extraction_300dpi(self):
        # 300 DPI = 300 / 0.0254 = ~11811 pixels per meter
        data = self._create_png_bytes_with_phys(11811, 11811, unit=1)
        res = extract_dpi_scale(data)

        assert res is not None
        assert res.calibration_method == "dpi_metadata"
        assert res.confidence == 0.85
        assert res.is_isotropic is True
        assert pytest.approx(res.mm_per_px, abs=1e-4) == 25.4 / 300.0

    def test_png_phys_unit_zero_ignored(self):
        # unit=0 means aspect ratio only, not physical meters
        data = self._create_png_bytes_with_phys(11811, 11811, unit=0)
        assert extract_dpi_scale(data) is None

    def test_jpeg_jfif_extraction_300dpi(self):
        # SOI + JFIF APP0 marker (0xFF, 0xE0)
        soi = b"\xff\xd8"
        # JFIF payload: "JFIF\x00", ver 1.1, units=1 (inch), Xdensity=300, Ydensity=300, thumb=0,0
        payload = b"JFIF\x00\x01\x01\x01" + struct.pack(">HHBB", 300, 300, 0, 0)
        app0_len = len(payload) + 2
        app0 = b"\xff\xe0" + struct.pack(">H", app0_len) + payload
        data = soi + app0

        res = extract_dpi_scale(data)
        assert res is not None
        assert res.calibration_method == "dpi_metadata"
        assert pytest.approx(res.mm_per_px, abs=1e-4) == 25.4 / 300.0

    def test_jpeg_exif_extraction_300dpi(self):
        # SOI + EXIF APP1 marker
        soi = b"\xff\xd8"
        # TIFF little-endian
        # IFD0 at offset 8 with 3 tags: XResolution (0x011A), YResolution (0x011B), Unit (0x0128)
        tiff_header = b"II\x2a\x00\x08\x00\x00\x00"  # magic 42, offset 8
        # IFD0: 3 entries
        num_entries = struct.pack("<H", 3)
        # Entry 1: 0x011A, type 5 (RATIONAL), count 1, offset 8 + 2 + 3*12 + 4 = 50
        entry1 = struct.pack("<HHII", 0x011A, 5, 1, 50)
        # Entry 2: 0x011B, type 5 (RATIONAL), count 1, offset 58
        entry2 = struct.pack("<HHII", 0x011B, 5, 1, 58)
        # Entry 3: 0x0128, type 3 (SHORT), count 1, value 2 (inch)
        entry3 = struct.pack("<HHII", 0x0128, 3, 1, 2)
        next_ifd = struct.pack("<I", 0)

        # Rational values: 300/1
        rat_x = struct.pack("<II", 300, 1)
        rat_y = struct.pack("<II", 300, 1)

        tiff = tiff_header + num_entries + entry1 + entry2 + entry3 + next_ifd + rat_x + rat_y
        exif_payload = b"Exif\x00\x00" + tiff
        app1_len = len(exif_payload) + 2
        app1 = b"\xff\xe1" + struct.pack(">H", app1_len) + exif_payload
        data = soi + app1

        res = extract_dpi_scale(data)
        assert res is not None
        assert res.calibration_method == "dpi_metadata"
        assert pytest.approx(res.mm_per_px, abs=1e-4) == 25.4 / 300.0

    def test_sample_image_gorro_trapper_dpi(self):
        """Reference sample image samples/gorro_trapper.png has no physical DPI metadata."""
        path = Path("samples/gorro_trapper.png")
        if path.is_file():
            assert extract_dpi_scale(path) is None


# ===========================================================================
# 7. Fallback Default & Auto-Detect Cascade
# ===========================================================================


class TestFallbackAndAutoDetect:
    def test_fallback_default_scale(self):
        res = get_fallback_scale(default_dpi=96.0)
        assert res.calibration_method == "fallback_default"
        assert res.confidence == 0.10
        assert res.is_isotropic is True
        assert pytest.approx(res.mm_per_px, abs=1e-4) == 25.4 / 96.0
        assert res.metadata["default_dpi"] == 96.0

        with pytest.raises(ValueError, match="strictly positive"):
            get_fallback_scale(default_dpi=0.0)

    def test_auto_detect_cascade_prefers_square(self):
        # Create image with square
        img = np.ones((500, 500), dtype=np.uint8) * 255
        cv2.rectangle(img, (150, 150), (350, 350), color=0, thickness=-1)

        res = auto_detect_scale(img, target_square_mm=50.0)
        assert res.calibration_method == "calibration_square"
        assert res.confidence == 0.95

    def test_auto_detect_cascade_prefers_dpi_over_fallback(self):
        # Blank image without square
        img = np.ones((100, 100), dtype=np.uint8) * 255
        # DPI bytes for 300 DPI
        data = TestDpiMetadataExtraction()._create_png_bytes_with_phys(11811, 11811, unit=1)

        res = auto_detect_scale(img, image_path=data)
        assert res.calibration_method == "dpi_metadata"
        assert res.confidence == 0.85
        assert pytest.approx(res.mm_per_px, abs=1e-4) == 25.4 / 300.0

    def test_auto_detect_cascade_falls_back_on_gorro_trapper(self):
        path = Path("samples/gorro_trapper.png")
        if path.is_file():
            img = cv2.imread(str(path))
            res = auto_detect_scale(img, image_path=path)
            assert res.calibration_method == "fallback_default"
            assert res.confidence == 0.10
            assert pytest.approx(res.mm_per_px, abs=1e-4) == 25.4 / 96.0
