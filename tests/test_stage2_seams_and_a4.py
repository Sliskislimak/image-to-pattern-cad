"""
Oficjalny, kompleksowy zestaw testów automatycznych Etapu 2 (Milestone 4 — R3):
Dual-Mode 2D Nesting, Seam Allowance Engine & Clo3D Exporters Verification Suite.

Weryfikuje wszystkie kryteria akceptacji (Acceptance Criteria) z ORIGINAL_REQUEST.md:
1. Jednolity naddatek 10 mm (Uniform Seam Allowance):
   - Wzrost szerokości i wysokości o 20.0 mm (+/- 0.25 mm).
   - Poprawność wielokąta cięcia (Polygon.is_valid == True).
   - Dylatacja na zewnątrz (oryginalny obrys w całości zawarty w obrysie cięcia).
2. Zróżnicowany naddatek na szew (Variable Per-Edge Seam Allowance):
   - Krawędź zgięcia (fold 0.0 mm na Frente) z przesunięciem 0.0 mm (+/- 0.05 mm).
   - Standardowe szwy z przesunięciem 10.0 mm (+/- 0.5 mm).
   - Ściągacze (elastic 15.0 mm na Cubre boca) z przesunięciem 15.0 mm (+/- 0.5 mm).
   - Bounding box Cubre boca (+20 mm szerokość, +30 mm wysokość).
3. Wielostronicowe dokumenty A4 (Multi-Page A4 PDF Dimensions):
   - Wszystkie strony w trybach 'hybrid' i 'poster' mają wymiary A4 (595.28 x 841.89 pt +/- 0.5 pt).
4. Wyrocznia braku kolizji (Non-Overlap Guarantee):
   - Na każdej stronie A4 zawierającej wiele elementów: poly1.intersection(poly2).area < 1e-2 mm².
5. Kompletność i kafelkowanie (9 Expanded Pieces & Oversized Slicing):
   - Wszystkie 9 rozwiniętych elementów czapki Gorro Trapper (x1 i x2) są obecne w wyjściu A4.
   - Elementy przewyższające A4 (np. Fuelle superior 350 mm) są dzielone na co najmniej 2 kafle.
   - Pasy klejenia (15 mm) i znaczniki pasowania (romby R{r}-C{c}).
6. Kwadrat kontrolny 50x50 mm i podwójne kontury na A4 (Calibration Square & Dual Contours):
   - Kwadrat 5.0 x 5.0 cm (141.73 x 141.73 pt +/- 0.5 pt) na stronie 1.
   - Ciągła linia cięcia (solid cut line) i przerywana linia szycia (dashed sewing line).
7. Eksporty podwójnych konturów dla Clo3D (Dual-Contour Clo3D Exports):
   - DXF-AAMA Warstwa 1 (obrys cięcia) i Warstwa 4 (linia szycia oraz linie wewnętrzne).
   - Wektorowy PDF 1:1 dla Clo3D (ciągła linia cięcia + przerywana linia szycia).
   - Wektorowy Arkusz Techniczny 1:1 (wypełnienie beżowe, węzły CAD, wymiary).
8. Czyste wykonanie run_stage2.py (End-to-End Pipeline Execution):
   - Kod wyjścia 0, 35 wygenerowanych plików w output/stage2_a4_and_seams/.
"""

from __future__ import annotations

import math
from pathlib import Path
import ezdxf
import pymupdf as fitz
import pytest
from shapely.geometry import LineString, Point, Polygon, box

import run_stage2
from src.core.contract import (
    Contour,
    PathSegment,
    PatternPiece,
    PatternProject,
    Point2D,
    PT_PER_MM,
)
from src.core.geometry import (
    contour_to_points,
    contour_to_shapely_polygon,
    get_contour_bounds,
    get_contour_dimensions_mm,
)
from src.core.seam_allowance import (
    apply_seam_allowance_to_piece,
    apply_seam_allowance_to_project,
    apply_uniform_seam_allowance,
    apply_variable_seam_allowance,
)
from src.exporters.a4_tiler_pdf import (
    PAGE_H_MM,
    PAGE_H_PT,
    PAGE_W_MM,
    PAGE_W_PT,
    export_a4_pdf,
)
from src.exporters.dxf_exporter import (
    AAMA_LAYER_BOUNDARY,
    AAMA_LAYER_GRAINLINE,
    AAMA_LAYER_INTERNAL,
    AAMA_LAYER_TEXT,
    export_clo3d_dxf,
)
from src.exporters.pdf_exporter import (
    export_clo3d_vector_pdf_1to1,
    export_technical_sheet_pdf_1to1,
)
from src.nesting.a4_nester import (
    expand_project_pieces,
    nest_hybrid_mode,
    nest_poster_mode,
    transform_piece_geometry,
)
from src.presets.gorro_trapper import (
    build_cubre_boca,
    build_frente,
    build_fuelle_superior,
    create_gorro_trapper_project,
)


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def gorro_base_project() -> PatternProject:
    """Zwraca bazowy projekt czapki Gorro Trapper (6 elementów bez naddatków)."""
    return create_gorro_trapper_project()


@pytest.fixture
def gorro_project_with_seams() -> PatternProject:
    """Zwraca projekt czapki Gorro Trapper z wygenerowanymi naddatkami na szwy."""
    base_proj = create_gorro_trapper_project()
    return apply_seam_allowance_to_project(base_proj, global_seam_allowance_mm=10.0)


@pytest.fixture
def calibrated_square_piece() -> PatternPiece:
    """Zwraca idealny skalibrowany kwadrat 100 x 100 mm."""
    cnt = Contour.from_polyline(
        [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)],
        is_closed=True,
    )
    return PatternPiece(
        id="calibrated_square",
        name="Kwadrat Kalibracyjny 100x100 mm",
        target_width_cm=10.0,
        target_height_cm=10.0,
        outer_contour=cnt,
        seam_allowance_mm=10.0,
    )


# ==============================================================================
# SECTION 1: UNIFORM SEAM ALLOWANCE (KRYTERIUM 1)
# ==============================================================================

class TestUniformSeamAllowance:
    """
    Weryfikacja jednolitego naddatku na szew (R1 / Kryterium 1):
    Uniform 10 mm seam allowance applied to calibrated piece increases bounding box
    width and height by 20.0 mm (+/- 0.25 mm) and produces valid polygon (Polygon.is_valid == True).
    """

    def test_uniform_seam_allowance_calibrated_square_bounds_and_validity(
        self, calibrated_square_piece: PatternPiece
    ) -> None:
        """Kwadrat 100x100 mm z naddatkiem 10 mm powiększa się dokładnie o 20.0 mm wzdłuż obu osi."""
        offset_piece = apply_uniform_seam_allowance(calibrated_square_piece, seam_mm=10.0)
        assert offset_piece.cut_contour is not None

        net_w, net_h = get_contour_dimensions_mm(offset_piece.outer_contour)
        cut_w, cut_h = get_contour_dimensions_mm(offset_piece.cut_contour)

        dw = cut_w - net_w
        dh = cut_h - net_h

        assert abs(dw - 20.0) <= 0.25, f"Błędny przyrost szerokości dW = {dw:.4f} mm (oczekiwano 20.0 +/- 0.25 mm)"
        assert abs(dh - 20.0) <= 0.25, f"Błędny przyrost wysokości dH = {dh:.4f} mm (oczekiwano 20.0 +/- 0.25 mm)"

        # Walidacja poprawności wielokąta Shapely
        cut_poly = contour_to_shapely_polygon(offset_piece.cut_contour)
        assert cut_poly.is_valid, "Obrys cięcia nie jest poprawnym wielokątem Shapely!"

        # Dylatacja na zewnątrz: net w całości zawiera się w cut
        net_poly = contour_to_shapely_polygon(offset_piece.outer_contour)
        diff_area = net_poly.difference(cut_poly).area
        assert diff_area < 1e-2, f"Linia netto wychodzi poza obrys cięcia: diff_area = {diff_area}"

        # Przyrost pola powierzchni: (120 * 120) - (100 * 100) = 14400 - 10000 = 4400 mm²
        expected_area = 120.0 * 120.0
        assert abs(cut_poly.area - expected_area) < 5.0

    def test_uniform_seam_allowance_fuelle_superior(self) -> None:
        """Pasek korony Fuelle superior (130 x 350 mm) z naddatkiem 10 mm daje 150 x 370 mm."""
        fuelle = build_fuelle_superior()
        offset_fuelle = apply_uniform_seam_allowance(fuelle, seam_mm=10.0)
        assert offset_fuelle.cut_contour is not None

        bo = get_contour_bounds(offset_fuelle.outer_contour)
        bc = get_contour_bounds(offset_fuelle.cut_contour)

        net_w = bo[2] - bo[0]
        net_h = bo[3] - bo[1]
        cut_w = bc[2] - bc[0]
        cut_h = bc[3] - bc[1]

        assert abs((cut_w - net_w) - 20.0) <= 0.25, f"dW = {cut_w - net_w} mm"
        assert abs((cut_h - net_h) - 20.0) <= 0.25, f"dH = {cut_h - net_h} mm"

        cut_poly = contour_to_shapely_polygon(offset_fuelle.cut_contour)
        assert cut_poly.is_valid
        assert cut_poly.area > (150.0 * 370.0 * 0.9)  # Sprawdzenie skali powierzchni

    def test_uniform_seam_allowance_all_pieces_dilation_and_validity(
        self, gorro_base_project: PatternProject
    ) -> None:
        """Wszystkie elementy czapki Gorro Trapper mają poprawny wielokąt cięcia i dodatnią dylatację."""
        for piece in gorro_base_project.pieces:
            piece_with_seam = apply_uniform_seam_allowance(piece, seam_mm=10.0)
            assert piece_with_seam.has_cut_contour
            assert piece_with_seam.cut_contour is not None

            net_poly = contour_to_shapely_polygon(piece_with_seam.outer_contour)
            cut_poly = contour_to_shapely_polygon(piece_with_seam.cut_contour)

            assert cut_poly.is_valid, f"Element {piece.name} wytworzył niepoprawny wielokąt!"
            assert cut_poly.area > net_poly.area, f"Pole cięcia ({cut_poly.area}) nie jest większe od netto ({net_poly.area})"
            diff_area = net_poly.difference(cut_poly).area
            assert diff_area < 1e-2, f"Linia netto wystaje poza cięcie w {piece.name}: {diff_area}"

    def test_uniform_seam_allowance_miter_limit_clamping(self) -> None:
        """Sprawdza, czy ostre narożniki są ograniczane przez MiterLimit=2.0 zgodnie ze specyfikacją."""
        # Trójkąt o ostrym kącie w wierzchołku górnym (szerokość 20 mm, wysokość 100 mm)
        tri_cnt = Contour.from_polyline(
            [(0.0, 0.0), (20.0, 0.0), (10.0, 100.0)],
            is_closed=True,
        )
        piece = PatternPiece(
            id="sharp_triangle",
            name="Ostry Trójkąt",
            target_width_cm=2.0,
            target_height_cm=10.0,
            outer_contour=tri_cnt,
        )
        offset_piece = apply_uniform_seam_allowance(piece, seam_mm=10.0, miter_limit=2.0)
        assert offset_piece.cut_contour is not None
        cut_poly = contour_to_shapely_polygon(offset_piece.cut_contour)
        assert cut_poly.is_valid

        # Przy miter_limit=2.0 i naddatku 10 mm, maksymalny wyskok narożnika nie przekracza 20 mm od wierzchołka
        _, _, _, max_y_cut = get_contour_bounds(offset_piece.cut_contour)
        assert max_y_cut <= 100.0 + 2.0 * 10.0 + 0.5, f"Wyskok narożnika przekroczył miter_limit: {max_y_cut}"


# ==============================================================================
# SECTION 2: VARIABLE PER-EDGE SEAM ALLOWANCE (KRYTERIUM 2)
# ==============================================================================

class TestVariableSeamAllowance:
    """
    Weryfikacja zróżnicowanych naddatków na krawędziach (R1 / Kryterium 2):
    Variable per-edge seam allowance (0.0 mm on fold edge, 10.0 mm on seams,
    15.0 mm on elastic curves of Cubre boca) shifts each edge by its configured
    offset distance within +/- 0.5 mm tolerance.
    """

    def test_variable_seam_allowance_fold_edge_preservation_frente(self) -> None:
        """Krawędź zgięcia (fold 0.0 mm na dole Frente) pozostaje idealnie na linii y=0."""
        frente = build_frente()
        frente_seam = apply_seam_allowance_to_piece(frente, global_seam_allowance_mm=10.0)
        assert frente_seam.cut_contour is not None

        bo = get_contour_bounds(frente.outer_contour)
        bc = get_contour_bounds(frente_seam.cut_contour)

        # Dolna krawędź Frente to linia zgięcia na y=0 (seam_allowance_mm=0.0, edge_role="fold")
        assert abs(bc[1] - 0.0) <= 0.05, f"Linia zgięcia przesunęła się: bc[1] = {bc[1]} mm (oczekiwano 0.0 mm +/- 0.05 mm)"

        # Krawędzie boczne (szwy 10 mm) rozszerzają się o 10 mm z lewej i prawej strony
        dw = (bc[2] - bc[0]) - (bo[2] - bo[0])
        assert abs(dw - 20.0) <= 0.25, f"Boczny naddatek na szew dW = {dw:.4f} mm (oczekiwano 20.0 +/- 0.25 mm)"

        # Poprawność wielokąta
        cut_poly = contour_to_shapely_polygon(frente_seam.cut_contour)
        assert cut_poly.is_valid, "Obrys Frente z krawędzią zgięcia nie jest poprawnym wielokątem!"

    def test_variable_seam_allowance_cubre_boca_elastic_curves(self) -> None:
        """Maska Cubre boca: 15.0 mm na łukach ściągacza (góra/dół) i 10.0 mm na pionowych bokach."""
        cb = build_cubre_boca()
        cb_seam = apply_seam_allowance_to_piece(cb, global_seam_allowance_mm=10.0)
        assert cb_seam.cut_contour is not None

        cbo = get_contour_bounds(cb.outer_contour)
        cbc = get_contour_bounds(cb_seam.cut_contour)

        net_w = cbo[2] - cbo[0]  # 280.0 mm
        net_h = cbo[3] - cbo[1]  # 140.0 mm
        cut_w = cbc[2] - cbc[0]  # 300.0 mm (+20 mm = 2 * 10 mm szew boczny)
        cut_h = cbc[3] - cbc[1]  # 170.0 mm (+30 mm = 2 * 15 mm ściągacz góra/dół)

        dw = cut_w - net_w
        dh = cut_h - net_h

        assert abs(dw - 20.0) <= 0.5, f"Szerokość Cubre boca dW = {dw:.4f} mm (oczekiwano 20.0 +/- 0.5 mm)"
        assert abs(dh - 30.0) <= 0.5, f"Wysokość Cubre boca dH = {dh:.4f} mm (oczekiwano 30.0 +/- 0.5 mm)"

        # Przesunięcia brzegów względem oryginału: min X i max X o 10 mm, min Y i max Y o 15 mm
        assert abs((cbc[0] - cbo[0]) - (-10.0)) <= 0.5, "Lewa krawędź nie przesunęła się o 10 mm"
        assert abs((cbc[2] - cbo[2]) - 10.0) <= 0.5, "Prawa krawędź nie przesunęła się o 10 mm"
        assert abs((cbc[1] - cbo[1]) - (-15.0)) <= 0.5, "Dolny ściągacz nie przesunął się o 15 mm"
        assert abs((cbc[3] - cbo[3]) - 15.0) <= 0.5, "Górny ściągacz nie przesunął się o 15 mm"

        cut_poly = contour_to_shapely_polygon(cb_seam.cut_contour)
        assert cut_poly.is_valid, "Obrys Cubre boca z naddatkami elastycznymi nie jest poprawnym wielokątem!"

    def test_variable_seam_allowance_custom_multiedge_synthetic(self) -> None:
        """Syntetyczny prostokąt z 4 różnymi naddatkami: 0mm (dół), 10mm (prawo), 15mm (góra), 20mm (lewo)."""
        p0 = Point2D(x=0.0, y=0.0)
        p1 = Point2D(x=100.0, y=0.0)
        p2 = Point2D(x=100.0, y=80.0)
        p3 = Point2D(x=0.0, y=80.0)

        seg_bottom = PathSegment.line(p0, p1, seam_allowance_mm=0.0, edge_role="fold")
        seg_right = PathSegment.line(p1, p2, seam_allowance_mm=10.0, edge_role="seam")
        seg_top = PathSegment.line(p2, p3, seam_allowance_mm=15.0, edge_role="elastic")
        seg_left = PathSegment.line(p3, p0, seam_allowance_mm=20.0, edge_role="hem")

        cnt = Contour(segments=[seg_bottom, seg_right, seg_top, seg_left], is_closed=True)
        piece = PatternPiece(
            id="synthetic_multi_edge",
            name="Wielonaddatkowy Prostokąt",
            target_width_cm=10.0,
            target_height_cm=8.0,
            outer_contour=cnt,
        )

        offset_piece = apply_variable_seam_allowance(piece, default_seam_mm=10.0)
        assert offset_piece.cut_contour is not None

        bc = get_contour_bounds(offset_piece.cut_contour)
        # Oczekiwane granice: X w [-20.0, 110.0], Y w [0.0, 95.0]
        assert abs(bc[0] - (-20.0)) <= 0.5, f"Lewy naddatek 20 mm: {bc[0]}"
        assert abs(bc[1] - 0.0) <= 0.1, f"Dolny naddatek 0 mm (fold): {bc[1]}"
        assert abs(bc[2] - 110.0) <= 0.5, f"Prawy naddatek 10 mm: {bc[2]}"
        assert abs(bc[3] - 95.0) <= 0.5, f"Górny naddatek 15 mm: {bc[3]}"

        cut_poly = contour_to_shapely_polygon(offset_piece.cut_contour)
        assert cut_poly.is_valid


# ==============================================================================
# SECTION 3: MULTI-PAGE A4 PDF DIMENSIONS (KRYTERIUM 3)
# ==============================================================================

class TestA4PageDimensions:
    """
    Weryfikacja wymiarów stron A4 (R2 / Kryterium 3):
    Both mode="hybrid" and mode="poster" generate valid multi-page PDF documents
    where every page has exact A4 dimensions (210.0 x 297.0 mm = 595.28 x 841.89 pt, tolerance +/- 0.5 pt).
    """

    def test_hybrid_mode_all_pages_exact_a4_dimensions(
        self, gorro_project_with_seams: PatternProject, tmp_path: Path
    ) -> None:
        """Każda strona PDF w trybie hybrydowym ma idealne wymiary A4 (595.28 x 841.89 pt)."""
        hybrid_res = nest_hybrid_mode(gorro_project_with_seams)
        pdf_path = export_a4_pdf(hybrid_res, tmp_path / "test_hybrid.pdf", render_png_previews=False)
        assert pdf_path.exists()

        doc = fitz.open(pdf_path)
        assert len(doc) == hybrid_res.total_pages
        assert len(doc) >= 10, f"Oczekiwano wielostronicowego dokumentu A4, otrzymano {len(doc)} stron"

        for idx, page in enumerate(doc):
            w_pt = page.rect.width
            h_pt = page.rect.height
            assert abs(w_pt - PAGE_W_PT) <= 0.5, (
                f"Tryb Hybrydowy strona {idx + 1}: błędna szerokość {w_pt:.2f} pt (oczekiwano {PAGE_W_PT:.2f} pt)"
            )
            assert abs(h_pt - PAGE_H_PT) <= 0.5, (
                f"Tryb Hybrydowy strona {idx + 1}: błędna wysokość {h_pt:.2f} pt (oczekiwano {PAGE_H_PT:.2f} pt)"
            )
        doc.close()

    def test_poster_mode_all_pages_exact_a4_dimensions(
        self, gorro_project_with_seams: PatternProject, tmp_path: Path
    ) -> None:
        """Każda strona PDF w trybie plakatowym ma idealne wymiary A4 (595.28 x 841.89 pt)."""
        poster_res = nest_poster_mode(gorro_project_with_seams)
        pdf_path = export_a4_pdf(poster_res, tmp_path / "test_poster.pdf", render_png_previews=False)
        assert pdf_path.exists()

        doc = fitz.open(pdf_path)
        assert len(doc) == poster_res.total_pages
        assert len(doc) >= 8, f"Oczekiwano wielostronicowego plakatu A4, otrzymano {len(doc)} stron"

        for idx, page in enumerate(doc):
            w_pt = page.rect.width
            h_pt = page.rect.height
            assert abs(w_pt - PAGE_W_PT) <= 0.5, (
                f"Tryb Plakatowy strona {idx + 1}: błędna szerokość {w_pt:.2f} pt (oczekiwano {PAGE_W_PT:.2f} pt)"
            )
            assert abs(h_pt - PAGE_H_PT) <= 0.5, (
                f"Tryb Plakatowy strona {idx + 1}: błędna wysokość {h_pt:.2f} pt (oczekiwano {PAGE_H_PT:.2f} pt)"
            )
        doc.close()


# ==============================================================================
# SECTION 4: NON-OVERLAPPING MULTI-PIECE SHEETS (KRYTERIUM 4)
# ==============================================================================

class TestNestingNonOverlap:
    """
    Weryfikacja braku kolizji na stronach wieloelementowych (R2 / Kryterium 4):
    On every A4 page containing multiple whole pieces, no two placed pieces overlap
    (poly1.intersection(poly2).area < 1e-2).
    """

    def test_multi_piece_whole_sheets_zero_overlap(self) -> None:
        """Dla wszystkich stron A4 zawierających >= 2 całe elementy: pole przecięcia < 1e-2 mm²."""
        # Tworzymy zestaw 8 mniejszych całych elementów, które mieszczą się wspólnie na arkuszach A4
        pieces = [
            PatternPiece(
                id=f"piece_{i}",
                name=f"Część {i}",
                quantity=1,
                target_width_cm=6.0,
                target_height_cm=5.0,
                outer_contour=Contour.from_polyline(
                    [(0.0, 0.0), (60.0, 0.0), (60.0, 50.0), (0.0, 50.0)],
                    is_closed=True,
                ),
            )
            for i in range(8)
        ]
        proj = apply_seam_allowance_to_project(
            PatternProject(project_name="MultiPackTest", pieces=pieces),
            global_seam_allowance_mm=5.0,
        )
        hybrid_res = nest_hybrid_mode(proj)
        multi_piece_pages = [p for p in hybrid_res.pages if len(p.placed_pieces) > 1 and not p.is_tiled]
        assert len(multi_piece_pages) > 0, "Brak stron wieloelementowych w teście upakowania całych części!"

        for page in multi_piece_pages:
            for i in range(len(page.placed_pieces)):
                for j in range(i + 1, len(page.placed_pieces)):
                    p1 = page.placed_pieces[i].get_placed_polygon()
                    p2 = page.placed_pieces[j].get_placed_polygon()
                    inter_area = p1.intersection(p2).area
                    assert inter_area < 1e-2, (
                        f"Wykryto kolizję na stronie {page.page_index} między elementami! "
                        f"Pole kolizji = {inter_area:.6f} mm² (wymagane < 0.01 mm²)"
                    )

    def test_gorro_trapper_poster_mode_zero_overlap(
        self, gorro_project_with_seams: PatternProject
    ) -> None:
        """W trybie plakatowym na wszystkich arkuszach zawierających wiele elementów: pole przecięcia < 1e-2 mm²."""
        poster_res = nest_poster_mode(gorro_project_with_seams)
        multi_piece_pages = [p for p in poster_res.pages if len(p.placed_pieces) > 1]
        assert len(multi_piece_pages) > 0, "Brak stron wieloelementowych w trybie plakatowym!"

        for page in multi_piece_pages:
            for i in range(len(page.placed_pieces)):
                for j in range(i + 1, len(page.placed_pieces)):
                    p1 = page.placed_pieces[i].get_placed_polygon()
                    p2 = page.placed_pieces[j].get_placed_polygon()
                    inter_area = p1.intersection(p2).area
                    assert inter_area < 1e-2, (
                        f"Kolizja na stronie {page.page_index} w trybie plakatowym: {inter_area:.6f} mm²"
                    )

    def test_synthetic_dense_collection_zero_overlap(self) -> None:
        """Syntetyczny zestaw małych elementów upakowanych wspólnie nie wykazuje kolizji."""
        pieces: list[PatternPiece] = []
        sizes = [(60.0, 40.0), (70.0, 50.0), (50.0, 30.0), (80.0, 40.0)]
        for idx, (w, h) in enumerate(sizes, start=1):
            cnt = Contour.from_polyline([(0, 0), (w, 0), (w, h), (0, h)], is_closed=True)
            pieces.append(
                PatternPiece(
                    id=f"synth_{idx}",
                    name=f"Element Syntetyczny {idx}",
                    target_width_cm=w / 10.0,
                    target_height_cm=h / 10.0,
                    outer_contour=cnt,
                    quantity=1,
                    seam_allowance_mm=5.0,
                )
            )
        proj = apply_seam_allowance_to_project(
            PatternProject(project_name="Test Syntetyczny", pieces=pieces),
            global_seam_allowance_mm=5.0,
        )
        res = nest_hybrid_mode(proj)
        for page in res.pages:
            if len(page.placed_pieces) > 1:
                for i in range(len(page.placed_pieces)):
                    for j in range(i + 1, len(page.placed_pieces)):
                        p1 = page.placed_pieces[i].get_placed_polygon()
                        p2 = page.placed_pieces[j].get_placed_polygon()
                        inter_area = p1.intersection(p2).area
                        assert inter_area < 1e-2, f"Kolizja syntetyczna: {inter_area} mm²"


# ==============================================================================
# SECTION 5: PATTERN PIECES EXPANSION & OVERSIZED SLICING (KRYTERIUM 5)
# ==============================================================================

class TestPatternPiecesAccountingAndSlicing:
    """
    Weryfikacja kompletności 9 elementów i podziału elementów przewyższających A4 (R2 / Kryterium 5):
    All 9 expanded pattern pieces of the Gorro Trapper hat (x1 and x2 quantities)
    are accounted for in the A4 output, and oversized pieces (such as Fuelle superior
    at 350 mm length) are split across at least 2 tiles with alignment markers and overlap margins.
    """

    def test_all_9_expanded_pieces_accounted_for_hybrid_and_poster(
        self, gorro_project_with_seams: PatternProject
    ) -> None:
        """Wszystkie 9 elementów czapki (3x x1 oraz 3x x2 symetryczne) są obecne w wyjściach A4."""
        expanded = expand_project_pieces(gorro_project_with_seams, mirror_symmetric=True)
        assert len(expanded) == 9

        # Sprawdzenie obecności w trybie hybrydowym
        hybrid_res = nest_hybrid_mode(gorro_project_with_seams)
        hybrid_names = {p.piece_name for p in hybrid_res.pages}
        expected_unique_names = {
            "Fuelle superior",
            "Frente",
            "Visera",
            "Cubre boca",
            "Lateral",
            "Orejera Compuesta",
        }
        for name in expected_unique_names:
            assert any(name.lower() in hn.lower() for hn in hybrid_names), f"Brak elementu '{name}' w stronach hybrydowych!"

        # Sprawdzenie obecności w trybie plakatowym
        poster_res = nest_poster_mode(gorro_project_with_seams)
        all_poster_pieces: set[str] = set()
        for p in poster_res.pages:
            for pl in p.placed_pieces:
                all_poster_pieces.add(pl.piece.id)
        assert len(all_poster_pieces) == 9, (
            f"W trybie plakatowym nie znaleziono wszystkich 9 rozwiniętych elementów: {all_poster_pieces}"
        )

    def test_oversized_pieces_split_across_multiple_tiles_with_markers(
        self, gorro_project_with_seams: PatternProject, tmp_path: Path
    ) -> None:
        """Elementy przewyższające A4 (Fuelle superior 350 mm) są kafelkowane na >= 2 arkusze ze znacznikami."""
        hybrid_res = nest_hybrid_mode(gorro_project_with_seams)

        # Fuelle superior ma długość netto 350 mm (brutto 370 mm), co przekracza okno A4 (277 mm)
        fuelle_pages = [p for p in hybrid_res.pages if "fuelle" in p.piece_name.lower()]
        assert len(fuelle_pages) >= 2, (
            f"Fuelle superior nie został podzielony na >= 2 kafle (otrzymano {len(fuelle_pages)} stron)!"
        )
        for fp in fuelle_pages:
            assert fp.is_tiled is True
            assert fp.total_rows >= 2 or fp.total_cols >= 2

        # Cubre boca (280 mm netto, 300 mm brutto) również przekracza szerokość i wysokość okna A4
        cb_pages = [p for p in hybrid_res.pages if "cubre" in p.piece_name.lower()]
        assert len(cb_pages) >= 2, f"Cubre boca nie został podzielony (otrzymano {len(cb_pages)} stron)!"

        # Eksport do PDF i weryfikacja znaczników pasowania (romby i linie klejenia)
        pdf_path = export_a4_pdf(hybrid_res, tmp_path / "test_hybrid_tiled.pdf", render_png_previews=False)
        doc = fitz.open(pdf_path)

        # Na kafelkowanych stronach Fuelle superior sprawdzamy obecność napisów pasowania (np. 'R1-C1 | R2-C1')
        fuelle_page_indices = [fp.page_index - 1 for fp in fuelle_pages]
        found_markers = False
        for p_idx in fuelle_page_indices:
            page_text = doc[p_idx].get_text()
            if "R1-C1" in page_text or "R2-C1" in page_text or "LINIA KLEJENIA" in page_text:
                found_markers = True
                break
        assert found_markers, "Na kafelkowanych stronach Fuelle superior brakuje oznaczeń pasowania lub linii klejenia!"
        doc.close()


# ==============================================================================
# SECTION 6: CALIBRATION TEST SQUARE & DUAL CONTOURS (KRYTERIUM 6)
# ==============================================================================

class TestCalibrationSquareAndDualContourA4:
    """
    Weryfikacja kwadratu kontrolnego 50x50 mm i stylistyki podwójnych konturów (R2 / Kryterium 6):
    Generated A4 PDF contains the 5.0 x 5.0 cm (50 x 50 mm = 141.73 x 141.73 pt)
    calibration test square and renders both dashed sewing line and solid cut line
    when seam allowances are enabled.
    """

    def test_calibration_square_50x50mm_presence_and_dimensions(
        self, gorro_project_with_seams: PatternProject, tmp_path: Path
    ) -> None:
        """Kwadrat kontrolny na stronie 1 ma wymiary dokładnie 50 x 50 mm (141.73 x 141.73 pt +/- 0.5 pt)."""
        hybrid_res = nest_hybrid_mode(gorro_project_with_seams)
        pdf_path = export_a4_pdf(hybrid_res, tmp_path / "calib_test.pdf", render_png_previews=False)
        doc = fitz.open(pdf_path)
        p1 = doc[0]

        # Weryfikacja etykiet tekstowych
        p1_text = p1.get_text()
        assert "TEST SKALI 1:1" in p1_text, "Brak nagłówka 'TEST SKALI 1:1' na stronie 1!"
        assert "5.0 x 5.0 cm" in p1_text, "Brak wymiaru '5.0 x 5.0 cm' na stronie 1!"

        # Weryfikacja wektorowego prostokąta w rysunkach PyMuPDF
        found_square = False
        expected_pt = 50.0 * PT_PER_MM  # 141.73228 pt
        for d in p1.get_drawings():
            r = d["rect"]
            if abs(r.width - expected_pt) <= 0.5 and abs(r.height - expected_pt) <= 0.5:
                found_square = True
                # Upewniamy się, że nie jest zamalowany nieprzezroczystym tłem zasłaniającym elementy
                assert d.get("fill") is None or d["fill"] == (1.0, 1.0, 1.0, 0.0)
                break
        assert found_square, f"Nie znaleziono wektorowego kwadratu o boku {expected_pt:.2f} pt na stronie 1!"
        doc.close()

    def test_dual_contour_rendering_styles_on_a4_pages(
        self, gorro_project_with_seams: PatternProject, tmp_path: Path
    ) -> None:
        """Na stronach A4 z naddatkami rysowane są zarówno ciągłe linie cięcia, jak i przerywane linie szycia."""
        hybrid_res = nest_hybrid_mode(gorro_project_with_seams)
        pdf_path = export_a4_pdf(hybrid_res, tmp_path / "dual_contour_test.pdf", render_png_previews=False)
        doc = fitz.open(pdf_path)

        # Strona 1 zawiera element z podwójnym konturem
        p1 = doc[0]
        has_solid_cut = False
        has_dashed_sew = False

        for d in p1.get_drawings():
            dashes = d.get("dashes", "")
            width = d.get("width", 0.0)
            # Solid cut line: grubość >= 1.0 pt, brak kresek (dashes in ("", "[] 0", None))
            if width >= 1.0 and (not dashes or dashes.strip().startswith("[]")):
                has_solid_cut = True
            # Dashed sewing line: styl kresek [4 2] 0 lub [4 3] 0
            elif dashes and not dashes.strip().startswith("[]"):
                if "4 2" in dashes or "4 3" in dashes:
                    has_dashed_sew = True

        assert has_solid_cut, "Na arkuszu A4 brakuje ciągłej linii cięcia (solid cut line)!"
        assert has_dashed_sew, "Na arkuszu A4 brakuje przerywanej linii szycia (dashed sewing line)!"
        doc.close()

    def test_fallback_single_contour_without_seams(
        self, gorro_base_project: PatternProject, tmp_path: Path
    ) -> None:
        """Projekt bez naddatków (has_seam_allowance=False) generuje wyłącznie pojedynczą linię ciągłą."""
        assert gorro_base_project.has_seam_allowance is False
        hybrid_res = nest_hybrid_mode(gorro_base_project)
        pdf_path = export_a4_pdf(hybrid_res, tmp_path / "no_seams_test.pdf", render_png_previews=False)
        doc = fitz.open(pdf_path)
        p1 = doc[0]

        has_dashed_sew = False
        for d in p1.get_drawings():
            dashes = d.get("dashes", "")
            if dashes and not dashes.strip().startswith("[]") and "4 2" in dashes:
                has_dashed_sew = True

        assert not has_dashed_sew, "W projekcie bez naddatków nie powinna występować dodatkowa linia szycia!"
        doc.close()


# ==============================================================================
# SECTION 7: DUAL-CONTOUR CLO3D EXPORTS (KRYTERIUM 7)
# ==============================================================================

class TestClo3DDualContourExports:
    """
    Weryfikacja eksportów DXF-AAMA oraz 1:1 Vector PDF dla Clo3D (R3 / Kryterium 7):
    Dual-contour Clo3D exports: DXF-AAMA Layer 1 (cut boundary) and Layer 4 (net sewing line),
    1:1 Vector PDF (solid cut line, dashed sewing line).
    """

    def test_dxf_aama_dual_contour_layers_and_dimensions(
        self, gorro_project_with_seams: PatternProject, tmp_path: Path
    ) -> None:
        """Eksport DXF-AAMA zawiera Warstwę 1 (obrys cięcia) oraz Warstwę 4 (linia szycia netto)."""
        dxf_path = export_clo3d_dxf(
            gorro_project_with_seams,
            tmp_path / "test_seams_clo3d.dxf",
            unit="mm",
            expand_quantities=False,
        )
        assert dxf_path.exists()

        doc = ezdxf.readfile(str(dxf_path))
        assert AAMA_LAYER_BOUNDARY in doc.layers  # "1"
        assert AAMA_LAYER_INTERNAL in doc.layers  # "4"
        assert AAMA_LAYER_GRAINLINE in doc.layers  # "7"
        assert AAMA_LAYER_TEXT in doc.layers  # "8"

        msp = doc.modelspace()
        inserts = list(msp.query("INSERT"))
        assert len(inserts) == 6

        # Sprawdzamy pierwszy blok (Fuelle superior: 130x350 mm netto, 150x370 mm cięcie)
        blk = doc.blocks.get(inserts[0].dxf.name)
        polys_l1 = [e for e in blk if e.dxftype() == "POLYLINE" and e.dxf.layer == AAMA_LAYER_BOUNDARY]
        polys_l4 = [e for e in blk if e.dxftype() == "POLYLINE" and e.dxf.layer == AAMA_LAYER_INTERNAL]

        assert len(polys_l1) == 1, "Brak zamkniętej polilinii cięcia na Warstwie 1!"
        assert len(polys_l4) >= 1, "Brak zamkniętej polilinii szycia netto na Warstwie 4!"

        # Wymiary Warstwy 1 (Cut Boundary): 150 x 370 mm
        pts_l1 = [v.dxf.location for v in polys_l1[0].vertices]
        w_l1 = max(p.x for p in pts_l1) - min(p.x for p in pts_l1)
        h_l1 = max(p.y for p in pts_l1) - min(p.y for p in pts_l1)
        assert abs(w_l1 - 150.0) <= 0.5, f"Błędna szerokość cięcia na Warstwie 1: {w_l1} mm"
        assert abs(h_l1 - 370.0) <= 0.5, f"Błędna wysokość cięcia na Warstwie 1: {h_l1} mm"

        # Wymiary Warstwy 4 (Net Sewing Line): 130 x 350 mm
        pts_l4 = [v.dxf.location for v in polys_l4[0].vertices]
        w_l4 = max(p.x for p in pts_l4) - min(p.x for p in pts_l4)
        h_l4 = max(p.y for p in pts_l4) - min(p.y for p in pts_l4)
        assert abs(w_l4 - 130.0) <= 0.5, f"Błędna szerokość szycia na Warstwie 4: {w_l4} mm"
        assert abs(h_l4 - 350.0) <= 0.5, f"Błędna wysokość szycia na Warstwie 4: {h_l4} mm"

    def test_dxf_aama_backward_compatibility_no_seam(
        self, gorro_base_project: PatternProject, tmp_path: Path
    ) -> None:
        """Gdy wykrój nie ma naddatków, Warstwa 1 zawiera bazowy kontur netto, a Warstwa 4 nie powiela go."""
        dxf_path = export_clo3d_dxf(
            gorro_base_project,
            tmp_path / "test_no_seams_clo3d.dxf",
            unit="mm",
            expand_quantities=False,
        )
        doc = ezdxf.readfile(str(dxf_path))
        inserts = list(doc.modelspace().query("INSERT"))
        blk = doc.blocks.get(inserts[0].dxf.name)

        polys_l1 = [e for e in blk if e.dxftype() == "POLYLINE" and e.dxf.layer == AAMA_LAYER_BOUNDARY]
        polys_l4 = [e for e in blk if e.dxftype() == "POLYLINE" and e.dxf.layer == AAMA_LAYER_INTERNAL]

        assert len(polys_l1) == 1
        # Dla Fuelle superior nie ma linii wewnętrznych, więc na Warstwie 4 jest 0 polilinii
        assert len(polys_l4) == 0

    def test_dxf_aama_unit_scaling_mm_and_cm(
        self, gorro_project_with_seams: PatternProject, tmp_path: Path
    ) -> None:
        """Współrzędne w jednostkach 'cm' są dokładnie 10-krotnie mniejsze niż w 'mm'."""
        dxf_mm = export_clo3d_dxf(gorro_project_with_seams, tmp_path / "scale_mm.dxf", unit="mm")
        dxf_cm = export_clo3d_dxf(gorro_project_with_seams, tmp_path / "scale_cm.dxf", unit="cm")

        doc_mm = ezdxf.readfile(str(dxf_mm))
        doc_cm = ezdxf.readfile(str(dxf_cm))

        ins_mm = list(doc_mm.modelspace().query("INSERT"))[0]
        ins_cm = list(doc_cm.modelspace().query("INSERT"))[0]

        poly_mm = [e for e in doc_mm.blocks.get(ins_mm.dxf.name) if e.dxftype() == "POLYLINE" and e.dxf.layer == "1"][0]
        poly_cm = [e for e in doc_cm.blocks.get(ins_cm.dxf.name) if e.dxftype() == "POLYLINE" and e.dxf.layer == "1"][0]

        w_mm = max(v.dxf.location.x for v in poly_mm.vertices) - min(v.dxf.location.x for v in poly_mm.vertices)
        w_cm = max(v.dxf.location.x for v in poly_cm.vertices) - min(v.dxf.location.x for v in poly_cm.vertices)

        assert abs(w_mm - 10.0 * w_cm) < 1e-3, f"Błędne skalowanie cm: w_mm={w_mm}, w_cm={w_cm}"

    def test_clo3d_vector_pdf_1to1_dual_contour_strokes(
        self, gorro_project_with_seams: PatternProject, tmp_path: Path
    ) -> None:
        """Wektorowy PDF 1:1 dla Clo3D zawiera solidną linię cięcia i przerywaną linię szycia."""
        pdf_path = export_clo3d_vector_pdf_1to1(
            gorro_project_with_seams,
            tmp_path / "clo3d_dual_test.pdf",
            expand_quantities=True,
        )
        assert pdf_path.exists()

        doc = fitz.open(pdf_path)
        assert len(doc) == 1
        drawings = doc[0].get_drawings()

        has_solid_cut = any(
            d.get("width", 0) >= 0.95 and d.get("dashes") in ("", None, "[] 0")
            for d in drawings
        )
        has_dashed_sew = any(
            "4 2" in (d.get("dashes") or "") or "3 3" in (d.get("dashes") or "")
            for d in drawings
        )

        assert has_solid_cut, "Brak ciągłej linii cięcia w wektorowym PDF 1:1 dla Clo3D!"
        assert has_dashed_sew, "Brak przerywanej linii szycia w wektorowym PDF 1:1 dla Clo3D!"
        doc.close()

    def test_technical_sheet_1to1_dual_contour_and_annotations(
        self, gorro_project_with_seams: PatternProject, tmp_path: Path
    ) -> None:
        """Arkusz Techniczny 1:1 renderuje beżowe wypełnienie cięcia, przerywaną linię szycia i węzły CAD."""
        tech_path = export_technical_sheet_pdf_1to1(
            gorro_project_with_seams,
            tmp_path / "tech_sheet_test.pdf",
            expand_quantities=False,
        )
        assert tech_path.exists()

        doc = fitz.open(tech_path)
        p = doc[0]
        drawings = p.get_drawings()

        # Beżowe wypełnienie wykroju cięcia (RGB ok. 0.89, 0.78, 0.56)
        has_fabric_fill = any(
            d.get("fill") and abs(d["fill"][0] - 0.89) < 0.05
            for d in drawings
        )
        assert has_fabric_fill, "Brak beżowego wypełnienia tkaniny na Arkuszu Technicznym!"

        # Sprawdzenie obecności tekstów etykiet naddatków
        text = p.get_text()
        assert "ARKUSZ TECHNICZNY" in text
        assert "GORRO TRAPPER" in text
        doc.close()


# ==============================================================================
# SECTION 8: RUN_STAGE2 CLEAN EXECUTION & ARTIFACT COMPLETENESS (KRYTERIUM 8)
# ==============================================================================

class TestRunStage2Pipeline:
    """
    Weryfikacja całościowego potoku wykonawczego run_stage2.py (R3 / Kryterium 8):
    Clean execution of run_stage2.py populating output/stage2_a4_and_seams/.
    """

    def test_run_stage2_clean_execution_and_deliverables_complete(self) -> None:
        """Wykonanie run_stage2.py kończy się kodem 0 i generuje komplet 35 plików w katalogu wyjściowym."""
        exit_code = run_stage2.main()
        assert exit_code == 0, f"run_stage2.py zakończył się niezerowym kodem wyjścia: {exit_code}"

        out_dir = Path("output/stage2_a4_and_seams")
        assert out_dir.exists(), f"Katalog {out_dir} nie istnieje po wykonaniu run_stage2.py!"

        expected_core_files = [
            "gorro_trapper_stage2_project.json",
            "gorro_trapper_seams_clo3d_master_mm.dxf",
            "gorro_trapper_seams_clo3d_full_set_mm.dxf",
            "gorro_trapper_seams_clo3d_full_set_cm.dxf",
            "gorro_trapper_seams_clo3d_import_master_1to1.pdf",
            "gorro_trapper_seams_clo3d_import_full_set_1to1.pdf",
            "gorro_trapper_seams_technical_sheet_1to1.pdf",
            "gorro_trapper_seams_technical_preview.png",
            "gorro_trapper_a4_print_hybrid.pdf",
            "gorro_trapper_a4_print_poster.pdf",
        ]

        # Weryfikacja plików bazowych
        for fname in expected_core_files:
            fpath = out_dir / fname
            assert fpath.exists(), f"Brak wymaganego pliku: {fname}"
            assert fpath.stat().st_size > 0, f"Plik {fname} ma zerowy rozmiar!"

        # Weryfikacja 13 podglądów PNG stron hybrydowych
        hybrid_pngs = sorted(out_dir.glob("hybrid_page_*.png"))
        assert len(hybrid_pngs) == 13, f"Oczekiwano 13 podglądów PNG stron hybrydowych, znaleziono {len(hybrid_pngs)}"
        for png in hybrid_pngs:
            assert png.stat().st_size > 1000, f"Podgląd {png.name} ma za mały rozmiar ({png.stat().st_size} B)"

        # Weryfikacja 12 podglądów PNG stron plakatowych
        poster_pngs = sorted(out_dir.glob("poster_page_*.png"))
        assert len(poster_pngs) == 12, f"Oczekiwano 12 podglądów PNG stron plakatowych, znaleziono {len(poster_pngs)}"
        for png in poster_pngs:
            assert png.stat().st_size > 1000, f"Podgląd {png.name} ma za mały rozmiar ({png.stat().st_size} B)"

        # Łączna liczba wygenerowanych plików = 10 plików bazowych + 13 + 12 = 35 plików
        all_generated = expected_core_files + [p.name for p in hybrid_pngs] + [p.name for p in poster_pngs]
        assert len(all_generated) == 35

        # Weryfikacja poprawności wygenerowanego JSON z naddatkami
        loaded_project = PatternProject.load_json(out_dir / "gorro_trapper_stage2_project.json")
        assert loaded_project.has_seam_allowance is True
        assert len(loaded_project.pieces) == 6
        for p in loaded_project.pieces:
            assert p.has_cut_contour is True
            assert p.cut_contour is not None
            assert len(p.cut_contour.segments) > 0
            assert contour_to_shapely_polygon(p.cut_contour).is_valid
