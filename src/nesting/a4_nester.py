"""
Moduł 2D Nestingu i Kafelkowania A4 (Milestone 2 - R2).

Implementuje:
1. Modele danych Pydantic V2:
   - PlacedPiece: element wykroju z koordynatami (x, y) i rotacją ortogonalną (0, 90, 180, 270)
   - NestingPage: pojedyncza strona A4 (cały element lub wycinek siatki kafelkowania)
   - NestingResult: całościowy wynik układu (tryb, lista stron, wymiary)
2. Rozwijanie elementów projektu z uwzględnieniem mnożników (x1, x2) i odbić lustrzanych
3. Klasyfikator rozmiaru (czy mieści się w oknie zadruku 190x277 mm w 4 orientacjach)
4. Tryb Hybrydowy (Hybrid Mode - Smart Pack & Tile):
   - Elementy mieszczące się w A4 drukowane w całości bez cięcia
   - Elementy ponadwymiarowe kafelkowane z zakładką 15 mm i paserami
   - Brak kolizji elementów na arkuszach wieloczęściowych (poly1.intersection(poly2).area < 1e-2)
5. Tryb Plakatowy (Poster Mode - Full-Sheet Grid Tile):
   - Ciasny nesting wszystkich 9 elementów na wspólnym arkuszu 2D (Shelf Packing Best-Fit)
   - Pocięcie na regularną siatkę arkuszy A4 (R x C) z zakładkami 15 mm
"""

from __future__ import annotations

import math
from typing import Literal, Optional, Sequence
from pydantic import BaseModel, Field
from shapely.affinity import rotate as shapely_rotate, translate as shapely_translate
from shapely.geometry import Polygon, box

from src.core.contract import (
    Contour,
    InternalLine,
    PathSegment,
    PatternPiece,
    PatternProject,
    Point2D,
)
from src.core.geometry import (
    contour_to_points,
    get_contour_bounds,
)


def mirror_piece_x(piece: PatternPiece, suffix: str = "_2") -> PatternPiece:
    """
    Tworzy odbicie lustrzane elementu wykroju względem osi X (dla par symetrycznych x2).
    Obsługuje zarówno kontur szycia (outer_contour), jak i kontur cięcia (cut_contour),
    linie wewnętrzne, nitkę prostą oraz zachowuje naddatki na szwy i role krawędzi.
    Zwraca znormalizowany element (min_x = 0, min_y = 0).
    """
    min_x, min_y, max_x, max_y = get_contour_bounds(piece.effective_boundary, steps_per_curve=48)
    W = max_x - min_x

    def flip_pt(p: Optional[Point2D]) -> Optional[Point2D]:
        if p is None:
            return None
        # Odbicie w osi X i normalizacja do min_x = 0, min_y = 0
        return Point2D(x=W - (p.x - min_x), y=p.y - min_y)

    def flip_contour(c: Optional[Contour]) -> Optional[Contour]:
        if c is None:
            return None
        new_segs: list[PathSegment] = []
        for s in c.segments:
            new_segs.append(
                PathSegment(
                    type=s.type,
                    start=flip_pt(s.end),  # type: ignore[arg-type]
                    end=flip_pt(s.start),  # type: ignore[arg-type]
                    cp1=flip_pt(s.cp2) if s.cp2 else None,
                    cp2=flip_pt(s.cp1) if s.cp1 else None,
                    seam_allowance_mm=s.seam_allowance_mm,
                    edge_role=s.edge_role,
                )
            )
        # Odwrócenie kolejności segmentów zachowuje orientację CCW konturu zamkniętego
        return Contour(segments=list(reversed(new_segs)), is_closed=c.is_closed)

    new_outer = flip_contour(piece.outer_contour)
    new_cut = flip_contour(piece.cut_contour)
    new_internals = [
        InternalLine(
            id=f"{il.id}{suffix}",
            label=il.label,
            line_type=il.line_type,
            contour=flip_contour(il.contour),  # type: ignore[arg-type]
        )
        for il in piece.internal_lines
    ]

    return PatternPiece(
        id=f"{piece.id}{suffix}",
        name=f"{piece.name} ({suffix.lstrip('_')}/{piece.quantity})",
        quantity=1,
        target_width_cm=piece.target_width_cm,
        target_height_cm=piece.target_height_cm,
        outer_contour=new_outer,  # type: ignore[arg-type]
        cut_contour=new_cut,
        internal_lines=new_internals,
        grainline_start=flip_pt(piece.grainline_start),
        grainline_end=flip_pt(piece.grainline_end),
        seam_allowance_mm=piece.seam_allowance_mm,
        fabric_note=piece.fabric_note,
    )


def rotate_piece(piece: PatternPiece, rotation_deg: int) -> PatternPiece:
    """
    Obraca element o zadany kąt ortogonalny (0, 90, 180, 270 stopni)
    oraz normalizuje jego lewy dolny róg do punktu (0, 0).
    """
    rot = rotation_deg % 360
    if rot == 0:
        # Zwracamy kopię znormalizowaną do (0, 0)
        min_x, min_y, _, _ = get_contour_bounds(piece.effective_boundary, steps_per_curve=48)
        if abs(min_x) < 1e-4 and abs(min_y) < 1e-4:
            return piece
        dx, dy = -min_x, -min_y

        def norm_pt(p: Optional[Point2D]) -> Optional[Point2D]:
            if p is None:
                return None
            return Point2D(x=p.x + dx, y=p.y + dy)

        def norm_contour(c: Optional[Contour]) -> Optional[Contour]:
            if c is None:
                return None
            new_segs = [
                PathSegment(
                    type=s.type,
                    start=norm_pt(s.start),  # type: ignore[arg-type]
                    end=norm_pt(s.end),  # type: ignore[arg-type]
                    cp1=norm_pt(s.cp1),
                    cp2=norm_pt(s.cp2),
                    seam_allowance_mm=s.seam_allowance_mm,
                    edge_role=s.edge_role,
                )
                for s in c.segments
            ]
            return Contour(segments=new_segs, is_closed=c.is_closed)

        new_internals = [
            InternalLine(
                id=il.id,
                label=il.label,
                line_type=il.line_type,
                contour=norm_contour(il.contour),  # type: ignore[arg-type]
            )
            for il in piece.internal_lines
        ]

        return piece.model_copy(
            update={
                "outer_contour": norm_contour(piece.outer_contour),
                "cut_contour": norm_contour(piece.cut_contour),
                "internal_lines": new_internals,
                "grainline_start": norm_pt(piece.grainline_start),
                "grainline_end": norm_pt(piece.grainline_end),
            }
        )

    def raw_rot(x: float, y: float) -> tuple[float, float]:
        if rot == 90:
            return -y, x
        elif rot == 180:
            return -x, -y
        elif rot == 270:
            return y, -x
        return x, y

    # Wyznaczamy przesunięcie do (0, 0) na podstawie obróconego konturu efektywnego
    pts = contour_to_points(piece.effective_boundary, steps_per_curve=48)
    r_pts = [raw_rot(x, y) for x, y in pts]
    min_x = min(p[0] for p in r_pts)
    min_y = min(p[1] for p in r_pts)

    def rot_pt(p: Optional[Point2D]) -> Optional[Point2D]:
        if p is None:
            return None
        rx, ry = raw_rot(p.x, p.y)
        return Point2D(x=rx - min_x, y=ry - min_y)

    def rot_contour(c: Optional[Contour]) -> Optional[Contour]:
        if c is None:
            return None
        new_segs = [
            PathSegment(
                type=s.type,
                start=rot_pt(s.start),  # type: ignore[arg-type]
                end=rot_pt(s.end),  # type: ignore[arg-type]
                cp1=rot_pt(s.cp1),
                cp2=rot_pt(s.cp2),
                seam_allowance_mm=s.seam_allowance_mm,
                edge_role=s.edge_role,
            )
            for s in c.segments
        ]
        return Contour(segments=new_segs, is_closed=c.is_closed)

    new_outer = rot_contour(piece.outer_contour)
    new_cut = rot_contour(piece.cut_contour)
    new_internals = [
        InternalLine(
            id=il.id,
            label=il.label,
            line_type=il.line_type,
            contour=rot_contour(il.contour),  # type: ignore[arg-type]
        )
        for il in piece.internal_lines
    ]

    tw = piece.target_height_cm if rot in (90, 270) else piece.target_width_cm
    th = piece.target_width_cm if rot in (90, 270) else piece.target_height_cm

    return PatternPiece(
        id=f"{piece.id}_rot{rot}",
        name=piece.name,
        quantity=piece.quantity,
        target_width_cm=tw,
        target_height_cm=th,
        outer_contour=new_outer,  # type: ignore[arg-type]
        cut_contour=new_cut,
        internal_lines=new_internals,
        grainline_start=rot_pt(piece.grainline_start),
        grainline_end=rot_pt(piece.grainline_end),
        seam_allowance_mm=piece.seam_allowance_mm,
        fabric_note=piece.fabric_note,
    )


def transform_piece_geometry(
    piece: PatternPiece,
    rotation_deg: int,
    dx: float,
    dy: float,
) -> PatternPiece:
    """Obraca element o rotation_deg i przesuwa go o wektor (dx, dy)."""
    p_rot = rotate_piece(piece, rotation_deg)
    if abs(dx) < 1e-4 and abs(dy) < 1e-4:
        return p_rot

    def trans_pt(p: Optional[Point2D]) -> Optional[Point2D]:
        if p is None:
            return None
        return Point2D(x=p.x + dx, y=p.y + dy)

    def trans_contour(c: Optional[Contour]) -> Optional[Contour]:
        if c is None:
            return None
        new_segs = [
            PathSegment(
                type=s.type,
                start=trans_pt(s.start),  # type: ignore[arg-type]
                end=trans_pt(s.end),  # type: ignore[arg-type]
                cp1=trans_pt(s.cp1),
                cp2=trans_pt(s.cp2),
                seam_allowance_mm=s.seam_allowance_mm,
                edge_role=s.edge_role,
            )
            for s in c.segments
        ]
        return Contour(segments=new_segs, is_closed=c.is_closed)

    return PatternPiece(
        id=p_rot.id,
        name=p_rot.name,
        quantity=p_rot.quantity,
        target_width_cm=p_rot.target_width_cm,
        target_height_cm=p_rot.target_height_cm,
        outer_contour=trans_contour(p_rot.outer_contour),  # type: ignore[arg-type]
        cut_contour=trans_contour(p_rot.cut_contour),
        internal_lines=[
            InternalLine(
                id=il.id,
                label=il.label,
                line_type=il.line_type,
                contour=trans_contour(il.contour),  # type: ignore[arg-type]
            )
            for il in p_rot.internal_lines
        ],
        grainline_start=trans_pt(p_rot.grainline_start),
        grainline_end=trans_pt(p_rot.grainline_end),
        seam_allowance_mm=p_rot.seam_allowance_mm,
        fabric_note=p_rot.fabric_note,
    )


class PlacedPiece(BaseModel):
    """
    Reprezentacja umieszczonego elementu wykroju na płaszczyźnie roboczej lub arkuszu.
    """

    piece: PatternPiece
    x_mm: float
    y_mm: float
    rotation_deg: int = 0  # 0, 90, 180, 270

    def get_placed_piece(self) -> PatternPiece:
        """Zwraca element po transformacji (obrót i translacja)."""
        return transform_piece_geometry(self.piece, self.rotation_deg, self.x_mm, self.y_mm)

    def get_placed_polygon(self, boundary_type: str = "effective") -> Polygon:
        """Zwraca obiekt Polygon biblioteki Shapely dla umieszczonego elementu."""
        p = self.get_placed_piece()
        boundary = (
            p.cut_contour
            if (boundary_type == "cut" and p.cut_contour)
            else (p.outer_contour if boundary_type == "outer" else p.effective_boundary)
        )
        pts = contour_to_points(boundary, steps_per_curve=48)
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly


class NestingPage(BaseModel):
    """
    Reprezentacja pojedynczej strony A4 wygenerowanej przez nester.
    """

    page_index: int
    row: int
    col: int
    total_rows: int
    total_cols: int
    is_tiled: bool
    piece_name: str
    placed_pieces: list[PlacedPiece]
    tile_box_mm: tuple[float, float, float, float]  # (gx0, gy0, gx1, gy1)


class NestingResult(BaseModel):
    """
    Kompletny wynik nestingu dla całego projektu.
    """

    mode: Literal["hybrid", "poster"]
    pages: list[NestingPage]
    total_pages: int
    layout_width_mm: float
    layout_height_mm: float
    margin_mm: float = 10.0
    overlap_mm: float = 15.0


def expand_project_pieces(
    project: PatternProject,
    mirror_symmetric: bool = True,
) -> list[PatternPiece]:
    """
    Rozwija elementy projektu uwzględniając mnożniki ilości (quantity x1, x2).
    Dla quantity > 1 tworzy ponumerowane kopie, opcjonalnie odbijając drugą sztukę lustrzanie.
    Dla projektu Gorro Trapper zwraca dokładnie 9 elementów.
    """
    expanded: list[PatternPiece] = []
    for p in project.pieces:
        if p.quantity <= 1:
            p_single = p.model_copy(update={"quantity": 1})
            expanded.append(p_single)
        else:
            p1 = p.model_copy(
                update={
                    "id": f"{p.id}_1",
                    "name": f"{p.name} (1/{p.quantity})",
                    "quantity": 1,
                }
            )
            expanded.append(p1)
            for idx in range(2, p.quantity + 1):
                if mirror_symmetric:
                    p_mir = mirror_piece_x(p, suffix=f"_{idx}")
                    p_mir.name = f"{p.name} ({idx}/{p.quantity})"
                    p_mir.quantity = 1
                    expanded.append(p_mir)
                else:
                    p_copy = p.model_copy(
                        update={
                            "id": f"{p.id}_{idx}",
                            "name": f"{p.name} ({idx}/{p.quantity})",
                            "quantity": 1,
                        }
                    )
                    expanded.append(p_copy)
    return expanded


def classify_piece_fit(
    piece: PatternPiece,
    printable_w_mm: float = 190.0,
    printable_h_mm: float = 277.0,
    overlap_mm: float = 15.0,
) -> tuple[bool, int, float, float]:
    """
    Bada czy element mieści się w obszarze zadruku pojedynczej strony A4 (190 x 277 mm)
    w 4 ortogonalnych rotacjach (0, 90, 180, 270 stopni).

    Zwraca krotkę:
    (is_whole: bool, best_rotation_deg: int, width_mm: float, height_mm: float)
    """
    # Sprawdzamy rotacje preferując 0°, następnie 90°, 180°, 270°
    for rot in (0, 90, 180, 270):
        p_rot = rotate_piece(piece, rot)
        min_x, min_y, max_x, max_y = get_contour_bounds(p_rot.effective_boundary, steps_per_curve=48)
        w = max_x - min_x
        h = max_y - min_y
        if w <= printable_w_mm + 1e-3 and h <= printable_h_mm + 1e-3:
            return (True, rot, w, h)

    # Element nie mieści się w całości w żadnej orientacji -> OVERSIZED
    # Wybieramy orientację, która minimalizuje liczbę wymaganych arkuszy A4 (R x C)
    best_rot = 0
    min_tiles = 1000
    best_w, best_h = 0.0, 0.0

    step_x = printable_w_mm - overlap_mm
    step_y = printable_h_mm - overlap_mm

    for rot in (0, 90):
        p_rot = rotate_piece(piece, rot)
        min_x, min_y, max_x, max_y = get_contour_bounds(p_rot.effective_boundary, steps_per_curve=48)
        w = max_x - min_x
        h = max_y - min_y
        cols = max(1, math.ceil((w - overlap_mm) / step_x))
        rows = max(1, math.ceil((h - overlap_mm) / step_y))
        tiles = rows * cols
        if tiles < min_tiles:
            min_tiles = tiles
            best_rot = rot
            best_w = w
            best_h = h

    return (False, best_rot, best_w, best_h)


def nest_hybrid_mode(
    project: PatternProject,
    page_w_mm: float = 210.0,
    page_h_mm: float = 297.0,
    margin_mm: float = 10.0,
    overlap_mm: float = 15.0,
) -> NestingResult:
    """
    Tryb Hybrydowy (Hybrid Mode — Smart Pack & Tile):
    1. Rozwija wszystkie elementy (9 sztuk dla Gorro Trapper).
    2. Elementy, które mieszczą się na pojedynczej stronie A4 (Frente, 2x Visera z rotacją 90°,
       2x Lateral) są pakowane w całości na arkuszach A4 bez żadnego cięcia ani sklejania.
       Gdy wiele elementów trafia na jedną stronę, egzekwowany jest warunek braku kolizji
       (poly1.intersection(poly2).area < 1e-2 mm²).
    3. Elementy ponadwymiarowe (Fuelle superior 350mm, Cubre boca 300mm, 2x Orejera 270mm)
       są automatycznie kafelkowane na sąsiadujące arkusze A4 z zakładką 15.0 mm i paserami.
    """
    printable_w = page_w_mm - 2.0 * margin_mm  # 190.0 mm
    printable_h = page_h_mm - 2.0 * margin_mm  # 277.0 mm
    step_x = printable_w - overlap_mm  # 175.0 mm
    step_y = printable_h - overlap_mm  # 262.0 mm

    expanded_pieces = expand_project_pieces(project, mirror_symmetric=True)
    if not expanded_pieces:
        return NestingResult(
            mode="hybrid",
            pages=[],
            total_pages=0,
            layout_width_mm=printable_w,
            layout_height_mm=printable_h,
            margin_mm=margin_mm,
            overlap_mm=overlap_mm,
        )

    whole_pieces: list[tuple[PatternPiece, int, float, float]] = []
    oversized_pieces: list[tuple[PatternPiece, int, float, float]] = []

    for p in expanded_pieces:
        is_whole, rot, w, h = classify_piece_fit(p, printable_w, printable_h, overlap_mm=overlap_mm)
        if is_whole:
            whole_pieces.append((p, rot, w, h))
        else:
            oversized_pieces.append((p, rot, w, h))

    pages: list[NestingPage] = []
    page_index = 1

    # 1. Pakowanie elementów mieszczących się w całości (Whole Pieces)
    # Stosujemy algorytm Shelf / First-Fit Decreasing z precyzyjną detekcją kolizji Shapely
    open_sheets: list[dict] = []

    for piece, rot, w, h in whole_pieces:
        placed = False
        p_cand = PlacedPiece(piece=piece, x_mm=0.0, y_mm=0.0, rotation_deg=rot)

        # Próbujemy umieścić na istniejącym arkuszu
        for sheet in open_sheets:
            # Sprawdzamy pozycję obok istniejących elementów lub powyżej
            candidate_positions = [
                (sheet["cursor_x"], 0.0),
                (0.0, sheet["max_h"] + 10.0),
            ]
            for cx, cy in candidate_positions:
                if (cx + w <= printable_w + 1e-3) and (cy + h <= printable_h + 1e-3):
                    cand_piece = PlacedPiece(piece=piece, x_mm=cx, y_mm=cy, rotation_deg=rot)
                    poly_cand = cand_piece.get_placed_polygon()
                    collision = False
                    for existing_poly in sheet["polygons"]:
                        if existing_poly.intersection(poly_cand).area >= 1e-2:
                            collision = True
                            break
                    if not collision:
                        sheet["pieces"].append(cand_piece)
                        sheet["polygons"].append(poly_cand)
                        sheet["cursor_x"] = max(sheet["cursor_x"], cx + w + 10.0)
                        sheet["max_h"] = max(sheet["max_h"], cy + h)
                        placed = True
                        break
            if placed:
                break

        if not placed:
            # Otwieramy nowy arkusz dla tego elementu
            # Jeśli element jest jedyny na stronie, centrujemy go estetycznie
            cx = max(0.0, (printable_w - w) / 2.0)
            cy = max(0.0, (printable_h - h) / 2.0)
            cand_piece = PlacedPiece(piece=piece, x_mm=cx, y_mm=cy, rotation_deg=rot)
            open_sheets.append(
                {
                    "pieces": [cand_piece],
                    "polygons": [cand_piece.get_placed_polygon()],
                    "cursor_x": cx + w + 10.0,
                    "max_h": cy + h,
                    "cursor_y": 0.0,
                }
            )

    # Konwertujemy arkusze whole_pieces na NestingPage
    for sheet in open_sheets:
        pieces_on_sheet: list[PlacedPiece] = sheet["pieces"]
        # Weryfikacja braku kolizji dla pewności matematycznej
        if len(pieces_on_sheet) > 1:
            for i in range(len(pieces_on_sheet)):
                for j in range(i + 1, len(pieces_on_sheet)):
                    p1 = pieces_on_sheet[i].get_placed_polygon()
                    p2 = pieces_on_sheet[j].get_placed_polygon()
                    overlap = p1.intersection(p2).area
                    assert overlap < 1e-2, f"Kolizja na arkuszu: {overlap:.4f} mm²"

        names = ", ".join(p.piece.name for p in pieces_on_sheet)
        pages.append(
            NestingPage(
                page_index=page_index,
                row=1,
                col=1,
                total_rows=1,
                total_cols=1,
                is_tiled=False,
                piece_name=names,
                placed_pieces=pieces_on_sheet,
                tile_box_mm=(0.0, 0.0, printable_w, printable_h),
            )
        )
        page_index += 1

    # 2. Kafelkowanie elementów ponadwymiarowych (Oversized Pieces)
    for piece, rot, w, h in oversized_pieces:
        cols = max(1, math.ceil((w - overlap_mm) / step_x))
        rows = max(1, math.ceil((h - overlap_mm) / step_y))

        # Obliczamy całkowity obszar siatki kafelkowania
        w_grid = printable_w + (cols - 1) * step_x
        h_grid = printable_h + (rows - 1) * step_y

        # Centrujemy element w siatce, aby cięcie było symetryczne i estetyczne
        ox = max(0.0, (w_grid - w) / 2.0)
        oy = max(0.0, (h_grid - h) / 2.0)

        placed_piece = PlacedPiece(piece=piece, x_mm=ox, y_mm=oy, rotation_deg=rot)
        piece_poly = placed_piece.get_placed_polygon()

        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                gx0 = (c - 1) * step_x
                gy0 = (r - 1) * step_y
                gx1 = gx0 + printable_w
                gy1 = gy0 + printable_h
                tile_rect = box(gx0, gy0, gx1, gy1)

                # Sprawdzamy czy ten kafel ma jakąkolwiek część elementu
                if tile_rect.intersects(piece_poly):
                    pages.append(
                        NestingPage(
                            page_index=page_index,
                            row=r,
                            col=c,
                            total_rows=rows,
                            total_cols=cols,
                            is_tiled=True,
                            piece_name=f"{piece.name} (R{r}C{c})",
                            placed_pieces=[placed_piece],
                            tile_box_mm=(gx0, gy0, gx1, gy1),
                        )
                    )
                    page_index += 1

    return NestingResult(
        mode="hybrid",
        pages=pages,
        total_pages=len(pages),
        layout_width_mm=printable_w,
        layout_height_mm=printable_h,
        margin_mm=margin_mm,
        overlap_mm=overlap_mm,
    )


def nest_poster_mode(
    project: PatternProject,
    page_w_mm: float = 210.0,
    page_h_mm: float = 297.0,
    margin_mm: float = 10.0,
    overlap_mm: float = 15.0,
    max_poster_width_mm: float = 700.0,
) -> NestingResult:
    """
    Tryb Plakatowy (Poster Mode — Full-Sheet Grid Tile):
    1. Rozwija wszystkie elementy projektu (9 sztuk dla Gorro Trapper).
    2. Ciasno upakowuje wszystkie elementy na wspólnym 2D master canvas
       za pomocą algorytmu Shelf Packing Best-Fit z badaniem obrotów 0°/90°.
    3. Dzieli cały master canvas na regularną siatkę arkuszy A4 (R x C)
       z zakładką 15.0 mm i paserami dopasowania.
    """
    printable_w = page_w_mm - 2.0 * margin_mm  # 190.0 mm
    printable_h = page_h_mm - 2.0 * margin_mm  # 277.0 mm
    step_x = printable_w - overlap_mm  # 175.0 mm
    step_y = printable_h - overlap_mm  # 262.0 mm

    expanded_pieces = expand_project_pieces(project, mirror_symmetric=True)
    if not expanded_pieces:
        return NestingResult(
            mode="poster",
            pages=[],
            total_pages=0,
            layout_width_mm=0.0,
            layout_height_mm=0.0,
            margin_mm=margin_mm,
            overlap_mm=overlap_mm,
        )

    # Przygotowujemy listę elementów z wymiarami
    piece_data: list[tuple[PatternPiece, float, float]] = []
    for p in expanded_pieces:
        min_x, min_y, max_x, max_y = get_contour_bounds(p.effective_boundary, steps_per_curve=48)
        piece_data.append((p, max_x - min_x, max_y - min_y))

    # Sortujemy malejąco według maksymalnego wymiaru (Best-Fit Decreasing)
    piece_data.sort(key=lambda item: max(item[1], item[2]), reverse=True)

    spacing = 15.0
    shelves: list[dict] = []
    curr_y = 0.0

    placed_master_pieces: list[PlacedPiece] = []

    for piece, orig_w, orig_h in piece_data:
        orientations = [(orig_w, orig_h, 0), (orig_h, orig_w, 90)]

        # Szukamy istniejącej półki z najlepszym dopasowaniem
        best_shelf = None
        best_diff = 1e9
        best_orient = None

        for s in shelves:
            for ow, oh, rot in orientations:
                if s["cursor_x"] + ow <= max_poster_width_mm and oh <= s["h"]:
                    diff = s["h"] - oh
                    if diff < best_diff:
                        best_diff = diff
                        best_shelf = s
                        best_orient = (ow, oh, rot)

        if best_shelf and best_orient:
            ow, oh, rot = best_orient
            px = best_shelf["cursor_x"]
            py = best_shelf["y"]
            placed_master_pieces.append(PlacedPiece(piece=piece, x_mm=px, y_mm=py, rotation_deg=rot))
            best_shelf["cursor_x"] += ow + spacing
        else:
            # Tworzymy nową półkę, orientując element tak, aby zminimalizować wysokość
            cand = [o for o in orientations if o[0] <= max_poster_width_mm]
            if not cand:
                cand = orientations
            cand.sort(key=lambda o: o[1])  # sortowanie po wysokości
            ow, oh, rot = cand[0]
            new_shelf = {
                "y": curr_y,
                "h": oh,
                "cursor_x": ow + spacing,
            }
            shelves.append(new_shelf)
            placed_master_pieces.append(PlacedPiece(piece=piece, x_mm=0.0, y_mm=curr_y, rotation_deg=rot))
            curr_y += oh + spacing

    layout_w = max(s["cursor_x"] - spacing for s in shelves)
    layout_h = curr_y - spacing

    # Obliczamy wymiary siatki A4 (R x C)
    cols = max(1, math.ceil((layout_w - overlap_mm) / step_x))
    rows = max(1, math.ceil((layout_h - overlap_mm) / step_y))

    w_grid = printable_w + (cols - 1) * step_x
    h_grid = printable_h + (rows - 1) * step_y

    # Centrujemy układ w globalnej siatce
    ox = max(0.0, (w_grid - layout_w) / 2.0)
    oy = max(0.0, (h_grid - layout_h) / 2.0)

    # Przesuwamy elementy na master canvas o (ox, oy)
    for pp in placed_master_pieces:
        pp.x_mm += ox
        pp.y_mm += oy

    # Weryfikacja braku nakładania się elementów na master canvas
    for i in range(len(placed_master_pieces)):
        for j in range(i + 1, len(placed_master_pieces)):
            p1 = placed_master_pieces[i].get_placed_polygon()
            p2 = placed_master_pieces[j].get_placed_polygon()
            area = p1.intersection(p2).area
            assert area < 1e-2, (
                f"Kolizja na master canvas między {placed_master_pieces[i].piece.name} "
                f"a {placed_master_pieces[j].piece.name}: {area:.4f} mm²"
            )

    pages: list[NestingPage] = []
    page_index = 1

    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            gx0 = (c - 1) * step_x
            gy0 = (r - 1) * step_y
            gx1 = gx0 + printable_w
            gy1 = gy0 + printable_h
            tile_rect = box(gx0, gy0, gx1, gy1)

            # Szukamy elementów przecinających ten kafel
            intersecting: list[PlacedPiece] = []
            for pp in placed_master_pieces:
                if tile_rect.intersects(pp.get_placed_polygon()):
                    intersecting.append(pp)

            names = ", ".join(pp.piece.name for pp in intersecting)
            pages.append(
                NestingPage(
                    page_index=page_index,
                    row=r,
                    col=c,
                    total_rows=rows,
                    total_cols=cols,
                    is_tiled=True,
                    piece_name=f"Poster: {names}" if names else "Poster (Siatka)",
                    placed_pieces=intersecting,
                    tile_box_mm=(gx0, gy0, gx1, gy1),
                )
            )
            page_index += 1

    return NestingResult(
        mode="poster",
        pages=pages,
        total_pages=len(pages),
        layout_width_mm=w_grid,
        layout_height_mm=h_grid,
        margin_mm=margin_mm,
        overlap_mm=overlap_mm,
    )
