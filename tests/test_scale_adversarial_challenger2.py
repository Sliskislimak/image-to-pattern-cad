"""
Adversarial Stress Test Suite - Challenger Iteration 2.
Empirical verification of parse_tailoring_text and scale_recovery edge cases.
"""

import pytest
from src.digitizer.scale_recovery import (
    parse_tailoring_text,
    calibrate_from_two_points,
    compute_anisotropic_scale,
    detect_calibration_square,
    ScaleResult,
)


class TestAdversarialMultipliersWithNotes:
    """Stress tests for trailing multipliers with notes, fabric notes, fold notes, etc."""

    @pytest.mark.parametrize("text,expected_w,expected_h,expected_q,expected_fabric,expected_fold", [
        ("Visera 20 cm x 11 cm x 2 - tela principal", 20.0, 11.0, 2, "tela principal", False),
        ("Visera 20 cm x 11 cm x 2 doblez", 20.0, 11.0, 2, "", True),
        ("Visera 20 cm x 11 cm x 2 (tela exterior)", 20.0, 11.0, 2, "tela exterior", False),
        ("Visera 20 cm x 11 cm x 2 - forro", 20.0, 11.0, 2, "forro", False),
        ("Visera 20 cm x 11 cm x 2, tela principal", 20.0, 11.0, 2, "tela principal", False),
        ("Visera 20 cm x 11 cm x 2 ; corte 2", 20.0, 11.0, 2, "", False),
        ("Frente 12,5 cm x 11 cm x 1 al lomo", 12.5, 11.0, 1, "", True),
        ("Cubre boca 28 cm x 14 cm x 1 - elastizar arriba y abajo", 28.0, 14.0, 1, "", False),
        ("Orejera 25 x 19,5 cm x 2 - elastizar", 25.0, 19.5, 2, "", False),
        ("Visera 20 x 11 cm x 2 (2 szt)", 20.0, 11.0, 2, "", False),
        ("Visera 20 x 11 cm x 2 - 2 szt.", 20.0, 11.0, 2, "", False),
        ("Visera 20 x 11 cm x 4 tela exterior", 20.0, 11.0, 4, "tela exterior", False),
    ])
    def test_trailing_multipliers_with_various_notes(
        self, text, expected_w, expected_h, expected_q, expected_fabric, expected_fold
    ):
        res = parse_tailoring_text(text)
        assert res["width_cm"] == pytest.approx(expected_w, abs=0.01)
        assert res["height_cm"] == pytest.approx(expected_h, abs=0.01)
        assert res["quantity"] == expected_q
        if expected_fabric:
            assert expected_fabric in res["fabric_note"]
        assert res["has_fold"] is expected_fold


class TestAdversarialSpacedMultipliers:
    """Stress tests for spaced multipliers with and without dimensions."""

    @pytest.mark.parametrize("text,expected_name,expected_q", [
        ("Visera x 2", "Visera", 2),
        ("Visera X 2", "Visera", 2),
        ("Visera x 1", "Visera", 1),
        ("Visera x 4", "Visera", 4),
        ("Visera x 12", "Visera", 12),
        ("Visera (x 2)", "Visera", 2),
        ("Visera (2x)", "Visera", 2),
        ("Visera ( 2 )", "Visera", 2),
        ("Visera 2 szt", "Visera", 2),
        ("Visera 2 szt.", "Visera", 2),
        ("Visera 2 pcs", "Visera", 2),
        ("Visera 2 pcs.", "Visera", 2),
        ("Visera cut 2", "Visera", 2),
        ("Visera cortar 2", "Visera", 2),
        ("Visera kroić 2", "Visera", 2),
        ("Visera ilość: 2", "Visera", 2),
        ("Visera cantidad 2", "Visera", 2),
    ])
    def test_spaced_and_keyword_multipliers(self, text, expected_name, expected_q):
        res = parse_tailoring_text(text)
        assert res["quantity"] == expected_q
        assert res["width_cm"] is None
        assert res["height_cm"] is None
        assert res["piece_name"] == expected_name


class TestAdversarialOCRWhitespaceDecimals:
    """Stress tests for OCR whitespace anomalies around decimals."""

    @pytest.mark.parametrize("text,expected_w,expected_h", [
        ("Frente 12 , 5 cm x 11 cm", 12.5, 11.0),
        ("Frente 12 , 5 cm x 11 , 0 cm", 12.5, 11.0),
        ("Frente 12 . 5 cm x 11 . 0 cm", 12.5, 11.0),
        ("Frente 12 , 5 x 11 , 0", 12.5, 11.0),
        ("Frente 12 . 5 x 11 . 0", 12.5, 11.0),
        ("Frente 12, 5 cm x 11, 0 cm", 12.5, 11.0),
        ("Frente 12 ,5 cm x 11 ,0 cm", 12.5, 11.0),
        ("Frente 12. 5 cm x 11. 0 cm", 12.5, 11.0),
        ("Frente 12 .5 cm x 11 .0 cm", 12.5, 11.0),
    ])
    def test_ocr_spaces_in_decimals(self, text, expected_w, expected_h):
        res = parse_tailoring_text(text)
        assert res["width_cm"] == pytest.approx(expected_w, abs=0.01)
        assert res["height_cm"] == pytest.approx(expected_h, abs=0.01)
        assert res["piece_name"] == "Frente"

    @pytest.mark.parametrize("text,expected_val", [
        ("19 , 5 cm", 19.5),
        ("19 . 5 cm", 19.5),
        ("19 , 5", 19.5),
        ("19 . 5", 19.5),
        ("19, 5", 19.5),
        ("19 ,5", 19.5),
        ("19. 5", 19.5),
        ("19 .5", 19.5),
    ])
    def test_ocr_spaces_in_single_decimal(self, text, expected_val):
        res = parse_tailoring_text(text)
        assert res["width_cm"] == pytest.approx(expected_val, abs=0.01)
        assert res["height_cm"] is None
        assert res["piece_name"] is None


class TestAdversarialDimensionDelimitersAndUnits:
    """Stress tests for multiplication operators, unit inheritance, and inch conversions."""

    @pytest.mark.parametrize("delim", ["x", "X", "*", "×", "✕", "✖", " by ", " na ", " por "])
    def test_dimension_delimiters(self, delim):
        text = f"Visera 20{delim}11 cm"
        res = parse_tailoring_text(text)
        assert res["width_cm"] == 20.0
        assert res["height_cm"] == 11.0
        assert res["piece_name"] == "Visera"

    @pytest.mark.parametrize("text,expected_w_cm,expected_h_cm", [
        ("130 mm x 350 mm", 13.0, 35.0),
        ("130 mm x 35 cm", 13.0, 35.0),
        ("13 cm x 350 mm", 13.0, 35.0),
        ("10 in x 5 in", 25.4, 12.7),
        ("10 inch x 5 inch", 25.4, 12.7),
        ('10" x 5"', 25.4, 12.7),
        ("10” x 5”", 25.4, 12.7),
        ("20 x 11 cm", 20.0, 11.0),
        ("20 cm x 11", 20.0, 11.0),
    ])
    def test_unit_conversions_and_inheritance(self, text, expected_w_cm, expected_h_cm):
        res = parse_tailoring_text(text)
        assert res["width_cm"] == pytest.approx(expected_w_cm, abs=0.01)
        assert res["height_cm"] == pytest.approx(expected_h_cm, abs=0.01)
        assert res["piece_name"] is None


class TestAdversarialPrefixAndPieceNameIsolation:
    """Stress tests ensuring piece names are isolated cleanly without dimension leakage."""

    @pytest.mark.parametrize("text,expected_name", [
        ("1. Visera 20 x 11 cm", "Visera"),
        ("2 - Lateral 10 x 20 cm", "Lateral"),
        ("3) Frente 12,5 x 11 cm", "Frente"),
        ("Orejera Compuesta 25 x 19,5 cm (x2)", "Orejera Compuesta"),
        ("Cubre boca 28 x 14 cm - doblez", "Cubre boca"),
        ("Fuelle superior: 13 x 35 cm", "Fuelle superior"),
    ])
    def test_piece_name_clean_prefix(self, text, expected_name):
        res = parse_tailoring_text(text)
        assert res["piece_name"] == expected_name

    @pytest.mark.parametrize("dim_only_text", [
        "20 x 11",
        "19,5",
        "12,5cm x 11,0cm",
        "28 cm",
        "130 mm x 350 mm",
        "10 in x 5 in",
    ])
    def test_dimension_only_returns_none_piece_name(self, dim_only_text):
        res = parse_tailoring_text(dim_only_text)
        assert res["piece_name"] is None, f"Expected None for '{dim_only_text}', got {res['piece_name']!r}"
