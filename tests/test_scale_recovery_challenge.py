"""
Empirical Challenge Tests for Milestone 1: Scale Recovery Engine (src/digitizer/scale_recovery.py).

Adversarial stress harness covering:
1. Mathematical boundary conditions for two-point calibration (extreme coords, tiny distances, large numbers, precision).
2. Binary image header robustness (truncated chunks, corrupt buffers, Big-Endian EXIF, unusual DPI units).
3. Tailoring semantics & attribute preservation in anisotropic calibration (multi-segment, edge_role, fold, notches, internal lines).
4. Tailoring text parser boundary conditions and formatting artifacts.
5. Calibration square detection under geometric deformations and edge cases.
"""

import math
import struct
import numpy as np
import pytest

from src.core.contract import (
    Contour,
    InternalLine,
    Notch,
    PathSegment,
    PatternPiece,
    Point2D,
)
from src.digitizer.scale_recovery import (
    ScaleResult,
    auto_detect_scale,
    calibrate_from_two_points,
    calibrate_piece_by_dimensions,
    detect_calibration_square,
    extract_dpi_scale,
    get_fallback_scale,
    parse_tailoring_text,
    scale_contour,
)


# ===========================================================================
# 1. Two-Point Calibration Boundary & Precision Tests
# ===========================================================================


class TestTwoPointCalibrationBoundary:
    """Mathematical boundary and edge case testing for calibrate_from_two_points."""

    def test_extreme_large_finite_coordinates(self):
        """Points with large coordinates within IEEE 754 precision."""
        p1 = (1e12, 1e12)
        p2 = (1e12 + 100.0, 1e12)
        res = calibrate_from_two_points(p1, p2, real_distance_cm=10.0)
        assert res.is_isotropic is True
        assert pytest.approx(res.mm_per_px, rel=1e-5) == 1.0

    def test_negative_coordinates(self):
        """Points located entirely in negative coordinate space."""
        p1 = (-500.0, -1000.0)
        p2 = (-200.0, -600.0)
        # dx = 300, dy = 400, dist_px = 500
        res = calibrate_from_two_points(p1, p2, real_distance_cm=50.0)
        assert pytest.approx(res.mm_per_px) == 1.0

    def test_tiny_pixel_distance_above_epsilon(self):
        """Distance slightly above minimum epsilon threshold (1e-6)."""
        p1 = (0.0, 0.0)
        p2 = (1e-5, 0.0)
        res = calibrate_from_two_points(p1, p2, real_distance_cm=1.0)
        assert res.mm_per_px > 0.0
        assert math.isfinite(res.mm_per_px)

    def test_sub_epsilon_pixel_distance_raises_value_error(self):
        """Distance below 1e-6 must cleanly raise ValueError."""
        p1 = (0.0, 0.0)
        p2 = (5e-7, 0.0)
        with pytest.raises(ValueError, match="Pixel distance between reference points"):
            calibrate_from_two_points(p1, p2, real_distance_cm=10.0)

    def test_numpy_array_input_support(self):
        """NumPy arrays are standard in CV pipelines and should be accepted as PointInput."""
        p1 = np.array([10.0, 20.0])
        p2 = np.array([110.0, 20.0])
        # Known bug: _normalize_point raises TypeError for np.ndarray
        res = calibrate_from_two_points(p1, p2, real_distance_cm=10.0)
        assert pytest.approx(res.mm_per_px) == 1.0

    def test_infinite_or_overflowing_real_distance(self):
        """Huge real_distance_cm that overflows float to inf should not produce inf scale factor."""
        p1 = (0.0, 0.0)
        p2 = (100.0, 0.0)
        # Known bug: 1e308 * 10.0 overflows to inf, creating ScaleResult(mm_per_px=inf)
        with pytest.raises((ValueError, OverflowError)):
            res = calibrate_from_two_points(p1, p2, real_distance_cm=1e308)
            assert math.isfinite(res.mm_per_px)

    def test_scale_contour_extreme_coordinates(self):
        """Verify scale_contour maintains numerical validity under scaling."""
        seg = PathSegment.line((1e5, 1e5), (1e5 + 100, 1e5 + 50))
        c = Contour(segments=[seg], is_closed=False)
        scale = ScaleResult.create_isotropic(mm_per_px=0.5, method="two_point_reference", confidence=1.0)
        scaled_c = scale_contour(c, scale)
        assert pytest.approx(scaled_c.segments[0].start.x) == 5e4
        assert pytest.approx(scaled_c.segments[0].end.x) == 5e4 + 50.0


# ===========================================================================
# 2. Binary Header Extraction Robustness & Fuzzing
# ===========================================================================


class TestBinaryHeaderRobustness:
    """Stress tests for PNG pHYs, JPEG JFIF, and EXIF resolution parsing."""

    def test_png_phys_truncated_chunk_data_raises_no_crash(self):
        """
        Truncated PNG pHYs chunk data must return None rather than crashing with unhandled struct.error.
        Known bug: struct.unpack('>IIB', cdata[:9]) crashes when len(cdata) < 9.
        """
        # Valid PNG signature + chunk length 9 + chunk type 'pHYs' + only 4 bytes of payload (truncated)
        corrupt_png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 9) + b"pHYs1234"
        res = extract_dpi_scale(corrupt_png)
        assert res is None

    def test_png_phys_zero_unit_aspect_ratio_only(self):
        """PNG pHYs with unit=0 specifies aspect ratio, not physical units; should return None."""
        chunk_data = struct.pack(">IIB", 3780, 3780, 0)
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(chunk_data)) + b"pHYs" + chunk_data
        assert extract_dpi_scale(png) is None

    def test_png_phys_zero_ppux_rejected(self):
        """PNG pHYs with ppux=0 must be rejected to prevent ZeroDivisionError."""
        chunk_data = struct.pack(">IIB", 0, 3780, 1)
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(chunk_data)) + b"pHYs" + chunk_data
        assert extract_dpi_scale(png) is None

    def test_big_endian_exif_resolution_unit(self):
        """
        Standard Big-Endian (MM) TIFF/EXIF ResolutionUnit (tag 0x0128).
        TIFF 6.0 specifies that 16-bit SHORT values are left-justified in the 4-byte value field.
        Known bug: Big-endian unpacks 0x00020000 as 131072, failing the unit == 2 check.
        """
        bo = ">"
        header = b"MM" + struct.pack(bo + "HI", 42, 8)
        num_entries = 3

        # Entry 1: XResolution (0x011A), RATIONAL (ftype 5), count 1, offset 46
        entry_x = struct.pack(bo + "HHII", 0x011A, 5, 1, 46)
        # Entry 2: YResolution (0x011B), RATIONAL (ftype 5), count 1, offset 54
        entry_y = struct.pack(bo + "HHII", 0x011B, 5, 1, 54)
        # Entry 3: ResolutionUnit (0x0128), SHORT (ftype 3), count 1, value 2 (Inch, left-justified)
        entry_unit = struct.pack(bo + "HH", 0x0128, 3) + struct.pack(bo + "I", 1) + struct.pack(bo + "H", 2) + b"\x00\x00"

        # Rational values: 300 / 1
        val_x = struct.pack(bo + "II", 300, 1)
        val_y = struct.pack(bo + "II", 300, 1)

        tiff = header + struct.pack(bo + "H", num_entries) + entry_x + entry_y + entry_unit + val_x + val_y
        app1_data = b"Exif\x00\x00" + tiff
        jpeg = b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1_data) + 2) + app1_data

        res = extract_dpi_scale(jpeg)
        assert res is not None, "Valid Big-Endian EXIF with left-justified SHORT ResolutionUnit must be parsed"
        assert pytest.approx(res.metadata["dpi_x"]) == 300.0

    def test_exif_zero_denominator_rejected(self):
        """EXIF with zero denominator in rational resolution must not cause ZeroDivisionError."""
        bo = "<"
        header = b"II" + struct.pack(bo + "HI", 42, 8)
        entry_x = struct.pack(bo + "HHII", 0x011A, 5, 1, 34)
        entry_y = struct.pack(bo + "HHII", 0x011B, 5, 1, 42)
        val_zero_den = struct.pack(bo + "II", 300, 0)
        tiff = header + struct.pack(bo + "H", 2) + entry_x + entry_y + val_zero_den + val_zero_den
        app1 = b"Exif\x00\x00" + tiff
        jpeg = b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
        assert extract_dpi_scale(jpeg) is None


# ===========================================================================
# 3. Anisotropic Calibration Attribute & Semantics Preservation Tests
# ===========================================================================


class TestAnisotropicCalibrationPreservation:
    """Stress tests verifying attribute preservation during anisotropic scaling."""

    @pytest.fixture
    def complex_pattern_piece(self) -> PatternPiece:
        """Creates a complex multi-segment piece with mixed edge roles, seam allowances, internal lines, and notches."""
        # Multi-segment boundary: 100 x 50 mm
        seg0 = PathSegment.line((0, 0), (100, 0), seam_allowance_mm=0.0, edge_role="fold")
        seg1 = PathSegment.bezier(
            (100, 0), (100, 25), (100, 35), (100, 50), seam_allowance_mm=15.0, edge_role="elastic"
        )
        seg2 = PathSegment.line((100, 50), (0, 50), seam_allowance_mm=10.0, edge_role="seam")
        seg3 = PathSegment.line((0, 50), (0, 0), seam_allowance_mm=20.0, edge_role="hem")
        contour = Contour(segments=[seg0, seg1, seg2, seg3], is_closed=True)

        # Internal fold line
        int_seg = PathSegment.line((20, 10), (20, 40), edge_role="fold", seam_allowance_mm=0.0)
        int_line = InternalLine(
            id="fold_axis", label="center_fold", line_type="fold", contour=Contour(segments=[int_seg], is_closed=False)
        )

        # Alignment notch at midpoint of bottom edge
        notch = Notch(id="notch_1", position=Point2D(x=50.0, y=0.0))

        return PatternPiece(
            id="complex_piece",
            name="Complex Trapper Piece",
            target_width_cm=10.0,
            target_height_cm=5.0,
            outer_contour=contour,
            internal_lines=[int_line],
            notches=[notch],
            seam_allowance_mm=10.0,
        )

    def test_outer_contour_attributes_preserved(self, complex_pattern_piece):
        """Verify outer contour edge_role and seam_allowance_mm are preserved after anisotropic calibration."""
        calibrated = calibrate_piece_by_dimensions(complex_pattern_piece, target_width_cm=20.0, target_height_cm=10.0)
        segs = calibrated.outer_contour.segments
        assert len(segs) == 4

        assert segs[0].edge_role == "fold"
        assert segs[0].seam_allowance_mm == 0.0

        assert segs[1].edge_role == "elastic"
        assert segs[1].seam_allowance_mm == 15.0

        assert segs[2].edge_role == "seam"
        assert segs[2].seam_allowance_mm == 10.0

        assert segs[3].edge_role == "hem"
        assert segs[3].seam_allowance_mm == 20.0

    def test_internal_lines_attributes_preserved(self, complex_pattern_piece):
        """
        Verify that internal lines preserve segment edge_role and seam_allowance_mm after calibration.
        Known bug: transform_contour drops edge_role and seam_allowance_mm on internal lines.
        """
        calibrated = calibrate_piece_by_dimensions(complex_pattern_piece, target_width_cm=20.0, target_height_cm=10.0)
        assert len(calibrated.internal_lines) == 1
        int_seg = calibrated.internal_lines[0].contour.segments[0]
        assert int_seg.edge_role == "fold", f"Expected edge_role='fold', got {int_seg.edge_role!r}"
        assert int_seg.seam_allowance_mm == 0.0, f"Expected seam_allowance_mm=0.0, got {int_seg.seam_allowance_mm}"

    def test_notches_transformed_coordinately(self, complex_pattern_piece):
        """
        Verify that notches are geometrically scaled and translated along with the piece.
        Known bug: calibrate_piece_anisotropic completely ignores notches, leaving them at unscaled coordinates.
        """
        calibrated = calibrate_piece_by_dimensions(complex_pattern_piece, target_width_cm=20.0, target_height_cm=10.0)
        assert len(calibrated.notches) == 1
        notch = calibrated.notches[0]
        # Piece was scaled 2x in X (10 cm -> 20 cm). Notch originally at x=50.0 should be at x=100.0
        assert pytest.approx(notch.position.x, abs=3.0) == 100.0, (
            f"Notch position x must be scaled with piece; expected ~100.0 mm, got {notch.position.x}"
        )


# ===========================================================================
# 4. Tailoring Text Parser Edge Cases
# ===========================================================================


class TestTailoringTextParserChallenge:
    """Stress testing multilingual text parsing for tailoring patterns."""

    def test_piece_name_not_garbled_with_multiplier(self):
        """
        Verify piece_name does not retain unbalanced opening parentheses when multiplier is extracted.
        Known bug: 'Visera (x2) 20 x 11 cm' yields piece_name 'Visera (x2'.
        """
        res = parse_tailoring_text("Visera (x2) 20 x 11 cm")
        assert res["quantity"] == 2
        assert res["piece_name"] == "Visera"

    def test_empty_and_whitespace_only_text(self):
        """Empty or whitespace strings should return default empty structure without crashing."""
        res = parse_tailoring_text("   \t\n  ")
        assert res["piece_name"] is None
        assert res["width_cm"] is None
        assert res["height_cm"] is None
        assert res["quantity"] == 1
        assert res["has_fold"] is False

    def test_comma_decimals_multilingual(self):
        """Verify Spanish/Polish comma decimal notation parsing."""
        res = parse_tailoring_text("Cubre boca 19,5 x 12,5 cm elastizar")
        assert pytest.approx(res["width_cm"]) == 19.5
        assert pytest.approx(res["height_cm"]) == 12.5
        assert res["has_elastic"] is True


# ===========================================================================
# 5. Calibration Square Detector Challenge Tests
# ===========================================================================


class TestCalibrationSquareDetectorChallenge:
    """Stress testing quad detection under boundary conditions."""

    def test_zero_sized_or_single_pixel_image(self):
        """Degenerate zero-size or 1x1 image inputs must safely return None."""
        assert detect_calibration_square(np.zeros((0, 0), dtype=np.uint8)) is None
        assert detect_calibration_square(np.zeros((1, 1), dtype=np.uint8)) is None

    def test_rotated_square_detection(self):
        """A square rotated 45 degrees should still be accurately detected."""
        import cv2

        img = np.full((400, 400), 255, dtype=np.uint8)
        rect = ((200, 200), (100, 100), 45)
        box = cv2.boxPoints(rect).astype(np.int32)
        cv2.drawContours(img, [box], 0, 0, -1)

        res = detect_calibration_square(img, target_square_mm=50.0)
        assert res is not None
        assert pytest.approx(res.mm_per_px, rel=0.05) == 0.5
