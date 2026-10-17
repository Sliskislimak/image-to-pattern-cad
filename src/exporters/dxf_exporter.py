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

from src.core.contract import Contour, PatternPiece, PatternProject, Point2D
from src.core.geometry import (
    contour_to_points,
    contour_to_shapely_polygon,
    get_contour_bounds,
    transform_contour,
)


AAMA_LAYER_BOUNDARY = "1"
AAMA_LAYER_INTERNAL = "4"
AAMA_LAYER_GRAINLINE = "7"
AAMA_LAYER_TEXT = "8"


def _mirror_piece_x(piece: PatternPiece, suffix: str = "_R") -> PatternPiece:
    """Tworzy odbicie lustrzane elementu względem osi X (dla par symetrycznych x2 w Clo3D)."""
    W = piece.target_width_mm

    def _flip_contour(c: Contour) -> Contour:
        # Odbicie: x' = W - x
        flipped = transform_contour(c, sx=-1.0, sy=1.0, dx=W, dy=0.0)
        return flipped

    new_outer = _flip_contour(piece.outer_contour)
    new_internals = [
        line.model_copy(update={"contour": _flip_contour(line.contour)})
        for line in piece.internal_lines
    ]
    g_start = (
        Point2D(x=W - piece.grainline_start.x, y=piece.grainline_start.y)
        if piece.grainline_start
        else None
    )
    g_end = (
        Point2D(x=W - piece.grainline_end.x, y=piece.grainline_end.y)
        if piece.grainline_end
        else None
    )

    return piece.model_copy(
        update={
            "id": f"{piece.id}{suffix.lower()}",
            "name": f"{piece.name}{suffix}",
            "quantity": 1,
            "outer_contour": new_outer,
            "internal_lines": new_internals,
            "grainline_start": g_start,
            "grainline_end": g_end,
        }
    )


def expand_project_pieces(project: PatternProject, expand_quantities: bool = False) -> list[PatternPiece]:
    """
    Zwraca listę elementów do rozmieszczenia:
    - gdy expand_quantities=False: zwraca oryginalne elementy wzorcowe (6 sztuk).
    - gdy expand_quantities=True: rozwija elementy x2 na pary (np. lewy i prawy nausznik / bok),
      dzięki czemu w Clo3D od razu mamy komplet części do zszycia całej czapki (9 sztuk).
    """
    if not expand_quantities:
        return list(project.pieces)

    expanded: list[PatternPiece] = []
    for p in project.pieces:
        if p.quantity <= 1:
            expanded.append(p)
        else:
            # Pierwsza sztuka (L / 1)
            p1 = p.model_copy(
                update={
                    "id": f"{p.id}_1",
                    "name": f"{p.name} (1/{p.quantity})",
                    "quantity": 1,
                }
            )
            expanded.append(p1)
            # Kolejne sztuki (odbicie lustrzane dla symetrycznych części bocznych)
            for idx in range(2, p.quantity + 1):
                p_mirror = _mirror_piece_x(p, suffix=f" ({idx}/{p.quantity})")
                p_mirror.id = f"{p.id}_{idx}"
                expanded.append(p_mirror)
    return expanded


def compute_grid_layout(
    pieces: list[PatternPiece],
    spacing_mm: float = 40.0,
    max_row_width_mm: float = 850.0,
) -> list[tuple[PatternPiece, float, float]]:
    """
    Rozmieszcza elementy w przejrzystej siatce 2D (bez nakładania się),
    zwracając listę krotek: (piece, offset_x_mm, offset_y_mm).
    """
    layout: list[tuple[PatternPiece, float, float]] = []
    cursor_x = 0.0
    cursor_y = 0.0
    row_max_h = 0.0

    for piece in pieces:
        min_x, min_y, max_x, max_y = get_contour_bounds(piece.outer_contour)
        w = max_x - min_x
        h = max_y - min_y

        if cursor_x > 0.0 and (cursor_x + w) > max_row_width_mm:
            cursor_x = 0.0
            cursor_y += row_max_h + spacing_mm
            row_max_h = 0.0

        layout.append((piece, cursor_x, cursor_y))
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

    # Rejestracja warstw AAMA
    layers_spec = [
        (AAMA_LAYER_BOUNDARY, 7),   # 7 = biały/czarny (obrys zewnętrzny cięcia)
        (AAMA_LAYER_INTERNAL, 1),   # 1 = czerwony (linie wewnętrzne / zaszywki)
        (AAMA_LAYER_GRAINLINE, 3),  # 3 = zielony (nitka prosta)
        (AAMA_LAYER_TEXT, 5),       # 5 = niebieski (opisy tekstowe)
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

        # 1. Obrys zewnętrzny na warstwie '1' (zamknięta POLYLINE 2D)
        outer_pts_mm = contour_to_points(piece.outer_contour, steps_per_curve=steps_per_curve)
        outer_pts_scaled = [(x * scale, y * scale) for x, y in outer_pts_mm]
        if len(outer_pts_scaled) >= 2:
            p0, p_end = outer_pts_scaled[0], outer_pts_scaled[-1]
            if math.hypot(p0[0] - p_end[0], p0[1] - p_end[1]) < 1e-4:
                outer_pts_scaled = outer_pts_scaled[:-1]

        poly = block.add_polyline2d(
            outer_pts_scaled,
            close=True,
            dxfattribs={"layer": AAMA_LAYER_BOUNDARY},
        )

        # 2. Linie wewnętrzne na warstwie '4'
        for internal in piece.internal_lines:
            int_pts_mm = contour_to_points(internal.contour, steps_per_curve=steps_per_curve)
            int_pts_scaled = [(x * scale, y * scale) for x, y in int_pts_mm]
            block.add_polyline2d(
                int_pts_scaled,
                close=internal.contour.is_closed,
                dxfattribs={"layer": AAMA_LAYER_INTERNAL},
            )

        # 3. Linia nitki prostej (Grainline) na warstwie '7'
        if piece.grainline_start and piece.grainline_end:
            block.add_line(
                start=(piece.grainline_start.x * scale, piece.grainline_start.y * scale),
                end=(piece.grainline_end.x * scale, piece.grainline_end.y * scale),
                dxfattribs={"layer": AAMA_LAYER_GRAINLINE},
            )

        # 4. Etykieta tekstowa AAMA wewnątrz bloku na warstwie '8'
        poly_geom = contour_to_shapely_polygon(piece.outer_contour)
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
