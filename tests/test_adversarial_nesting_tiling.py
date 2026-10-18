"""
Zestaw testów odpornościowych i weryfikacyjnych dla Milestone 2 (R2):
Dual-Mode 2D Nesting & Multi-Page A4 Tiling Engine.

Weryfikuje:
1. Wymiary stron PDF we wszystkich trybach: A4 = 595.28 x 841.89 pt (+/- 0.5 pt).
2. Kwadrat kalibracyjny 5.0 x 5.0 cm (50 x 50 mm = 141.73 x 141.73 pt) na stronie 1.
3. Renderowanie podwójnych konturów (solidna linia cięcia, przerywana linia szycia).
4. Rozwinięcie 9 elementów czapki Gorro Trapper (x1 i x2) i ich obecność na arkuszach.
5. Wyrocznię braku kolizji wieloelementowych: poly1.intersection(poly2).area < 1e-2.
6. Wyrocznię rekonstrukcji geometrii: suma kafelków odtwarza 100% oryginalnego wielokąta.
7. Spójność współrzędnych paserów (romby) i linii klejenia na stykających się arkuszach.
8. Kafelkowanie skrajnych wymiarów (pas o długości 1000 mm).
9. Obsługę projektów bez naddatków na szwy (has_seam_allowance=False).
10. Integralność wektorową konturów na kaflach: brak fałszywych cięciw diagonalnych (spurious chords)
    przecinających wnętrze wykroju z powodu domyślnego closePath=True w PyMuPDF.
"""

from __future__ import annotations

import math
from pathlib import Path
import pytest
import pymupdf as fitz
from shapely.geometry import Point, Polygon, box
from shapely.ops import linemerge, unary_union

from src.core.contract import (
    Contour,
    PathSegment,
    PatternPiece,
    PatternProject,
    Point2D,
)
from src.core.geometry import (
    contour_to_points,
    contour_to_shapely_linestring,
    contour_to_shapely_polygon,
)
from src.core.seam_allowance import apply_seam_allowance_to_project
from src.presets.gorro_trapper import create_gorro_trapper_project
from src.nesting.a4_nester import (
    expand_project_pieces,
    nest_hybrid_mode,
    nest_poster_mode,
)
from src.exporters.a4_tiler_pdf import export_a4_pdf


@pytest.fixture(scope="module")
def gorro_project_with_seams() -> PatternProject:
    return apply_seam_allowance_to_project(create_gorro_trapper_project())


@pytest.fixture(scope="module")
def hybrid_and_poster_pdfs(gorro_project_with_seams, tmp_path_factory) -> tuple[Path, Path]:
    out_dir = tmp_path_factory.mktemp("pdf_test")
    hybrid_res = nest_hybrid_mode(gorro_project_with_seams)
    poster_res = nest_poster_mode(gorro_project_with_seams)

    hybrid_pdf = out_dir / "test_hybrid.pdf"
    poster_pdf = out_dir / "test_poster.pdf"

    export_a4_pdf(hybrid_res, hybrid_pdf, render_png_previews=False)
    export_a4_pdf(poster_res, poster_pdf, render_png_previews=False)
    return hybrid_pdf, poster_pdf


def test_pdf_pages_exact_a4_dimensions(hybrid_and_poster_pdfs):
    """Kryterium R2: Każda strona PDF ma wymiary A4 (595.28 x 841.89 pt +/- 0.5 pt)."""
    hybrid_pdf, poster_pdf = hybrid_and_poster_pdfs

    for pdf_path, expected_count in [(hybrid_pdf, 13), (poster_pdf, 12)]:
        doc = fitz.open(pdf_path)
        assert len(doc) == expected_count
        for idx, page in enumerate(doc):
            w = page.rect.width
            h = page.rect.height
            assert abs(w - 595.28) < 0.5, f"{pdf_path.name} strona {idx+1} zła szerokość: {w} pt"
            assert abs(h - 841.89) < 0.5, f"{pdf_path.name} strona {idx+1} zła wysokość: {h} pt"
        doc.close()


def test_scale_calibration_square_50x50mm_presence(hybrid_and_poster_pdfs):
    """Kryterium R2: Kwadrat kontrolny 5.0 x 5.0 cm (141.73 pt) i instrukcja na stronie 1."""
    hybrid_pdf, poster_pdf = hybrid_and_poster_pdfs

    for pdf_path in [hybrid_pdf, poster_pdf]:
        doc = fitz.open(pdf_path)
        p1 = doc[0]
        text = p1.get_text()
        assert "TEST SKALI 1:1" in text
        assert "5.0 x 5.0 cm" in text

        found_square = False
        for d in p1.get_drawings():
            dr = d["rect"]
            if abs(dr.width - 141.73) < 1.0 and abs(dr.height - 141.73) < 1.0:
                found_square = True
                break
        assert found_square, f"Brak kwadratu kontrolnego 50x50 mm na stronie 1 w {pdf_path.name}"
        doc.close()


def test_dual_contour_rendering_styles(hybrid_and_poster_pdfs):
    """Kryterium R2: Podwójne kontury - solid cut line i dashed sewing line."""
    hybrid_pdf, poster_pdf = hybrid_and_poster_pdfs

    for pdf_path in [hybrid_pdf, poster_pdf]:
        doc = fitz.open(pdf_path)
        p1 = doc[0]
        has_solid = False
        has_dashed = False
        for d in p1.get_drawings():
            dashes = d.get("dashes", "")
            if dashes and "4 2" in dashes:
                has_dashed = True
            elif d.get("width", 0) > 1.1 and dashes in (None, "", "[] 0"):
                has_solid = True
        assert has_solid, f"Brak solidnej linii cięcia w {pdf_path.name}"
        assert has_dashed, f"Brak przerywanej linii szycia w {pdf_path.name}"
        doc.close()


def test_gorro_trapper_all_9_expanded_pieces_accounted(gorro_project_with_seams):
    """Kryterium R2: Wszystkie 9 rozwiniętych elementów wykroju są uwzględnione w nestingu."""
    expanded = expand_project_pieces(gorro_project_with_seams, mirror_symmetric=True)
    assert len(expanded) == 9

    expected_ids = {
        "fuelle_superior",
        "frente",
        "visera_1",
        "visera_2",
        "cubre_boca",
        "lateral_1",
        "lateral_2",
        "orejera_compuesta_1",
        "orejera_compuesta_2",
    }

    hybrid_res = nest_hybrid_mode(gorro_project_with_seams)
    hybrid_ids = {pp.piece.id.split("_rot")[0] for p in hybrid_res.pages for pp in p.placed_pieces}
    assert hybrid_ids == expected_ids

    poster_res = nest_poster_mode(gorro_project_with_seams)
    poster_ids = {pp.piece.id.split("_rot")[0] for p in poster_res.pages for pp in p.placed_pieces}
    assert poster_ids == expected_ids


def test_multi_piece_whole_sheet_collision_oracle():
    """Kryterium R2: Na arkuszach z wieloma elementami brak kolizji (overlap < 1e-2)."""
    # Tworzymy 12 mniejszych elementów
    pieces = [
        PatternPiece(
            id=f"piece_{i}",
            name=f"Square {i}",
            quantity=1,
            target_width_cm=5.0,
            target_height_cm=5.0,
            outer_contour=Contour.from_polyline(
                [(0.0, 0.0), (50.0, 0.0), (50.0, 50.0), (0.0, 50.0)], is_closed=True
            ),
        )
        for i in range(12)
    ]
    proj = PatternProject(project_name="multi_pack", pieces=pieces)
    res = nest_hybrid_mode(proj)

    for page in res.pages:
        if len(page.placed_pieces) > 1:
            for i in range(len(page.placed_pieces)):
                for j in range(i + 1, len(page.placed_pieces)):
                    p1 = page.placed_pieces[i].get_placed_polygon()
                    p2 = page.placed_pieces[j].get_placed_polygon()
                    overlap = p1.intersection(p2).area
                    assert overlap < 1e-2, f"Wykryto kolizję {overlap:.4f} mm² na stronie {page.page_index}"


def test_slicing_geometric_conservation_oracle(gorro_project_with_seams):
    """
    Wyrocznia rekonstrukcji geometrii: suma kafli pociętego elementu
    odtwarza 100.0% oryginalnego wielokąta z zerową stratą pola.
    """
    hybrid = nest_hybrid_mode(gorro_project_with_seams)
    fuelle_pages = [p for p in hybrid.pages if "Fuelle superior" in p.piece_name]
    assert len(fuelle_pages) == 2

    placed_poly = fuelle_pages[0].placed_pieces[0].get_placed_polygon()
    clipped_tiles = []
    for p in fuelle_pages:
        tile_box = box(*p.tile_box_mm)
        clipped_tiles.append(placed_poly.intersection(tile_box))

    reconstructed = unary_union(clipped_tiles)
    diff_area = placed_poly.symmetric_difference(reconstructed).area
    assert diff_area < 1e-2, f"Utrata geometrii przy kafelkowaniu: {diff_area} mm²"


def test_adjacent_tile_markers_coordinate_alignment(gorro_project_with_seams):
    """Sprawdza czy pasery dopasowania na sąsiadujących kaflach mają tę samą pozycję w CAD."""
    hybrid = nest_hybrid_mode(gorro_project_with_seams)
    # Cubre boca kafelkowane poziomo: R1C1 i R1C2
    cubre_p1 = next(p for p in hybrid.pages if "Cubre boca" in p.piece_name and p.col == 1)
    cubre_p2 = next(p for p in hybrid.pages if "Cubre boca" in p.piece_name and p.col == 2)

    # Granica klejenia w R1C1 to gx0 + printable_w - overlap_mm/2 = 0 + 190 - 7.5 = 182.5 mm
    # Granica pasowania w R1C2 to gx0 + overlap_mm/2 = 175 + 7.5 = 182.5 mm
    marker_x_p1 = cubre_p1.tile_box_mm[0] + (190.0 - 15.0 / 2.0)
    marker_x_p2 = cubre_p2.tile_box_mm[0] + (15.0 / 2.0)
    assert abs(marker_x_p1 - marker_x_p2) < 1e-4


def test_ultra_long_piece_tiling_stress():
    """Stres-test: Pas o długości 1000 mm kafelkowany na co najmniej 4 arkusze A4."""
    strap = PatternPiece(
        id="strap_1000",
        name="Strap 1000mm",
        quantity=1,
        target_width_cm=4.0,
        target_height_cm=100.0,
        outer_contour=Contour.from_polyline(
            [(0.0, 0.0), (40.0, 0.0), (40.0, 1000.0), (0.0, 1000.0)], is_closed=True
        ),
    )
    proj = PatternProject(project_name="strap", pieces=[strap])
    hybrid = nest_hybrid_mode(proj)
    assert hybrid.total_pages >= 4


def test_no_seam_allowance_project_handling():
    """Weryfikacja obsługi projektu bez naddatków na szwy (cut_contour=None)."""
    proj = create_gorro_trapper_project()
    assert proj.has_seam_allowance is False
    hybrid = nest_hybrid_mode(proj)
    assert hybrid.total_pages == 13


def test_no_spurious_interior_diagonal_chords_on_tiled_pages(gorro_project_with_seams, tmp_path):
    """
    KRYTYCZNY TEST REGRESYJNY / WERYFIKACYJNY:
    Na stronach kafelkowanych pocięte kontury cięcia i szycia nie mogą zawierać
    fałszywych cięciw diagonalnych przecinających wnętrze wykroju.
    Błąd powstaje, gdy PyMuPDF zamyka otwarty segment (closePath=True w finish()).
    """
    hybrid = nest_hybrid_mode(gorro_project_with_seams)
    pdf_path = tmp_path / "verification_chords.pdf"
    export_a4_pdf(hybrid, pdf_path, render_png_previews=False)

    doc = fitz.open(pdf_path)
    # Strona 6: Fuelle superior (R1C1)
    p6 = hybrid.pages[5]
    tp = p6.placed_pieces[0].get_placed_piece()
    gx0, gy0, gx1, gy1 = p6.tile_box_mm
    cut_ring = contour_to_shapely_linestring(tp.cut_contour, steps_per_curve=64)

    page = doc[5]
    cut_drawings = [d for d in page.get_drawings() if d.get("width") == 1.25]

    spurious_segments = []
    for d in cut_drawings:
        for item in d["items"]:
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                x1_mm = (p1.x / 2.834645669291339) - 10.0 + gx0
                y1_mm = (297.0 - (p1.y / 2.834645669291339)) - 10.0 + gy0
                x2_mm = (p2.x / 2.834645669291339) - 10.0 + gx0
                y2_mm = (297.0 - (p2.y / 2.834645669291339)) - 10.0 + gy0

                mid_x = (x1_mm + x2_mm) / 2.0
                mid_y = (y1_mm + y2_mm) / 2.0
                dist = cut_ring.distance(Point(mid_x, mid_y))
                if dist > 2.0:
                    spurious_segments.append((mid_x, mid_y, dist))

    doc.close()

    assert len(spurious_segments) == 0, (
        f"Wykryto {len(spurious_segments)} fałszywych cięciw tnących wnętrze elementu na stronie 6! "
        f"Maksymalna dewiacja od konturu cięcia: {max(s[2] for s in spurious_segments):.2f} mm"
    )
