"""
Klocek 1A: Eksporter DXF-AAMA / ASTM dla programu Clo3D (oparty o bibliotekę ezdxf).

Zgodność ze standardem AAMA/ASTM (używanym przez Clo3D, Seamly2D VDXFEngine, Gerber, Lectra):
- Wersja pliku: AutoCAD R12 (AC1009) – wymagana przez parsery AAMA dla klasycznych encji POLYLINE 2D.
- Każdy element wykroju jest definiowany jako osobny BLOCK i wstawiany na MODELSPACE (INSERT).
- Warstwy standardu AAMA:
  * Warstwa '1' (BOUNDARY): Zamknięty kontur zewnętrzny elementu (POLYLINE 2D).
  * Warstwa '4' (INTERNAL): Linie wewnętrzne, zaszywki, linie podziału (otwarta POLYLINE / LINE).
  * Warstwa '7' (GRAINLINE): Linia nitki prostej (LINE).
  * Warstwa '8' (TEXT / ANNOTATION): Metadane tekstowe elementu (TEXT).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal
import ezdxf

from src.core.contract import Contour, PathSegment, PatternPiece, PatternProject, Point2D
from src.core.geometry import (
    contour_to_points,
    contour_to_shapely_polygon,
    expand_project_pieces as geom_expand,
    get_contour_bounds,
    mirror_piece_x,
    transform_contour,
)


AAMA_LAYER_BOUNDARY = "1"
AAMA_LAYER_NOTCH = "2"
AAMA_LAYER_INTERNAL = "4"
AAMA_LAYER_FOLD = "5"
AAMA_LAYER_GRAINLINE = "7"
AAMA_LAYER_TEXT = "8"


def _mirror_piece_x(piece: PatternPiece, suffix: str = "_R") -> PatternPiece:
    """Tworzy odbicie lustrzane elementu względem osi X (dla par symetrycznych x2 w Clo3D)."""
    return mirror_piece_x(piece, suffix=suffix)


def expand_project_pieces(project: PatternProject, expand_quantities: bool = False) -> list[PatternPiece]:
    """
    Zwraca listę elementów do rozmieszczenia:
    - gdy expand_quantities=False: zwraca oryginalne elementy wzorcowe (6 sztuk).
    - gdy expand_quantities=True: rozwija elementy x2 na pary (np. lewy i prawy nausznik / bok),
      dzięki czemu w Clo3D od razu mamy komplet części do zszycia całej czapki (9 sztuk).
    """
    if not expand_quantities:
        return list(project.pieces)
    return geom_expand(project, mirror_symmetric=True)


def compute_grid_layout(
    pieces: list[PatternPiece],
    spacing_mm: float = 40.0,
    max_row_width_mm: float = 850.0,
) -> list[tuple[PatternPiece, float, float]]:
    """
    Rozmieszcza elementy w przejrzystej siatce 2D (bez nakładania się),
    zwracając listę krotek: (piece, offset_x_mm, offset_y_mm).
    Oblicza bounding box na podstawie piece.effective_boundary,
    dzięki czemu elementy z naddatkami na szwy nie nachodzą na siebie.
    """
    layout: list[tuple[PatternPiece, float, float]] = []
    cursor_x = 0.0
    cursor_y = 0.0
    row_max_h = 0.0

    for piece in pieces:
        min_x, min_y, max_x, max_y = get_contour_bounds(piece.effective_boundary)
        w = max_x - min_x
        h = max_y - min_y

        if cursor_x > 0.0 and (cursor_x + w) > max_row_width_mm:
            cursor_x = 0.0
            cursor_y += row_max_h + spacing_mm
            row_max_h = 0.0

        off_x = cursor_x - min_x
        off_y = cursor_y - min_y
        layout.append((piece, off_x, off_y))
        cursor_x += w + spacing_mm
        row_max_h = max(row_max_h, h)

    return layout


def export_clo3d_dxf(
    project: PatternProject,
    output_path: str | Path,
    unit: Literal["mm", "cm"] = "mm",
    expand_quantities: bool = False,
    steps_per_curve: int = 36,
) -> Path:
    """
    Generuje plik DXF-AAMA (R12) gotowy do zaimportowania w Clo3D:
    `File -> Import -> DXF (AAMA/ASTM)` (oraz kompatybilny ze zwykłym `Import -> DXF`).

    Parametry:
    - unit: 'mm' (domyślna jednostka w oknie importu Clo3D) lub 'cm'
    - expand_quantities: czy rozwinąć mnożniki x2 na osobne części (np. lewa/prawa)
    """
    scale = 0.1 if unit == "cm" else 1.0

    # Tworzymy dokument DXF w standardzie R12 (AC1009) dla maksymalnej kompatybilności z AAMA/ASTM
    doc = ezdxf.new(dxfversion="R12")
    msp = doc.modelspace()

    # Rejestracja warstw AAMA (AutoCAD R12)
    layers_spec = [
        (AAMA_LAYER_BOUNDARY, 7),   # 1 = biały/czarny (obrys zewnętrzny cięcia / Boundary)
        (AAMA_LAYER_NOTCH, 4),      # 2 = cyan (karbiki montażowe / Notches)
        (AAMA_LAYER_INTERNAL, 1),   # 4 = czerwony (linie wewnętrzne / zaszywki / linia szycia)
        (AAMA_LAYER_FOLD, 6),       # 5 = magenta (linie zgięcia / Fold / Mirror axis)
        (AAMA_LAYER_GRAINLINE, 3),  # 7 = zielony (nitka prosta / Grainline)
        (AAMA_LAYER_TEXT, 5),       # 8 = niebieski (opisy tekstowe / Annotations)
    ]
    for layer_name, color in layers_spec:
        if layer_name not in doc.layers:
            doc.layers.add(name=layer_name, color=color)

    pieces_to_export = expand_project_pieces(project, expand_quantities=expand_quantities)
    placed_pieces = compute_grid_layout(pieces_to_export, spacing_mm=40.0)

    for idx, (piece, off_x_mm, off_y_mm) in enumerate(placed_pieces, start=1):
        # Bezpieczna nazwa bloku DXF (tylko wielkie litery, cyfry i podkreślenia)
        clean_id = "".join(ch if ch.isalnum() else "_" for ch in piece.id.upper())
        block_name = f"PIECE_{idx}_{clean_id}"[:31]
        block = doc.blocks.new(name=block_name)

        if piece.has_cut_contour and piece.cut_contour is not None:
            # 1. Obrys cięcia (Cut Boundary) na warstwie '1' (zamknięta POLYLINE 2D)
            cut_pts_mm = contour_to_points(piece.cut_contour, steps_per_curve=steps_per_curve)
            cut_pts_scaled = [(x * scale, y * scale) for x, y in cut_pts_mm]
            if len(cut_pts_scaled) >= 2:
                p0, p_end = cut_pts_scaled[0], cut_pts_scaled[-1]
                if math.hypot(p0[0] - p_end[0], p0[1] - p_end[1]) < 1e-4:
                    cut_pts_scaled = cut_pts_scaled[:-1]

            block.add_polyline2d(
                cut_pts_scaled,
                close=True,
                dxfattribs={"layer": AAMA_LAYER_BOUNDARY},
            )

            # 2. Net sewing line (outer_contour) na warstwie '4' (zamknięta POLYLINE 2D)
            sew_pts_mm = contour_to_points(piece.outer_contour, steps_per_curve=steps_per_curve)
            sew_pts_scaled = [(x * scale, y * scale) for x, y in sew_pts_mm]
            if len(sew_pts_scaled) >= 2:
                p0, p_end = sew_pts_scaled[0], sew_pts_scaled[-1]
                if math.hypot(p0[0] - p_end[0], p0[1] - p_end[1]) < 1e-4:
                    sew_pts_scaled = sew_pts_scaled[:-1]

            block.add_polyline2d(
                sew_pts_scaled,
                close=True,
                dxfattribs={"layer": AAMA_LAYER_INTERNAL},
            )
        else:
            # Obrys zewnętrzny bazowy na warstwie '1' (zamknięta POLYLINE 2D)
            outer_pts_mm = contour_to_points(piece.outer_contour, steps_per_curve=steps_per_curve)
            outer_pts_scaled = [(x * scale, y * scale) for x, y in outer_pts_mm]
            if len(outer_pts_scaled) >= 2:
                p0, p_end = outer_pts_scaled[0], outer_pts_scaled[-1]
                if math.hypot(p0[0] - p_end[0], p0[1] - p_end[1]) < 1e-4:
                    outer_pts_scaled = outer_pts_scaled[:-1]

            block.add_polyline2d(
                outer_pts_scaled,
                close=True,
                dxfattribs={"layer": AAMA_LAYER_BOUNDARY},
            )

        # 3. Linie wewnętrzne na warstwie '4' (lub '5' dla zgięcia fold)
        for internal in piece.internal_lines:
            int_pts_mm = contour_to_points(internal.contour, steps_per_curve=steps_per_curve)
            int_pts_scaled = [(x * scale, y * scale) for x, y in int_pts_mm]
            layer = AAMA_LAYER_FOLD if internal.line_type == "fold" else AAMA_LAYER_INTERNAL
            block.add_polyline2d(
                int_pts_scaled,
                close=internal.contour.is_closed,
                dxfattribs={"layer": layer},
            )

        # 3b. Karbiki montażowe (Notches) na warstwie '2'
        for notch in piece.notches:
            px = notch.position.x * scale
            py = notch.position.y * scale
            L = notch.length_mm * scale
            if notch.direction:
                dx = notch.direction.x * L
                dy = notch.direction.y * L
            else:
                dx = 0.0
                dy = L
            block.add_line(
                start=(px, py),
                end=(px + dx, py + dy),
                dxfattribs={"layer": AAMA_LAYER_NOTCH},
            )

        # 4. Linia nitki prostej (Grainline) na warstwie '7'
        if piece.grainline_start and piece.grainline_end:
            block.add_line(
                start=(piece.grainline_start.x * scale, piece.grainline_start.y * scale),
                end=(piece.grainline_end.x * scale, piece.grainline_end.y * scale),
                dxfattribs={"layer": AAMA_LAYER_GRAINLINE},
            )

        # 5. Etykieta tekstowa AAMA wewnątrz bloku na warstwie '8'
        poly_geom = contour_to_shapely_polygon(piece.effective_boundary)
        rep_pt = poly_geom.representative_point()
        cx = max(10.0, rep_pt.x - piece.target_width_mm * 0.18) * scale
        cy = rep_pt.y * scale
        text_height = 6.0 * scale
        block.add_text(
            f"Piece Name: {piece.name}",
            dxfattribs={
                "layer": AAMA_LAYER_TEXT,
                "insert": (cx, cy),
                "height": text_height,
            },
        )
        block.add_text(
            f"Quantity: {piece.quantity}",
            dxfattribs={
                "layer": AAMA_LAYER_TEXT,
                "insert": (cx, cy - text_height * 1.6),
                "height": text_height * 0.85,
            },
        )
        if piece.has_cut_contour:
            block.add_text(
                f"Seam: {piece.seam_allowance_mm:.1f} mm",
                dxfattribs={
                    "layer": AAMA_LAYER_TEXT,
                    "insert": (cx, cy - text_height * 3.0),
                    "height": text_height * 0.85,
                },
            )

        # Wstawienie bloku na MODELSPACE we właściwym miejscu siatki 2D
        msp.add_blockref(
            name=block_name,
            insert=(off_x_mm * scale, off_y_mm * scale),
            dxfattribs={"layer": AAMA_LAYER_BOUNDARY},
        )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out))
    return out
