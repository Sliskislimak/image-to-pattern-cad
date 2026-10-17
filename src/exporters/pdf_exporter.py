"""
Klocek 1B: Eksporter Wektorowego PDF w skali 1:1 (oparty o bibliotekę PyMuPDF / pymupdf).

Udostępnia dwa tryby eksportu PDF 1:1:
1. `export_clo3d_vector_pdf_1to1`:
   Czysty plik wektorowy PDF w skali 1:1 przeznaczony bezpośrednio do importu w Clo3D
   (`File -> Import -> Adobe PDF`). Zawiera wyłącznie zamknięte krzywe Béziera / odcinki
   obrysów części oraz linie wewnętrzne (bez ramki strony, aby Clo3D nie traktowało
   strony jako wielkiego prostokąta materiału).
2. `export_technical_sheet_pdf_1to1`:
   Bogaty arkusz techniczny PDF 1:1 z wypełnieniem kolorystycznym (styl oryginalnej grafiki),
   etykietami wymiarów w cm/mm, mnożnikami (x1, x2), oznaczonymi węzłami krzywych Béziera,
   liniami nitki prostej oraz wzorcem skali 5 x 5 cm.
"""

from __future__ import annotations

from pathlib import Path
import pymupdf as fitz

from src.core.contract import PT_PER_MM, Contour, PatternProject, Point2D
from src.core.geometry import contour_to_shapely_polygon, get_contour_bounds
from src.exporters.dxf_exporter import compute_grid_layout, expand_project_pieces


def _draw_contour_on_shape(
    shape: fitz.Shape,
    contour: Contour,
    off_x_mm: float,
    off_y_mm: float,
    canvas_height_mm: float,
    margin_x_mm: float,
    margin_top_mm: float,
) -> None:
    """Rysuje wszystkie segmenty konturu (linie oraz krzywe Béziera 3. stopnia) na obiekcie fitz.Shape."""

    def to_pt(pt: Point2D) -> fitz.Point:
        x_mm = margin_x_mm + off_x_mm + pt.x
        y_mm = margin_top_mm + (canvas_height_mm - (off_y_mm + pt.y))
        return fitz.Point(x_mm * PT_PER_MM, y_mm * PT_PER_MM)

    for seg in contour.segments:
        p0 = to_pt(seg.start)
        p3 = to_pt(seg.end)
        if seg.type == "cubic_bezier" and seg.cp1 is not None and seg.cp2 is not None:
            p1 = to_pt(seg.cp1)
            p2 = to_pt(seg.cp2)
            shape.draw_bezier(p0, p1, p2, p3)
        else:
            shape.draw_line(p0, p3)


def _compute_canvas_extents(
    placed_pieces: list[tuple],
) -> tuple[float, float]:
    """Oblicza całkowitą szerokość i wysokość obszaru roboczego w mm."""
    max_x_mm = 0.0
    max_y_mm = 0.0
    for piece, off_x, off_y in placed_pieces:
        _, _, px_max, py_max = get_contour_bounds(piece.outer_contour)
        max_x_mm = max(max_x_mm, off_x + px_max)
        max_y_mm = max(max_y_mm, off_y + py_max)
    return (max(max_x_mm, 100.0), max(max_y_mm, 100.0))


def export_clo3d_vector_pdf_1to1(
    project: PatternProject,
    output_path: str | Path,
    expand_quantities: bool = False,
    include_internal_lines: bool = True,
) -> Path:
    """
    Generuje czysty wektorowy PDF w skali 1:1 zoptymalizowany pod:
    `Clo3D -> File -> Import -> Adobe PDF`.

    Każdy element to pojedyncza zamknięta ścieżka wektorowa (Cubic Bézier + Line)
    w skali 1:1 (1 mm = 2.83464567 pt).
    """
    pieces = expand_project_pieces(project, expand_quantities=expand_quantities)
    placed = compute_grid_layout(pieces, spacing_mm=40.0)
    canvas_w_mm, canvas_h_mm = _compute_canvas_extents(placed)
    margin_mm = 30.0

    page_w_pt = (canvas_w_mm + 2.0 * margin_mm) * PT_PER_MM
    page_h_pt = (canvas_h_mm + 2.0 * margin_mm) * PT_PER_MM

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open() as doc:
        page = doc.new_page(width=page_w_pt, height=page_h_pt)

        for piece, off_x, off_y in placed:
            outer_shape = page.new_shape()
            _draw_contour_on_shape(
                outer_shape,
                piece.outer_contour,
                off_x,
                off_y,
                canvas_h_mm,
                margin_mm,
                margin_mm,
            )
            outer_shape.finish(
                color=(0.0, 0.0, 0.0),
                fill=None,
                width=1.0,
                closePath=True,
            )
            outer_shape.commit()

            if include_internal_lines:
                for internal in piece.internal_lines:
                    int_shape = page.new_shape()
                    _draw_contour_on_shape(
                        int_shape,
                        internal.contour,
                        off_x,
                        off_y,
                        canvas_h_mm,
                        margin_mm,
                        margin_mm,
                    )
                    int_shape.finish(
                        color=(0.8, 0.1, 0.1),
                        fill=None,
                        width=0.75,
                        closePath=internal.contour.is_closed,
                    )
                    int_shape.commit()

        doc.save(str(out))
    return out


def export_technical_sheet_pdf_1to1(
    project: PatternProject,
    output_path: str | Path,
    expand_quantities: bool = False,
) -> Path:
    """
    Generuje bogaty arkusz techniczny PDF 1:1 do podglądu i weryfikacji wymiarów:
    - Wypełnienie w kolorze beżowym zbliżonym do oryginalnego wykroju czapki
    - Dokładne wymiary każdego elementu (cm oraz mm) i mnożniki (x1, x2)
    - Zaznaczone punkty węzłowe (czerwone kropki = punkty narożne segmentów)
    - Linie wewnętrzne (przerywane czerwone) oraz nitki proste (zielone)
    - Wzorzec kalibracyjny 5 x 5 cm (50 x 50 mm).
    """
    pieces = expand_project_pieces(project, expand_quantities=expand_quantities)
    placed = compute_grid_layout(pieces, spacing_mm=55.0)
    canvas_w_mm, canvas_h_mm = _compute_canvas_extents(placed)
    margin_x_mm = 45.0
    margin_top_mm = 80.0
    margin_bottom_mm = 45.0

    page_w_pt = (canvas_w_mm + 2.0 * margin_x_mm) * PT_PER_MM
    page_h_pt = (canvas_h_mm + margin_top_mm + margin_bottom_mm) * PT_PER_MM

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open() as doc:
        page = doc.new_page(width=page_w_pt, height=page_h_pt)

        # Tytuł arkusza technicznego
        page.insert_text(
            fitz.Point(margin_x_mm * PT_PER_MM, 28.0 * PT_PER_MM),
            f"ARKUSZ TECHNICZNY CAD 1:1 - {project.project_name.upper()}",
            fontsize=18,
            color=(0.15, 0.15, 0.15),
        )
        page.insert_text(
            fitz.Point(margin_x_mm * PT_PER_MM, 38.0 * PT_PER_MM),
            "Skala 1:1 (100%) | Jednostki bazowe: mm / cm | Bez zapasow na szwy (Sin margenes de costura)",
            fontsize=11,
            color=(0.35, 0.35, 0.35),
        )

        # Kwadrat kontrolny skali 5 x 5 cm (50 x 50 mm) w prawym górnym rogu
        cal_x_pt = page_w_pt - (margin_x_mm + 50.0) * PT_PER_MM
        cal_y_pt = 15.0 * PT_PER_MM
        cal_rect = fitz.Rect(
            cal_x_pt,
            cal_y_pt,
            cal_x_pt + 50.0 * PT_PER_MM,
            cal_y_pt + 50.0 * PT_PER_MM,
        )
        cal_shape = page.new_shape()
        cal_shape.draw_rect(cal_rect)
        cal_shape.finish(color=(0.1, 0.1, 0.1), fill=(0.94, 0.94, 0.94), width=1.2)
        cal_shape.commit()
        page.insert_text(
            fitz.Point(cal_x_pt + 6.0 * PT_PER_MM, cal_y_pt + 23.0 * PT_PER_MM),
            "TEST SKALI 1:1",
            fontsize=9,
            color=(0.1, 0.1, 0.1),
        )
        page.insert_text(
            fitz.Point(cal_x_pt + 8.0 * PT_PER_MM, cal_y_pt + 30.0 * PT_PER_MM),
            "5.0 x 5.0 cm",
            fontsize=9,
            color=(0.1, 0.1, 0.1),
        )

        for piece, off_x, off_y in placed:
            # 1. Ramka pomocnicza Bounding Boxa (jasnoszara przerywana)
            bx0 = (margin_x_mm + off_x) * PT_PER_MM
            by0 = (margin_top_mm + (canvas_h_mm - (off_y + piece.target_height_mm))) * PT_PER_MM
            bx1 = bx0 + piece.target_width_mm * PT_PER_MM
            by1 = by0 + piece.target_height_mm * PT_PER_MM
            bbox_shape = page.new_shape()
            bbox_shape.draw_rect(fitz.Rect(bx0, by0, bx1, by1))
            bbox_shape.finish(color=(0.75, 0.75, 0.75), dashes="[3 3] 0", width=0.6)
            bbox_shape.commit()

            # 2. Wypełniony kontur zewnętrzny elementu (beżowy jak na rysunku)
            outer_shape = page.new_shape()
            _draw_contour_on_shape(
                outer_shape,
                piece.outer_contour,
                off_x,
                off_y,
                canvas_h_mm,
                margin_x_mm,
                margin_top_mm,
            )
            outer_shape.finish(
                color=(0.42, 0.28, 0.12),
                fill=(0.89, 0.78, 0.56),
                width=1.5,
                closePath=True,
            )
            outer_shape.commit()

            # 3. Linie wewnętrzne (czerwone)
            for internal in piece.internal_lines:
                int_shape = page.new_shape()
                _draw_contour_on_shape(
                    int_shape,
                    internal.contour,
                    off_x,
                    off_y,
                    canvas_h_mm,
                    margin_x_mm,
                    margin_top_mm,
                )
                int_shape.finish(
                    color=(0.85, 0.12, 0.12),
                    dashes="[4 2] 0",
                    width=1.1,
                    closePath=internal.contour.is_closed,
                )
                int_shape.commit()

            # 4. Linia nitki prostej (Grainline - zielona)
            if piece.grainline_start and piece.grainline_end:
                gx0 = (margin_x_mm + off_x + piece.grainline_start.x) * PT_PER_MM
                gy0 = (margin_top_mm + (canvas_h_mm - (off_y + piece.grainline_start.y))) * PT_PER_MM
                gx1 = (margin_x_mm + off_x + piece.grainline_end.x) * PT_PER_MM
                gy1 = (margin_top_mm + (canvas_h_mm - (off_y + piece.grainline_end.y))) * PT_PER_MM
                g_shape = page.new_shape()
                g_shape.draw_line(fitz.Point(gx0, gy0), fitz.Point(gx1, gy1))
                g_shape.finish(color=(0.15, 0.55, 0.25), width=1.0)
                g_shape.commit()

            # 5. Węzły segmentów (punkty narożne - małe kropki CAD)
            node_shape = page.new_shape()
            for seg in piece.outer_contour.segments:
                nx = (margin_x_mm + off_x + seg.start.x) * PT_PER_MM
                ny = (margin_top_mm + (canvas_h_mm - (off_y + seg.start.y))) * PT_PER_MM
                node_shape.draw_circle(fitz.Point(nx, ny), 2.2)
            node_shape.finish(color=(0.75, 0.1, 0.1), fill=(1.0, 1.0, 1.0), width=0.8)
            node_shape.commit()

            # 6. Opis elementu wewnątrz wielokąta (przy użyciu punktu reprezentatywnego Shapely)
            poly_geom = contour_to_shapely_polygon(piece.outer_contour)
            rep_pt = poly_geom.representative_point()
            label_x_mm = margin_x_mm + off_x + max(8.0, rep_pt.x - 32.0)
            label_y_mm = margin_top_mm + (canvas_h_mm - (off_y + rep_pt.y))

            page.insert_text(
                fitz.Point(label_x_mm * PT_PER_MM, label_y_mm * PT_PER_MM),
                f"{piece.name} (x{piece.quantity})",
                fontsize=11.5,
                color=(0.1, 0.1, 0.1),
            )
            page.insert_text(
                fitz.Point(label_x_mm * PT_PER_MM, label_y_mm * PT_PER_MM + 13.0),
                f"{piece.target_width_cm:g} x {piece.target_height_cm:g} cm ({piece.target_width_mm:.0f} x {piece.target_height_mm:.0f} mm)",
                fontsize=9.0,
                color=(0.25, 0.2, 0.15),
            )

        doc.save(str(out))
    return out
