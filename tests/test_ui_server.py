"""
Unit & Integration Tests for Image-to-Pattern CAD Studio (Stage 4 UI Server & API).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.contract import PatternProject
from src.presets.gorro_trapper import create_gorro_trapper_project
from src.ui.server import create_app, session


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    # Reset session to clean state before test
    session.project = create_gorro_trapper_project()
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"
    assert "pieces_count" in data
    assert data["pieces_count"] >= 6


def test_presets_endpoints(client: TestClient) -> None:
    # 1. Get Presets
    res = client.get("/api/presets")
    assert res.status_code == 200
    presets = res.json()
    assert len(presets) >= 2
    preset_ids = [p["id"] for p in presets]
    assert "gorro_trapper_cad" in preset_ids
    assert "gorro_trapper_digitized" in preset_ids

    # 2. Load Preset CAD
    load_res = client.post("/api/presets/gorro_trapper_cad/load")
    assert load_res.status_code == 200
    load_data = load_res.json()
    assert load_data["status"] == "success"
    assert len(load_data["project"]["pieces"]) == 6

    # 3. Load Invalid Preset (404)
    invalid_res = client.post("/api/presets/non_existent_preset/load")
    assert invalid_res.status_code == 404


def test_project_get_and_post(client: TestClient) -> None:
    # GET
    res = client.get("/api/project")
    assert res.status_code == 200
    proj_data = res.json()
    assert proj_data["project_name"] == "Gorro Trapper con Tapabocas"

    # POST (Update)
    proj_data["project_name"] = "Projekt Zmodyfikowany UI"
    post_res = client.post("/api/project", json=proj_data)
    assert post_res.status_code == 200
    updated_data = post_res.json()
    assert updated_data["project"]["project_name"] == "Projekt Zmodyfikowany UI"

    # Verify session updated
    assert session.project.project_name == "Projekt Zmodyfikowany UI"


def test_global_seam_endpoint(client: TestClient) -> None:
    res = client.post("/api/project/global-seam", json={"seam_allowance_mm": 15.0})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    for piece in data["project"]["pieces"]:
        assert piece["seam_allowance_mm"] == 15.0
        assert piece["cut_contour"] is not None


def test_piece_tailoring_operations(client: TestClient) -> None:
    # Find Frente piece (which has a fold edge)
    frente_id = None
    for p in session.project.pieces:
        if "frente" in p.name.lower() or p.has_fold:
            frente_id = p.id
            break
    assert frente_id is not None

    # 1. Change Seam Allowance for Piece
    res_seam = client.post(f"/api/pieces/{frente_id}/seam-allowance", json={"seam_allowance_mm": 12.0})
    assert res_seam.status_code == 200
    piece_data = res_seam.json()["piece"]
    assert piece_data["seam_allowance_mm"] == 12.0

    # 2. Unfold Piece on Fold Edge
    res_unfold = client.post(f"/api/pieces/{frente_id}/unfold")
    assert res_unfold.status_code == 200
    unfolded_data = res_unfold.json()["piece"]
    assert "Unfolded" in unfolded_data["name"]

    # 3. Mirror Piece in X
    res_mirror = client.post(f"/api/pieces/{frente_id}/mirror")
    assert res_mirror.status_code == 200
    mirrored_data = res_mirror.json()["piece"]
    assert "(2/1)" in mirrored_data["name"] or "Mirrored" in mirrored_data["name"]

    # 4. Quantity Change
    res_qty = client.post(f"/api/pieces/{frente_id}/quantity", json={"quantity": 2})
    assert res_qty.status_code == 200
    assert res_qty.json()["piece"]["quantity"] == 2

    # 5. Anisotropic Scaling
    res_scale = client.post(
        f"/api/pieces/{frente_id}/scale-anisotropic",
        json={"target_width_cm": 25.0, "target_height_cm": 15.0},
    )
    assert res_scale.status_code == 200
    scaled_data = res_scale.json()["piece"]
    assert scaled_data["target_width_cm"] == 25.0
    assert scaled_data["target_height_cm"] == 15.0


def test_segment_role_and_override(client: TestClient) -> None:
    piece = session.project.pieces[0]
    res = client.post(
        f"/api/pieces/{piece.id}/segments/0/role",
        json={"edge_role": "elastic", "seam_allowance_mm": 18.0},
    )
    assert res.status_code == 200
    updated_piece = res.json()["piece"]
    first_seg = updated_piece["outer_contour"]["segments"][0]
    assert first_seg["edge_role"] == "elastic"
    assert first_seg["seam_allowance_mm"] == 18.0


def test_nesting_calculate_endpoint(client: TestClient) -> None:
    # 1. Hybrid Mode
    res_hybrid = client.post(
        "/api/nesting/calculate",
        json={"mode": "hybrid", "page_w_mm": 210.0, "page_h_mm": 297.0},
    )
    assert res_hybrid.status_code == 200
    data_h = res_hybrid.json()
    assert data_h["mode"] == "hybrid"
    assert data_h["total_pages"] >= 1
    assert len(data_h["pages"]) == data_h["total_pages"]

    # 2. Poster Mode
    res_poster = client.post(
        "/api/nesting/calculate",
        json={"mode": "poster", "page_w_mm": 210.0, "page_h_mm": 297.0},
    )
    assert res_poster.status_code == 200
    data_p = res_poster.json()
    assert data_p["mode"] == "poster"
    assert data_p["total_pages"] >= 1


def test_production_exports(client: TestClient) -> None:
    # 1. DXF-AAMA export
    res_dxf = client.get("/api/export/dxf?unit=mm&expand=true")
    assert res_dxf.status_code == 200
    assert "attachment" in res_dxf.headers["content-disposition"]
    assert res_dxf.headers["content-type"] == "application/dxf"
    assert b"SECTION" in res_dxf.content
    assert b"ENTITIES" in res_dxf.content

    # 2. Clo3D 1:1 Vector PDF export
    res_pdf_clo3d = client.get("/api/export/pdf-clo3d?expand=false")
    assert res_pdf_clo3d.status_code == 200
    assert res_pdf_clo3d.headers["content-type"] == "application/pdf"
    assert res_pdf_clo3d.content.startswith(b"%PDF")

    # 3. Technical Sheet PDF export
    res_pdf_tech = client.get("/api/export/pdf-tech")
    assert res_pdf_tech.status_code == 200
    assert res_pdf_tech.headers["content-type"] == "application/pdf"
    assert res_pdf_tech.content.startswith(b"%PDF")

    # 4. A4 Print PDF export (Hybrid & Poster)
    res_pdf_a4_h = client.get("/api/export/pdf-a4?mode=hybrid")
    assert res_pdf_a4_h.status_code == 200
    assert res_pdf_a4_h.headers["content-type"] == "application/pdf"
    assert res_pdf_a4_h.content.startswith(b"%PDF")

    res_pdf_a4_p = client.get("/api/export/pdf-a4?mode=poster")
    assert res_pdf_a4_p.status_code == 200
    assert res_pdf_a4_p.content.startswith(b"%PDF")

    # 5. JSON Contract export
    res_json = client.get("/api/export/json")
    assert res_json.status_code == 200
    assert res_json.headers["content-type"] == "application/json"
    parsed = PatternProject.model_validate_json(res_json.text)
    assert len(parsed.pieces) >= 6


def test_static_spa_serving(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "IMAGE-TO-PATTERN CAD" in res.text

    res_js = client.get("/app.js")
    assert res_js.status_code == 200
    assert "state" in res_js.text

    res_css = client.get("/style.css")
    assert res_css.status_code == 200


def test_digitize_api_endpoint(client: TestClient) -> None:
    sample_img = Path("samples/gorro_trapper.png")
    if not sample_img.is_file():
        pytest.skip("Brak pliku samples/gorro_trapper.png")

    with open(sample_img, "rb") as f:
        file_bytes = f.read()

    res = client.post(
        "/api/digitize?seam_mode=net_only&seam_allowance_mm=10.0&auto_unfold=false",
        files={"file": ("test_pattern.png", file_bytes, "image/png")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["pieces_count"] >= 5
    assert len(session.project.pieces) == data["pieces_count"]
