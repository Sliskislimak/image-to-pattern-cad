"""
Moduł Eksportera A4 PDF (Milestone 2 - R2).

Generuje wielostronicowy dokument wektorowy PDF w formacie ISO A4 zoptymalizowany
do domowego wydruku 1:1 i precyzyjnego sklejania wykrojów:
1. Dokładne wymiary każdej strony: 210.0 x 297.0 mm = 595.28 x 841.89 pt (+/- 0.5 pt).
2. Podwójne kontury (Dual Contours):
   - Solidna czarna linia cięcia (Cut Line, width 1.25 pt)
   - Przerywana niebieska/szara linia szycia (Sewing Line, dashes="[4 2] 0", width 0.9 pt)
3. Czerwone linie wewnętrzne (zaszywki, linie pomocnicze) i zielona nitka prosta (Grainline) ze strzałkami.
4. Etykiety naddatków na szwy przy krawędziach ("+10 mm", "+15 mm elastizar", "0 mm doblar/fold").
5. Ramka marginesu drukarki (10.0 mm).
6. Strefy i linie zakładki klejenia (15.0 mm z etykietą "LINIA SKLEJENIA / GLUE LINE (15 mm)").
7. Pasery dopasowania w kształcie rombów (Alignment Diamonds) z oznaczeniem "Rząd X, Kolumna Y"
   (np. "R1-C1 | R1-C2") wycentrowane na granicach zakładek.
8. Mini-mapa montażu (Assembly Mini-Map) w nagłówku pokazująca siatkę R x C z wyróżnioną bieżącą stroną.
9. Kwadrat kontrolny skali 1:1 o wymiarach 5.0 x 5.0 cm (50 x 50 mm = 141.73 x 141.73 pt) z instrukcją.
10. Opcjonalne renderowanie rastrowych podglądów PNG (150 DPI) dla każdej strony.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional, Sequence
import pymupdf as fitz
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
    box,
)

from src.core.contract import (
    PT_PER_MM,
    Contour,
    PathSegment,
    PatternPiece,
    Point2D,
)
from src.core.geometry import (
    contour_to_points,
    contour_to_shapely_linestring,
    get_contour_bounds,
)
from src.nesting.a4_nester import NestingPage, NestingResult, PlacedPiece

# Wymiary arkusza A4
PAGE_W_MM: float = 210.0
PAGE_H_MM: float = 297.0
PAGE_W_PT: float = PAGE_W_MM * PT_PER_MM  # 595.27559 pt
PAGE_H_PT: float = PAGE_H_MM * PT_PER_MM  # 841.88976 pt

MARGIN_MM: float = 10.0
OVERLAP_MM: float = 15.0


def _to_fitz_point(
    x_canvas_mm: float,
    y_canvas_mm: float,
    gx0: float,
    gy0: float,
    margin_mm: float = MARGIN_MM,
) -> fitz.Point:
    """
    Konwertuje punkt z przestrzeni globalnej canvas (CAD, Y rośnie w górę)
    do współrzędnych arkusza PDF (fitz, Y rośnie w dół) z uwzględnieniem
    okna kafelka (gx0, gy0) i marginesu drukarki.
    """
    x_page_mm = margin_mm + (x_canvas_mm - gx0)
    y_page_mm = margin_mm + (y_canvas_mm - gy0)
    x_pt = x_page_mm * PT_PER_MM
    y_pt = (PAGE_H_MM - y_page_mm) * PT_PER_MM
    return fitz.Point(x_pt, y_pt)


def _draw_shapely_geom(
    shape: fitz.Shape,
    geom,
    gx0: float,
    gy0: float,
    color: tuple[float, float, float],
    dashes: Optional[str] = None,
    width: float = 1.0,
    margin_mm: float = MARGIN_MM,
) -> None:
    """Rysuje dowolną geometrię Shapely na obiekcie fitz.Shape."""
    if geom.is_empty:
        return

    geoms = geom.geoms if hasattr(geom, "geoms") else [geom]
    for g in geoms:
        if isinstance(g, (LineString,)):
            pts = list(g.coords)
            if len(pts) >= 2:
                fitz_pts = [_to_fitz_point(x, y, gx0, gy0, margin_mm) for x, y in pts]
                shape.draw_polyline(fitz_pts)
                shape.finish(color=color, dashes=dashes, width=width, closePath=False)
        elif isinstance(g, Polygon):
            pts = list(g.exterior.coords)
            if len(pts) >= 2:
                fitz_pts = [_to_fitz_point(x, y, gx0, gy0, margin_mm) for x, y in pts]
                shape.draw_polyline(fitz_pts)
                shape.finish(color=color, dashes=dashes, width=width, closePath=True)
            for interior in g.interiors:
                ipts = list(interior.coords)
                if len(ipts) >= 2:
                    fitz_pts = [_to_fitz_point(x, y, gx0, gy0, margin_mm) for x, y in ipts]
                    shape.draw_polyline(fitz_pts)
                    shape.finish(color=color, dashes=dashes, width=width, closePath=True)
        elif isinstance(g, MultiLineString):
            for line in g.geoms:
                pts = list(line.coords)
                if len(pts) >= 2:
                    fitz_pts = [_to_fitz_point(x, y, gx0, gy0, margin_mm) for x, y in pts]
                    shape.draw_polyline(fitz_pts)
                    shape.finish(color=color, dashes=dashes, width=width, closePath=False)
        elif isinstance(g, GeometryCollection):
            for sub_g in g.geoms:
                _draw_shapely_geom(shape, sub_g, gx0, gy0, color, dashes, width, margin_mm)


def _draw_alignment_diamond(
    page: fitz.Page,
    cx_pt: float,
    cy_pt: float,
    label: str,
    half_size_mm: float = 4.0,
    color: tuple[float, float, float] = (0.8, 0.15, 0.15),
    text_offset_y: float = 3.0,
) -> None:
    """Rysuje paser dopasowania w kształcie rombu z krzyżem celowniczym i etykietą."""
    hs = half_size_mm * PT_PER_MM
    shape = page.new_shape()
    # Romb
    diamond_pts = [
        fitz.Point(cx_pt, cy_pt - hs),
        fitz.Point(cx_pt + hs, cy_pt),
        fitz.Point(cx_pt, cy_pt + hs),
        fitz.Point(cx_pt - hs, cy_pt),
        fitz.Point(cx_pt, cy_pt - hs),
    ]
    shape.draw_polyline(diamond_pts)
    # Krzyż celowniczy wystający o 3 pt
    ch = hs + 4.0
    shape.draw_line(fitz.Point(cx_pt - ch, cy_pt), fitz.Point(cx_pt + ch, cy_pt))
    shape.draw_line(fitz.Point(cx_pt, cy_pt - ch), fitz.Point(cx_pt, cy_pt + ch))
    shape.finish(color=color, width=0.8)
    shape.commit()

    # Etykieta obok pasera (automatyczne dopasowanie, by nie wystawała poza arkusz)
    approx_text_w = len(label) * 4.5
    if cx_pt > PAGE_W_PT / 2.0 or (cx_pt + hs + 4.0 + approx_text_w > PAGE_W_PT - 4.0):
        # Umieszczamy po lewej stronie rombu
        text_x = cx_pt - hs - 4.0 - approx_text_w
    else:
        text_x = cx_pt + hs + 4.0

    text_x = max(4.0, min(text_x, PAGE_W_PT - approx_text_w - 4.0))

    page.insert_text(
        fitz.Point(text_x, cy_pt + text_offset_y),
        label,
        fontsize=7.5,
        color=color,
    )


def _draw_mini_map(
    page: fitz.Page,
    current_row: int,
    current_col: int,
    total_rows: int,
    total_cols: int,
    page_index: int,
    total_pages: int,
    margin_mm: float = MARGIN_MM,
) -> None:
    """Rysuje mini-mapę montażu (Assembly Mini-Map) w prawym górnym rogu strony."""
    cell_w = 6.0 * PT_PER_MM
    cell_h = 8.5 * PT_PER_MM
    map_w = total_cols * cell_w
    map_h = total_rows * cell_h

    m_pt = margin_mm * PT_PER_MM

    # Tytuł mini-mapy
    title_text = f"Arkusz {page_index}/{total_pages} (R{current_row}-C{current_col})"
    title_w = len(title_text) * 4.2
    # Prawostronne wyrównanie tytułu i mapy wewnątrz marginesu zadruku
    title_x = max(m_pt + 4.0, PAGE_W_PT - m_pt - title_w)
    start_x = max(m_pt + 4.0, PAGE_W_PT - m_pt - map_w)
    start_y = m_pt + 10.0

    page.insert_text(
        fitz.Point(title_x, start_y - 3.0),
        title_text,
        fontsize=6.5,
        color=(0.3, 0.3, 0.3),
    )

    shape = page.new_shape()
    for r in range(total_rows):
        for c in range(total_cols):
            # W CAD i szyciu row 1 to dół wykroju, row total_rows to góra.
            # W reprezentacji wizualnej góra siatki powinna odpowiadać wyższym Y (row total_rows).
            grid_row_idx = (total_rows - 1) - r
            bx0 = start_x + c * cell_w
            by0 = start_y + grid_row_idx * cell_h
            rect = fitz.Rect(bx0, by0, bx0 + cell_w, by0 + cell_h)
            shape.draw_rect(rect)

            is_active = (r + 1 == current_row) and (c + 1 == current_col)
            if is_active:
                shape.finish(color=(0.15, 0.4, 0.75), fill=None, width=1.5)
                # Krzyżyk w aktywnej komórce dla wyraźnego wyróżnienia bez kryjącego tła
                shape.draw_line(fitz.Point(bx0, by0), fitz.Point(bx0 + cell_w, by0 + cell_h))
                shape.draw_line(fitz.Point(bx0, by0 + cell_h), fitz.Point(bx0 + cell_w, by0))
                shape.finish(color=(0.15, 0.4, 0.75), width=0.8)
            else:
                shape.finish(color=(0.6, 0.6, 0.6), fill=None, width=0.5)

    shape.commit()


def _draw_calibration_square(page: fitz.Page, margin_mm: float = MARGIN_MM) -> None:
    """Rysuje precyzyjny kwadrat kontrolny skali 1:1 o wymiarach 5.0 x 5.0 cm (50 x 50 mm)."""
    size_mm = 50.0
    size_pt = size_mm * PT_PER_MM  # 141.7323 pt

    # Umieszczamy kwadrat w lewym dolnym narożniku arkusza (wewnątrz marginesu)
    x0 = (margin_mm + 4.0) * PT_PER_MM
    y0 = PAGE_H_PT - (margin_mm + size_mm + 4.0) * PT_PER_MM

    rect = fitz.Rect(x0, y0, x0 + size_pt, y0 + size_pt)
    shape = page.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=(0.1, 0.1, 0.1), fill=None, width=1.2)

    # Wewnętrzny krzyż centrujący
    cx = x0 + size_pt / 2.0
    cy = y0 + size_pt / 2.0
    shape.draw_line(fitz.Point(cx - 10, cy), fitz.Point(cx + 10, cy))
    shape.draw_line(fitz.Point(cx, cy - 10), fitz.Point(cx, cy + 10))
    shape.finish(color=(0.7, 0.7, 0.7), dashes="[2 2] 0", width=0.6)
    shape.commit()

    # Teksty instruktażowe wewnątrz kwadratu
    page.insert_text(
        fitz.Point(x0 + 12.0, y0 + 26.0),
        "TEST SKALI 1:1",
        fontsize=9.0,
        color=(0.1, 0.1, 0.1),
    )
    page.insert_text(
        fitz.Point(x0 + 12.0, y0 + 44.0),
        "5.0 x 5.0 cm (50 x 50 mm)",
        fontsize=8.5,
        color=(0.1, 0.1, 0.1),
    )
    page.insert_text(
        fitz.Point(x0 + 12.0, y0 + 96.0),
        "ZMIERZ LINIJKĄ PO WYDRUKU",
        fontsize=7.0,
        color=(0.3, 0.3, 0.3),
    )
    page.insert_text(
        fitz.Point(x0 + 12.0, y0 + 110.0),
        "MEASURE WITH RULER (1:1 SCALE)",
        fontsize=6.5,
        color=(0.4, 0.4, 0.4),
    )


def _draw_printable_border(page: fitz.Page, margin_mm: float = MARGIN_MM) -> None:
    """Rysuje ramkę marginesu zadruku drukarki."""
    m_pt = margin_mm * PT_PER_MM
    border_shape = page.new_shape()
    border_rect = fitz.Rect(m_pt, m_pt, PAGE_W_PT - m_pt, PAGE_H_PT - m_pt)
    border_shape.draw_rect(border_rect)
    border_shape.finish(color=(0.75, 0.75, 0.75), dashes="[3 3] 0", width=0.6)
    border_shape.commit()


def _draw_glue_overlap_lines(
    page: fitz.Page,
    page_model: NestingPage,
    margin_mm: float = MARGIN_MM,
    overlap_mm: float = OVERLAP_MM,
) -> None:
    """Rysuje paski klejenia i linie prowadzące dla kafelkowania."""
    if not page_model.is_tiled:
        return

    m_pt = margin_mm * PT_PER_MM
    p_w_pt = PAGE_W_PT
    p_h_pt = PAGE_H_PT

    # Prawy margines klejenia (gdy po prawej stronie jest kolejna kolumna)
    if page_model.col < page_model.total_cols:
        cut_x_mm = PAGE_W_MM - margin_mm - overlap_mm
        cut_x_pt = cut_x_mm * PT_PER_MM
        glue_strip_rect = fitz.Rect(cut_x_pt, m_pt, p_w_pt - m_pt, p_h_pt - m_pt)

        g_shape = page.new_shape()
        g_shape.draw_rect(glue_strip_rect)
        g_shape.finish(color=(0.88, 0.88, 0.88), fill=(0.96, 0.96, 0.96), width=0.5)
        # Linia cięcia/klejenia
        g_shape.draw_line(fitz.Point(cut_x_pt, m_pt), fitz.Point(cut_x_pt, p_h_pt - m_pt))
        g_shape.finish(color=(0.45, 0.45, 0.45), dashes="[4 3] 0", width=0.8)
        g_shape.commit()

        page.insert_text(
            fitz.Point(cut_x_pt + 8.0, p_h_pt / 2.0 + 60.0),
            f"LINIA SKLEJENIA / GLUE LINE ({overlap_mm:.0f} mm) ->",
            fontsize=7.5,
            rotate=90,
            color=(0.45, 0.45, 0.45),
        )

    # Górny margines klejenia (gdy w CAD wyżej jest kolejny rząd; w fitz to góra strony)
    if page_model.row < page_model.total_rows:
        cut_y_mm = margin_mm + overlap_mm
        cut_y_pt = cut_y_mm * PT_PER_MM
        top_strip_rect = fitz.Rect(m_pt, m_pt, p_w_pt - m_pt, cut_y_pt)

        t_shape = page.new_shape()
        t_shape.draw_rect(top_strip_rect)
        t_shape.finish(color=(0.88, 0.88, 0.88), fill=(0.96, 0.96, 0.96), width=0.5)
        t_shape.draw_line(fitz.Point(m_pt, cut_y_pt), fitz.Point(p_w_pt - m_pt, cut_y_pt))
        t_shape.finish(color=(0.45, 0.45, 0.45), dashes="[4 3] 0", width=0.8)
        t_shape.commit()

        page.insert_text(
            fitz.Point(p_w_pt / 2.0 - 70.0, cut_y_pt - 5.0),
            f"LINIA SKLEJENIA / GLUE LINE ({overlap_mm:.0f} mm) ^",
            fontsize=7.5,
            color=(0.45, 0.45, 0.45),
        )


def _draw_alignment_diamonds(
    page: fitz.Page,
    page_model: NestingPage,
    margin_mm: float = MARGIN_MM,
    overlap_mm: float = OVERLAP_MM,
) -> None:
    """Rysuje pasery dopasowania na granicach arkuszy kafelkowanych."""
    if not page_model.is_tiled:
        return

    p_w_pt = PAGE_W_PT
    p_h_pt = PAGE_H_PT

    # Paser na prawej krawędzi (środek paska overlap)
    if page_model.col < page_model.total_cols:
        cut_x_mm = PAGE_W_MM - margin_mm - overlap_mm
        mid_strip_x = (cut_x_mm + overlap_mm / 2.0) * PT_PER_MM
        _draw_alignment_diamond(
            page,
            mid_strip_x,
            p_h_pt / 2.0,
            label=f"R{page_model.row}-C{page_model.col} | R{page_model.row}-C{page_model.col+1}",
        )

    # Lewy margines pasowania (gdy po lewej stronie jest poprzednia kolumna)
    if page_model.col > 1:
        mid_left_x = (margin_mm + overlap_mm / 2.0) * PT_PER_MM
        _draw_alignment_diamond(
            page,
            mid_left_x,
            p_h_pt / 2.0,
            label=f"R{page_model.row}-C{page_model.col-1} | R{page_model.row}-C{page_model.col}",
        )

    # Górny margines pasowania (gdy w CAD wyżej jest kolejny rząd; w fitz to góra strony)
    if page_model.row < page_model.total_rows:
        mid_strip_y = (margin_mm + overlap_mm / 2.0) * PT_PER_MM
        _draw_alignment_diamond(
            page,
            p_w_pt / 2.0,
            mid_strip_y,
            label=f"R{page_model.row}-C{page_model.col} | R{page_model.row+1}-C{page_model.col}",
            text_offset_y=-6.0,
        )

    # Dolny margines pasowania (gdy niżej był poprzedni rząd)
    if page_model.row > 1:
        mid_bot_y = (PAGE_H_MM - margin_mm - overlap_mm / 2.0) * PT_PER_MM
        _draw_alignment_diamond(
            page,
            p_w_pt / 2.0,
            mid_bot_y,
            label=f"R{page_model.row-1}-C{page_model.col} | R{page_model.row}-C{page_model.col}",
            text_offset_y=10.0,
        )

    # Narożne pasery celownicze (gdy stykają się arkusze w siatce)
    if page_model.col < page_model.total_cols and page_model.row < page_model.total_rows:
        corner_x = (PAGE_W_MM - margin_mm - overlap_mm / 2.0) * PT_PER_MM
        corner_y = (margin_mm + overlap_mm / 2.0) * PT_PER_MM
        _draw_alignment_diamond(
            page,
            corner_x,
            corner_y,
            label=f"R{page_model.row}C{page_model.col}",
            half_size_mm=3.0,
            color=(0.2, 0.2, 0.2),
        )


def export_a4_pdf(
    nesting_result: NestingResult,
    output_path: str | Path,
    render_png_previews: bool = True,
    png_output_dir: Optional[str | Path] = None,
    margin_mm: Optional[float] = None,
    overlap_mm: Optional[float] = None,
) -> Path:
    """
    Generuje wielostronicowy dokument wektorowy A4 PDF oraz opcjonalne podglądy PNG.

    Parametry:
    - nesting_result: wynik nestingu z trybu 'hybrid' lub 'poster'
    - output_path: ścieżka do docelowego pliku .pdf
    - render_png_previews: czy zapisać każdą stronę jako podgląd PNG w 150 DPI
    - png_output_dir: opcjonalny katalog na podglądy PNG (domyślnie katalog pliku PDF)
    - margin_mm: opcjonalne nadpisanie marginesu drukarki (domyślnie z nesting_result lub 10.0 mm)
    - overlap_mm: opcjonalne nadpisanie zakładki klejenia (domyślnie z nesting_result lub 15.0 mm)
    """
    out_pdf = Path(output_path).resolve()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    if png_output_dir:
        png_dir = Path(png_output_dir).resolve()
        png_dir.mkdir(parents=True, exist_ok=True)
    else:
        png_dir = out_pdf.parent

    margin = margin_mm if margin_mm is not None else getattr(nesting_result, "margin_mm", MARGIN_MM)
    overlap = overlap_mm if overlap_mm is not None else getattr(nesting_result, "overlap_mm", OVERLAP_MM)

    with fitz.open() as doc:

        m_pt = margin * PT_PER_MM
        p_w_pt = PAGE_W_PT
        p_h_pt = PAGE_H_PT

        for page_model in nesting_result.pages:
            page = doc.new_page(width=p_w_pt, height=p_h_pt)
            gx0, gy0, gx1, gy1 = page_model.tile_box_mm
            tile_rect = box(gx0, gy0, gx1, gy1)

            # 1. Ramka marginesu zadruku drukarki
            _draw_printable_border(page, margin_mm=margin)

            # 2. Paski klejenia i linie prowadzące dla kafelkowania
            _draw_glue_overlap_lines(page, page_model, margin_mm=margin, overlap_mm=overlap)

            # 2b. Pasery dopasowania na granicach arkuszy
            _draw_alignment_diamonds(page, page_model, margin_mm=margin, overlap_mm=overlap)

            # 3. Rysowanie elementów wykroju (Dual Contours + Internals + Grainline + Seams)
            cad_shape = page.new_shape()

            for pp in page_model.placed_pieces:
                trans_p = pp.get_placed_piece()

                # Kontur cięcia (Cut Line)
                if trans_p.has_cut_contour:
                    cut_ring = contour_to_shapely_linestring(trans_p.cut_contour, steps_per_curve=48)
                    clipped_cut = cut_ring.intersection(tile_rect)
                    _draw_shapely_geom(
                        cad_shape,
                        clipped_cut,
                        gx0,
                        gy0,
                        color=(0.08, 0.08, 0.08),
                        dashes=None,
                        width=1.25,
                        margin_mm=margin,
                    )

                # Kontur szycia (Sewing Line)
                sew_ring = contour_to_shapely_linestring(trans_p.outer_contour, steps_per_curve=48)
                clipped_sew = sew_ring.intersection(tile_rect)
                if trans_p.has_cut_contour:
                    # Przerywana niebieska linia szycia wewnątrz
                    _draw_shapely_geom(
                        cad_shape,
                        clipped_sew,
                        gx0,
                        gy0,
                        color=(0.17, 0.36, 0.52),
                        dashes="[4 2] 0",
                        width=0.9,
                        margin_mm=margin,
                    )
                else:
                    # Brak naddatków -> linia zewnętrzna to stała linia cięcia
                    _draw_shapely_geom(
                        cad_shape,
                        clipped_sew,
                        gx0,
                        gy0,
                        color=(0.08, 0.08, 0.08),
                        dashes=None,
                        width=1.25,
                        margin_mm=margin,
                    )

                # Linie wewnętrzne (Internal lines / darts)
                for il in trans_p.internal_lines:
                    il_ring = contour_to_shapely_linestring(il.contour, steps_per_curve=32)
                    clipped_il = il_ring.intersection(tile_rect)
                    _draw_shapely_geom(
                        cad_shape,
                        clipped_il,
                        gx0,
                        gy0,
                        color=(0.85, 0.2, 0.2),
                        dashes="[4 2] 0",
                        width=0.85,
                        margin_mm=margin,
                    )

                # Nitka prosta (Grainline)
                if trans_p.grainline_start and trans_p.grainline_end:
                    g_line = LineString([trans_p.grainline_start.to_tuple(), trans_p.grainline_end.to_tuple()])
                    clipped_g = g_line.intersection(tile_rect)
                    if not clipped_g.is_empty:
                        _draw_shapely_geom(
                            cad_shape,
                            clipped_g,
                            gx0,
                            gy0,
                            color=(0.15, 0.65, 0.3),
                            dashes=None,
                            width=1.0,
                            margin_mm=margin,
                        )
                        # Strzałka na końcu nitki prostej, jeśli punkt końcowy mieści się w kaflu
                        if (
                            gx0 <= trans_p.grainline_end.x <= gx1
                            and gy0 <= trans_p.grainline_end.y <= gy1
                        ):
                            tip_pt = _to_fitz_point(trans_p.grainline_end.x, trans_p.grainline_end.y, gx0, gy0, margin_mm=margin)
                            # Strzałka w osi Y
                            cad_shape.draw_line(tip_pt, fitz.Point(tip_pt.x - 4.0, tip_pt.y + 8.0))
                            cad_shape.draw_line(tip_pt, fitz.Point(tip_pt.x + 4.0, tip_pt.y + 8.0))
                            cad_shape.finish(color=(0.15, 0.65, 0.3), width=1.0)

                # Karbiki montażowe (Notches) na obrysie
                for notch in trans_p.notches:
                    n_pos = notch.position
                    L = notch.length_mm
                    if notch.direction:
                        n_end = Point2D(x=n_pos.x + notch.direction.x * L, y=n_pos.y + notch.direction.y * L)
                    else:
                        n_end = Point2D(x=n_pos.x, y=n_pos.y + L)
                    n_line = LineString([n_pos.to_tuple(), n_end.to_tuple()])
                    clipped_n = n_line.intersection(tile_rect)
                    if not clipped_n.is_empty:
                        _draw_shapely_geom(
                            cad_shape,
                            clipped_n,
                            gx0,
                            gy0,
                            color=(0.1, 0.45, 0.8),
                            dashes=None,
                            width=1.4,
                            margin_mm=margin,
                        )

            cad_shape.commit()

            # 4. Adnotacje naddatków na szwy przy krawędziach
            for pp in page_model.placed_pieces:
                trans_p = pp.get_placed_piece()
                if trans_p.has_cut_contour:
                    for seg in trans_p.outer_contour.segments:
                        mx = (seg.start.x + seg.end.x) / 2.0
                        my = (seg.start.y + seg.end.y) / 2.0
                        if gx0 + 5.0 <= mx <= gx1 - 5.0 and gy0 + 5.0 <= my <= gy1 - 5.0:
                            allowance = (
                                seg.seam_allowance_mm
                                if seg.seam_allowance_mm is not None
                                else trans_p.seam_allowance_mm
                            )
                            role = seg.edge_role
                            label_txt = None
                            if role == "fold" or allowance == 0.0:
                                label_txt = "0 mm (ZGIĘCIE/FOLD)"
                            elif role == "elastic":
                                label_txt = f"+{allowance:.0f} mm (ELASTIZAR)"
                            elif role == "hem":
                                label_txt = f"+{allowance:.0f} mm (HEM)"
                            elif allowance > 0.0:
                                label_txt = f"+{allowance:.0f} mm"

                            if label_txt:
                                fpt = _to_fitz_point(mx, my, gx0, gy0, margin_mm=margin)
                                lbl_w = len(label_txt) * 4.2
                                lbl_x = max(m_pt + 4.0, min(fpt.x - 20.0, PAGE_W_PT - m_pt - lbl_w - 4.0))
                                page.insert_text(
                                    fitz.Point(lbl_x, fpt.y - 3.0),
                                    label_txt,
                                    fontsize=6.5,
                                    color=(0.2, 0.35, 0.5),
                                )

                # Etykieta nazwy elementu w środku
                min_px, min_py, max_px, max_py = get_contour_bounds(trans_p.effective_boundary, steps_per_curve=32)
                c_mid_x = (min_px + max_px) / 2.0
                c_mid_y = (min_py + max_py) / 2.0
                if gx0 <= c_mid_x <= gx1 and gy0 <= c_mid_y <= gy1:
                    label_pt = _to_fitz_point(c_mid_x, c_mid_y, gx0, gy0, margin_mm=margin)
                    name_w = len(trans_p.name) * 5.2
                    name_x = label_pt.x - name_w / 2.0
                    name_x = max(m_pt + 4.0, min(name_x, PAGE_W_PT - m_pt - name_w - 4.0))
                    page.insert_text(
                        fitz.Point(name_x, label_pt.y - 5.0),
                        f"{trans_p.name}",
                        fontsize=9.0,
                        color=(0.1, 0.1, 0.1),
                    )
                    if trans_p.fabric_note:
                        fn_w = len(trans_p.fabric_note) * 4.0
                        fn_x = label_pt.x - fn_w / 2.0
                        fn_x = max(m_pt + 4.0, min(fn_x, PAGE_W_PT - m_pt - fn_w - 4.0))
                        page.insert_text(
                            fitz.Point(fn_x, label_pt.y + 6.0),
                            f"{trans_p.fabric_note}",
                            fontsize=7.0,
                            color=(0.4, 0.4, 0.4),
                        )

            # 5. Mini-mapa montażu w prawym górnym rogu
            _draw_mini_map(
                page,
                current_row=page_model.row,
                current_col=page_model.col,
                total_rows=page_model.total_rows,
                total_cols=page_model.total_cols,
                page_index=page_model.page_index,
                total_pages=nesting_result.total_pages,
                margin_mm=margin,
            )

            # 6. Kwadrat kontrolny skali 1:1 (50x50 mm) na Stronie 1
            if page_model.page_index == 1:
                _draw_calibration_square(page, margin_mm=margin)

            # 7. Nagłówek i stopka arkusza
            mode_str = "Tryb Hybrydowy (Smart Pack & Tile)" if nesting_result.mode == "hybrid" else "Tryb Plakatowy (Grid)"
            page.insert_text(
                fitz.Point(m_pt, 7.0 * PT_PER_MM),
                f"Image-to-Pattern CAD | {page_model.piece_name} | {mode_str} | Skala 1:1",
                fontsize=8.0,
                color=(0.2, 0.2, 0.2),
            )

            page.insert_text(
                fitz.Point(m_pt, p_h_pt - 4.0 * PT_PER_MM),
                f"Arkusz A4 (210 x 297 mm) | Margines drukarki: {margin:.0f} mm | Zakladka klejenia: {overlap:.0f} mm | Strona {page_model.page_index} z {nesting_result.total_pages}",
                fontsize=6.5,
                color=(0.5, 0.5, 0.5),
            )

        doc.save(str(out_pdf))

    # 8. Renderowanie podglądów PNG (150 DPI)
    if render_png_previews:
        with fitz.open(str(out_pdf)) as doc_render:
            for idx, page in enumerate(doc_render):
                pix = page.get_pixmap(dpi=150)
                prefix = "hybrid" if nesting_result.mode == "hybrid" else "poster"
                png_path = png_dir / f"{prefix}_page_{idx+1:02d}.png"
                pix.save(str(png_path))

    return out_pdf
