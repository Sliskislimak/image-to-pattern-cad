"""
Zestaw testów weryfikujących rozszerzenia semantyki krawieckiej (Etap 1 uzupełnienie)
oraz poprawki audytorskie GravityAudit:
1. Rozkładanie wykroju na linii zgięcia (unfold_piece_on_fold) dla krawędzi 'fold' / 'doblez'.
2. Karbiki montażowe (Notches) na warstwie '2' standardu DXF-AAMA oraz na arkuszach A4 PDF.
3. Linie zgięcia (Fold / Mirror axis) na dedykowanej warstwie '5' standardu DXF-AAMA.
4. Resetowanie cut_contour podczas re-kalibracji anizotropowej (ochrona spójności geometrii).
5. Centralizacja funkcji mirror_piece_x oraz expand_project_pieces w src.core.geometry (DRY).
"""

from __future__ import annotations

import math
import pytest
import ezdxf
from shapely.geometry import Polygon

from src.core.contract import (
    LINE_TYPE_CUT,
    LINE_TYPE_FOLD,
    LINE_TYPE_SEAM,
    LINE_TYPE_SLIT,
    Contour,
    InternalLine,
    Notch,
    PathSegment,
    PatternPiece,
    PatternProject,
    Point2D,
)
from src.core.geometry import (
    calibrate_piece_anisotropic,
    contour_to_points,
    expand_project_pieces,
    get_contour_bounds,
    mirror_piece_x,
    unfold_piece_on_fold,
)
from src.exporters.dxf_exporter import (
    AAMA_LAYER_BOUNDARY,
    AAMA_LAYER_FOLD,
    AAMA_LAYER_INTERNAL,
    AAMA_LAYER_NOTCH,
    export_clo3d_dxf,
)
from src.presets.gorro_trapper import build_frente, build_fuelle_superior, create_gorro_trapper_project


def test_calibrate_piece_resets_cut_contour() -> None:
    """Weryfikuje, że ponowna kalibracja anizotropowa unieważnia cut_contour (ochrona przed desynchronizacją)."""
    piece = build_frente()
    # Sztucznie przypisujemy cut_contour
    dummy_cut = Contour.from_polyline([(0, 0), (100, 0), (100, 100), (0, 100)], is_closed=True)
    piece_with_seam = piece.model_copy(update={"cut_contour": dummy_cut})
    assert piece_with_seam.has_cut_contour is True

    # Re-kalibracja
    recalibrated = calibrate_piece_anisotropic(piece_with_seam)
    assert recalibrated.cut_contour is None
    assert recalibrated.has_cut_contour is False


def test_unfold_piece_on_fold_edge_frente() -> None:
    """
    Weryfikuje rozłożenie (Unfold) elementu Frente wzdłuż dolnej krawędzi zgięcia:
    - Oryginalne Frente ma szerokość 12,5 cm (125 mm) i wysokość 11,0 cm (110 mm).
    - Dolna krawędź to linia zgięcia (y = 0).
    - Po rozłożeniu symetrycznym wysokość podwaja się: 110 mm * 2 = 220 mm (+/- 0.5 mm).
    - Powstaje w pełni zamknięty, poprawny wielokąt bez samoprzecięć.
    - Oryginalna linia zgięcia trafia do internal_lines z typem 'fold'.
    """
    frente = build_frente()
    assert frente.has_fold is True
    assert frente.fold_segment_index == 0

    unfolded = unfold_piece_on_fold(frente)

    # Weryfikacja wymiarów rozłożonego elementu
    min_x, min_y, max_x, max_y = get_contour_bounds(unfolded.outer_contour)
    w_mm = max_x - min_x
    h_mm = max_y - min_y

    assert math.isclose(w_mm, 125.0, abs_tol=0.5), f"Oczekiwano szerokości 125 mm, otrzymano {w_mm:.2f}"
    assert math.isclose(h_mm, 220.0, abs_tol=0.5), f"Oczekiwano podwojonej wysokości 220 mm, otrzymano {h_mm:.2f}"
    assert math.isclose(min_x, 0.0, abs_tol=1e-3)
    assert math.isclose(min_y, 0.0, abs_tol=1e-3)

    # Weryfikacja poprawności topologicznej wielokąta
    pts = contour_to_points(unfolded.outer_contour)
    poly = Polygon(pts)
    assert poly.is_valid is True
    assert poly.area > 0.0

    # Weryfikacja linii zgięcia jako osi wewnętrznej
    fold_lines = [il for il in unfolded.internal_lines if il.line_type == "fold"]
    assert len(fold_lines) >= 1
    assert "Linia zgięcia" in fold_lines[0].label or "Fold" in fold_lines[0].label


def test_unfold_raises_value_error_if_no_fold() -> None:
    """Weryfikuje, że próba rozłożenia elementu bez krawędzi 'fold' zgłasza ValueError."""
    fuelle = build_fuelle_superior()
    assert fuelle.has_fold is False
    with pytest.raises(ValueError, match="nie posiada krawędzi o roli 'fold'"):
        unfold_piece_on_fold(fuelle)


def test_dxf_aama_exports_notches_and_fold_layers(tmp_path) -> None:
    """
    Weryfikuje, że eksporter DXF-AAMA eksportuje:
    - Karbiki montażowe na warstwie '2' (AAMA_LAYER_NOTCH)
    - Linie zgięcia / osie symetrii na warstwie '5' (AAMA_LAYER_FOLD)
    """
    frente = build_frente()
    unfolded_frente = unfold_piece_on_fold(frente)

    # Dodajemy karbiki montażowe
    notch1 = Notch(
        id="notch_center_top",
        position=Point2D(x=62.5, y=220.0),
        direction=Point2D(x=0.0, y=-1.0),
        length_mm=6.0,
        label="Nacięcie środka",
    )
    notch2 = Notch(
        id="notch_left_side",
        position=Point2D(x=0.0, y=110.0),
        direction=Point2D(x=1.0, y=0.0),
        length_mm=5.0,
        label="Nacięcie pasowania",
    )
    piece_with_notches = unfolded_frente.model_copy(update={"notches": [notch1, notch2]})

    project = PatternProject(
        project_name="Test Notches & Folds",
        pieces=[piece_with_notches],
    )

    dxf_file = tmp_path / "test_notches.dxf"
    export_clo3d_dxf(project, dxf_file, unit="mm")
    assert dxf_file.exists()

    doc = ezdxf.readfile(str(dxf_file))

    # Weryfikacja obecności warstw AAMA '2' i '5'
    assert AAMA_LAYER_NOTCH in doc.layers, "Brak warstwy '2' (NOTCHES) w DXF"
    assert AAMA_LAYER_FOLD in doc.layers, "Brak warstwy '5' (FOLD) w DXF"

    # Weryfikacja zawartości bloku
    block = doc.blocks.get("PIECE_1_FRENTE")
    assert block is not None

    notch_lines = [e for e in block if e.dxftype() == "LINE" and e.dxf.layer == AAMA_LAYER_NOTCH]
    assert len(notch_lines) == 2, f"Oczekiwano 2 karbików na warstwie '2', znaleziono {len(notch_lines)}"

    fold_polylines = [e for e in block if e.dxf.layer == AAMA_LAYER_FOLD]
    assert len(fold_polylines) >= 1, "Oczekiwano co najmniej 1 linii zgięcia na warstwie '5'"


def test_dry_centralized_expand_and_mirror() -> None:
    """Weryfikuje poprawne działanie scentralizowanych funkcji mirror_piece_x i expand_project_pieces."""
    project = create_gorro_trapper_project()
    assert len(project.pieces) == 6

    # Rozwinięcie mnożników (Gorro Trapper ma części x1 i x2 dające łącznie 9 sztuk)
    expanded = expand_project_pieces(project, mirror_symmetric=True)
    assert len(expanded) == 9

    # Weryfikacja, że kopia symetryczna ma odwrócone x i jest znormalizowana
    visera_1 = next(p for p in expanded if p.id == "visera_1")
    visera_2 = next(p for p in expanded if p.id == "visera_2")

    min_x1, min_y1, max_x1, max_y1 = get_contour_bounds(visera_1.outer_contour)
    min_x2, min_y2, max_x2, max_y2 = get_contour_bounds(visera_2.outer_contour)

    assert math.isclose(max_x1 - min_x1, max_x2 - min_x2, abs_tol=1e-3)
    assert math.isclose(max_y1 - min_y1, max_y2 - min_y2, abs_tol=1e-3)
    assert math.isclose(min_x1, 0.0, abs_tol=1e-3)
    assert math.isclose(min_x2, 0.0, abs_tol=1e-3)
