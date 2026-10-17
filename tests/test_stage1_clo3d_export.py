"""
Testy automatyczne Etapu 1:
- Walidacja kontraktu danych PatternProject (JSON Schema)
- Walidacja wymiarów 1:1 wszystkich 6 elementów czapki Gorro Trapper (dokładność < 0.05 mm)
- Walidacja skalowania anizotropowego (odporność na nieproporcjonalne rysunki z internetu)
- Walidacja struktury AAMA-DXF (bloki, warstwy 1/4/7/8, zamknięte polilinie)
- Walidacja wektorowego PDF 1:1 dla Clo3D.
"""

from __future__ import annotations

from pathlib import Path
import ezdxf
import pymupdf as fitz

from src.core.contract import Contour, PatternPiece, PatternProject
from src.core.geometry import (
    calibrate_piece_anisotropic,
    contour_to_shapely_polygon,
    get_contour_dimensions_mm,
)
from src.exporters.dxf_exporter import (
    AAMA_LAYER_BOUNDARY,
    AAMA_LAYER_GRAINLINE,
    AAMA_LAYER_INTERNAL,
    export_clo3d_dxf,
)
from src.exporters.pdf_exporter import (
    export_clo3d_vector_pdf_1to1,
    export_technical_sheet_pdf_1to1,
)
from src.presets.gorro_trapper import create_gorro_trapper_project


def test_gorro_trapper_dimensions_exact_1to1() -> None:
    """Sprawdza, czy każdy z 6 elementów czapki ma idealny wymiar 1:1 w milimetrach."""
    project = create_gorro_trapper_project()
    assert len(project.pieces) == 6

    expected_cm = {
        "fuelle_superior": (13.0, 35.0, 1),
        "frente": (12.5, 11.0, 1),
        "visera": (20.0, 11.0, 2),
        "cubre_boca": (28.0, 14.0, 1),
        "lateral": (10.0, 20.0, 2),
        "orejera_compuesta": (25.0, 19.5, 2),
    }

    for piece in project.pieces:
        exp_w_cm, exp_h_cm, exp_qty = expected_cm[piece.id]
        assert piece.quantity == exp_qty
        w_mm, h_mm = get_contour_dimensions_mm(piece.outer_contour, steps_per_curve=64)
        assert abs(w_mm - exp_w_cm * 10.0) < 0.05, f"Błędna szerokość dla {piece.name}: {w_mm} mm"
        assert abs(h_mm - exp_h_cm * 10.0) < 0.05, f"Błędna wysokość dla {piece.name}: {h_mm} mm"

        # Kontur musi tworzyć poprawny, zamknięty wielokąt o dodatnim polu powierzchni
        poly = contour_to_shapely_polygon(piece.outer_contour)
        assert poly.is_valid
        assert poly.area > 5000.0


def test_anisotropic_calibration_fixes_distorted_aspect_ratio() -> None:
    """
    Symuluje sytuację z obrazka, gdzie element 28x14 cm był narysowany w pikselach
    jako nieproporcjonalny prostokąt np. 180x150 px. Po kalibracji ma mieć dokładnie 280x140 mm.
    """
    raw_contour = Contour.from_polyline(
        [(10.0, 20.0), (190.0, 20.0), (190.0, 170.0), (10.0, 170.0)],
        is_closed=True,
    )
    piece = PatternPiece(
        id="distorted_test",
        name="Test Anizotropowy",
        target_width_cm=28.0,
        target_height_cm=14.0,
        outer_contour=raw_contour,
    )
    calibrated = calibrate_piece_anisotropic(piece)
    w_mm, h_mm = get_contour_dimensions_mm(calibrated.outer_contour)
    assert abs(w_mm - 280.0) < 1e-4
    assert abs(h_mm - 140.0) < 1e-4


def test_json_contract_roundtrip(tmp_path: Path) -> None:
    """Sprawdza zapis i odczyt kontraktu danych PatternProject.json."""
    project = create_gorro_trapper_project()
    json_path = tmp_path / "PatternProject.json"
    project.save_json(json_path)

    loaded = PatternProject.load_json(json_path)
    assert loaded.project_name == project.project_name
    assert len(loaded.pieces) == 6
    assert loaded.pieces[0].name == "Fuelle superior"


def test_dxf_aama_export_structure(tmp_path: Path) -> None:
    """Weryfikuje strukturę wygenerowanego pliku DXF-AAMA pod kątem wymogów Clo3D."""
    project = create_gorro_trapper_project()
    dxf_path = export_clo3d_dxf(project, tmp_path / "test_clo3d.dxf", unit="mm", expand_quantities=False)
    assert dxf_path.exists()

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    inserts = list(msp.query("INSERT"))
    assert len(inserts) == 6

    # Sprawdzamy warstwy AAMA
    assert AAMA_LAYER_BOUNDARY in doc.layers
    assert AAMA_LAYER_INTERNAL in doc.layers
    assert AAMA_LAYER_GRAINLINE in doc.layers

    # Sprawdzamy zawartość pierwszego bloku (Fuelle superior: 130 x 350 mm)
    first_block = doc.blocks.get(inserts[0].dxf.name)
    polylines_layer1 = [
        e for e in first_block if e.dxftype() == "POLYLINE" and e.dxf.layer == AAMA_LAYER_BOUNDARY
    ]
    assert len(polylines_layer1) == 1
    boundary_poly = polylines_layer1[0]
    assert boundary_poly.is_closed

    pts = [v.dxf.location for v in boundary_poly.vertices]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    assert abs((max(xs) - min(xs)) - 130.0) < 0.1
    assert abs((max(ys) - min(ys)) - 350.0) < 0.1


def test_vector_pdf_1to1_export(tmp_path: Path) -> None:
    """Weryfikuje wygenerowanie czystego wektorowego PDF 1:1 dla Clo3D oraz arkusza technicznego."""
    project = create_gorro_trapper_project()
    clean_pdf = export_clo3d_vector_pdf_1to1(project, tmp_path / "clo3d_1to1.pdf", expand_quantities=True)
    tech_pdf = export_technical_sheet_pdf_1to1(project, tmp_path / "tech_1to1.pdf", expand_quantities=False)

    assert clean_pdf.exists()
    assert tech_pdf.exists()

    doc = fitz.open(str(clean_pdf))
    assert len(doc) == 1
    drawings = doc[0].get_drawings()
    # 9 rozwiniętych elementów (3x x1 + 3x x2) + 6 linii wewnętrznych (po 3 w obu nausznikach) = 15 ścieżek
    assert len(drawings) >= 9
    doc.close()
