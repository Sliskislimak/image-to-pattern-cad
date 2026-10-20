"""
Tests for interactive ROI zones (include / exclude) in the Pattern Semantic Digitizer.

Covers: zone validation & rasterization semantics, image masking, the end-to-end pipeline on a
synthetic two-piece sheet, reference-spec auto-detection and the /api/digitize `zones` parameter.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.digitizer.pipeline import DigitizerPipeline, should_use_reference_specs
from src.digitizer.roi import (
    ROIZone,
    apply_roi_to_image,
    draw_zones_overlay,
    parse_zones,
    rasterize_zones,
)
from src.ui.server import create_app

W, H = 400, 300
TAN_BGR = (140, 185, 215)


def _rect_zone(mode: str, x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"mode": mode, "points": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]}


def _two_piece_sheet() -> np.ndarray:
    """White sheet with two tan, black-outlined rectangular pieces (left and right)."""
    img = np.full((H, W, 3), 255, np.uint8)
    for x0 in (30, 230):
        cv2.rectangle(img, (x0, 60), (x0 + 140, 240), TAN_BGR, thickness=-1)
        cv2.rectangle(img, (x0, 60), (x0 + 140, 240), (0, 0, 0), thickness=2)
    return img


@pytest.fixture
def sheet_path(tmp_path: Path) -> Path:
    path = tmp_path / "two_pieces.png"
    cv2.imwrite(str(path), _two_piece_sheet())
    return path


def _pipeline(tmp_path: Path, **kwargs) -> DigitizerPipeline:
    return DigitizerPipeline(
        output_dir=tmp_path / "out",
        export_clo3d=False,
        export_a4=False,
        reference_specs="none",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Zone model & rasterization
# ---------------------------------------------------------------------------


class TestZoneModel:
    def test_requires_three_points(self):
        with pytest.raises(ValidationError):
            ROIZone(mode="include", points=[(0.1, 0.1), (0.9, 0.9)])

    def test_rejects_unknown_mode(self):
        with pytest.raises(ValidationError):
            ROIZone(mode="maybe", points=[(0, 0), (1, 0), (1, 1)])

    def test_rejects_non_finite_points(self):
        with pytest.raises(ValidationError):
            ROIZone(mode="include", points=[(0, 0), (float("nan"), 0), (1, 1)])

    def test_points_are_clamped_to_unit_square(self):
        zone = ROIZone(mode="include", points=[(-0.5, 0.2), (1.7, 0.2), (0.5, 3.0)])
        assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in zone.points)

    def test_parse_zones_accepts_none_and_dicts(self):
        assert parse_zones(None) == []
        zones = parse_zones([_rect_zone("exclude", 0.0, 0.0, 0.5, 0.5)])
        assert len(zones) == 1 and zones[0].mode == "exclude"

    def test_parse_zones_rejects_non_list(self):
        with pytest.raises(ValueError):
            parse_zones("not a list")


class TestRasterization:
    def test_no_zones_allows_whole_image(self):
        assert rasterize_zones([], (H, W, 3)).all()

    def test_include_zone_restricts_search_area(self):
        allowed = rasterize_zones(parse_zones([_rect_zone("include", 0.0, 0.0, 0.5, 1.0)]), (H, W, 3))
        assert allowed[:, : W // 2 - 5].all()
        assert not allowed[:, W // 2 + 5 :].any()

    def test_exclude_only_removes_area_from_whole_image(self):
        allowed = rasterize_zones(parse_zones([_rect_zone("exclude", 0.5, 0.0, 1.0, 1.0)]), (H, W, 3))
        assert allowed[:, : W // 2 - 5].all()
        assert not allowed[:, W // 2 + 5 :].any()

    def test_exclude_cuts_hole_inside_include(self):
        zones = parse_zones(
            [
                _rect_zone("include", 0.0, 0.0, 1.0, 1.0),
                _rect_zone("exclude", 0.4, 0.4, 0.6, 0.6),
            ]
        )
        allowed = rasterize_zones(zones, (H, W, 3))
        assert allowed[10, 10]
        assert not allowed[H // 2, W // 2]

    def test_multiple_include_zones_are_united(self):
        zones = parse_zones(
            [
                _rect_zone("include", 0.0, 0.0, 0.2, 1.0),
                _rect_zone("include", 0.8, 0.0, 1.0, 1.0),
            ]
        )
        allowed = rasterize_zones(zones, (H, W, 3))
        assert allowed[H // 2, 10] and allowed[H // 2, W - 10]
        assert not allowed[H // 2, W // 2]

    def test_zones_leaving_nothing_raise(self):
        zones = parse_zones([_rect_zone("exclude", 0.0, 0.0, 1.0, 1.0)])
        with pytest.raises(ValueError, match="no area"):
            rasterize_zones(zones, (H, W, 3))


class TestImageHelpers:
    def test_apply_roi_paints_background_outside_allowed(self):
        img = _two_piece_sheet()
        allowed = rasterize_zones(parse_zones([_rect_zone("include", 0.0, 0.0, 0.5, 1.0)]), img.shape)
        masked = apply_roi_to_image(img, allowed)
        assert (masked[:, W // 2 + 5 :] == 255).all()
        assert not np.array_equal(masked[:, : W // 2 - 5], np.full_like(masked[:, : W // 2 - 5], 255))
        # Input must not be modified
        assert np.array_equal(img, _two_piece_sheet())

    def test_apply_roi_shape_mismatch_raises(self):
        with pytest.raises(ValueError):
            apply_roi_to_image(_two_piece_sheet(), np.ones((10, 10), bool))

    def test_overlay_marks_zones_without_touching_input(self):
        img = _two_piece_sheet()
        zones = parse_zones([_rect_zone("exclude", 0.0, 0.0, 0.5, 1.0)])
        out = draw_zones_overlay(img, zones)
        assert out.shape == img.shape
        assert not np.array_equal(out, img)
        assert np.array_equal(img, _two_piece_sheet())


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


class TestPipelineWithZones:
    def test_without_zones_finds_both_pieces(self, tmp_path, sheet_path):
        project = _pipeline(tmp_path).process_image(sheet_path)
        assert len(project.pieces) == 2

    def test_exclude_zone_removes_one_piece(self, tmp_path, sheet_path):
        zones = [_rect_zone("exclude", 0.5, 0.0, 1.0, 1.0)]
        project = _pipeline(tmp_path).process_image(sheet_path, zones=zones)
        assert len(project.pieces) == 1

    def test_include_zone_keeps_only_selected_piece(self, tmp_path, sheet_path):
        zones = [_rect_zone("include", 0.5, 0.0, 1.0, 1.0)]
        project = _pipeline(tmp_path).process_image(sheet_path, zones=zones)
        assert len(project.pieces) == 1

    def test_zones_cover_everything_raises(self, tmp_path, sheet_path):
        zones = [_rect_zone("exclude", 0.0, 0.0, 1.0, 1.0)]
        with pytest.raises(ValueError):
            _pipeline(tmp_path).process_image(sheet_path, zones=zones)

    def test_diagnostic_sheet_is_generated_with_zones(self, tmp_path, sheet_path):
        zones = [_rect_zone("exclude", 0.5, 0.0, 1.0, 1.0)]
        _pipeline(tmp_path).process_image(sheet_path, zones=zones)
        assert (tmp_path / "out" / "diagnostic_contact_sheet.png").is_file()

    @pytest.mark.parametrize("mode", ["auto", "color"])
    def test_color_modes_find_filled_pieces(self, tmp_path, sheet_path, mode):
        project = _pipeline(tmp_path, segmentation_mode=mode).process_image(sheet_path)
        assert len(project.pieces) == 2

    @pytest.mark.parametrize("mode", ["auto", "line_art"])
    def test_line_art_modes_find_outlined_pieces(self, tmp_path, mode):
        img = np.full((H, W, 3), 255, np.uint8)
        for x0 in (30, 230):
            cv2.rectangle(img, (x0, 60), (x0 + 140, 240), (0, 0, 0), thickness=2)
        path = tmp_path / "outlines.png"
        cv2.imwrite(str(path), img)

        project = _pipeline(tmp_path, segmentation_mode=mode).process_image(path)
        assert len(project.pieces) == 2

    def test_line_art_exclude_zone_removes_one_outlined_piece(self, tmp_path):
        img = np.full((H, W, 3), 255, np.uint8)
        for x0 in (30, 230):
            cv2.rectangle(img, (x0, 60), (x0 + 140, 240), (0, 0, 0), thickness=2)
        path = tmp_path / "outlines.png"
        cv2.imwrite(str(path), img)

        zones = [_rect_zone("exclude", 0.5, 0.0, 1.0, 1.0)]
        project = _pipeline(tmp_path, segmentation_mode="line_art").process_image(path, zones=zones)
        assert len(project.pieces) == 1


# ---------------------------------------------------------------------------
# Reference specs auto-detection
# ---------------------------------------------------------------------------


class TestReferenceSpecsDetection:
    def test_explicit_modes(self):
        assert should_use_reference_specs("gorro_trapper", "anything.png") is True
        assert should_use_reference_specs("none", "gorro_trapper.png") is False

    def test_auto_uses_filename_hint(self):
        assert should_use_reference_specs("auto", "upload_gorro_trapper.png") is True

    def test_auto_rejects_unrelated_sheet(self):
        assert should_use_reference_specs("auto", "cipao.png", [(0, 0, 100, 100), (0, 0, 50, 200)]) is False

    def test_auto_recognises_renamed_gorro_sheet(self):
        sample = Path("samples/gorro_trapper.png")
        if not sample.is_file():
            pytest.skip("samples/gorro_trapper.png missing")
        from src.digitizer.vectorizer import segment_pattern_pieces_detailed

        pieces = segment_pattern_pieces_detailed(sample)
        assert should_use_reference_specs("auto", "renamed.png", [p.bbox_px for p in pieces]) is True

    def test_unrelated_sheet_keeps_generic_piece_names(self, tmp_path, sheet_path):
        pipeline = DigitizerPipeline(output_dir=tmp_path / "out", export_clo3d=False, export_a4=False)
        project = pipeline.process_image(sheet_path)
        assert all(p.id.startswith("piece_") for p in project.pieces)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class TestDigitizeEndpointZones:
    def _post(self, client: TestClient, path: Path, zones, query: str = ""):
        data = {"zones": json.dumps(zones)} if zones is not None else {}
        with open(path, "rb") as f:
            return client.post(
                f"/api/digitize?reference_specs=none{query}",
                files={"file": ("two_pieces.png", f.read(), "image/png")},
                data=data,
            )

    def test_endpoint_without_zones(self, client, sheet_path):
        res = self._post(client, sheet_path, None)
        assert res.status_code == 200, res.text
        assert res.json()["pieces_count"] == 2
        assert res.json()["zones_applied"] == 0

    def test_endpoint_with_exclude_zone(self, client, sheet_path):
        res = self._post(client, sheet_path, [_rect_zone("exclude", 0.5, 0.0, 1.0, 1.0)])
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["pieces_count"] == 1
        assert body["zones_applied"] == 1

    def test_endpoint_rejects_malformed_zones(self, client, sheet_path):
        res = self._post(client, sheet_path, [{"mode": "include", "points": [[0, 0], [1, 1]]}])
        assert res.status_code == 400

    def test_endpoint_rejects_invalid_json(self, client, sheet_path):
        with open(sheet_path, "rb") as f:
            res = client.post(
                "/api/digitize",
                files={"file": ("two_pieces.png", f.read(), "image/png")},
                data={"zones": "{not json"},
            )
        assert res.status_code == 400

    def test_endpoint_reports_422_when_nothing_to_search(self, client, sheet_path):
        res = self._post(client, sheet_path, [_rect_zone("exclude", 0.0, 0.0, 1.0, 1.0)])
        assert res.status_code == 422
