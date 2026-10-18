"""
Moduł operacji geometrycznych 2D:
- Adaptacyjna dyskretyzacja krzywych Béziera do polilinii (dla DXF-AAMA, Shapely i Clipper2)
- Wyliczanie dokładnego BoundingBoxa w milimetrach
- Skalowanie anizotropowe (osobno w osi X i Y) do docelowych wymiarów (target_width_cm, target_height_cm)
- Automatyczne wyznaczanie linii nitki prostej (Grainline) oraz środka ciężkości.
"""

from __future__ import annotations

import math
from typing import Optional
from shapely.geometry import Polygon, LineString

from src.core.contract import (
    MM_PER_CM,
    Contour,
    InternalLine,
    Notch,
    PathSegment,
    PatternPiece,
    PatternProject,
    Point2D,
)


def evaluate_cubic_bezier(
    p0: Point2D,
    p1: Point2D,
    p2: Point2D,
    p3: Point2D,
    t: float,
) -> Point2D:
    """Oblicza punkt na krzywej Béziera 3. stopnia dla parametru t in [0, 1]."""
    u = 1.0 - t
    tt = t * t
    uu = u * u
    uuu = uu * u
    ttt = tt * t

    x = uuu * p0.x + 3.0 * uu * t * p1.x + 3.0 * u * tt * p2.x + ttt * p3.x
    y = uuu * p0.y + 3.0 * uu * t * p1.y + 3.0 * u * tt * p2.y + ttt * p3.y
    return Point2D(x=x, y=y)


def flatten_segment(segment: PathSegment, steps_per_curve: int = 32) -> list[Point2D]:
    """
    Zamienia pojedynczy segment (linię lub krzywą Béziera) na listę punktów (bez punktu końcowego,
    który będzie początkiem kolejnego segmentu lub zostanie dodany na końcu konturu).
    """
    if segment.type == "line" or segment.cp1 is None or segment.cp2 is None:
        return [segment.start]

    pts: list[Point2D] = []
    for i in range(steps_per_curve):
        t = i / float(steps_per_curve)
        pts.append(evaluate_cubic_bezier(segment.start, segment.cp1, segment.cp2, segment.end, t))
    return pts


def contour_to_points(contour: Contour, steps_per_curve: int = 32) -> list[tuple[float, float]]:
    """
    Dyskretyzuje obiekt Contour (linie + krzywe Béziera) do listy współrzędnych (x, y) w mm.
    """
    if not contour.segments:
        return []

    points: list[tuple[float, float]] = []
    for seg in contour.segments:
        for pt in flatten_segment(seg, steps_per_curve=steps_per_curve):
            points.append((pt.x, pt.y))

    last_end = contour.segments[-1].end
    if not contour.is_closed:
        points.append((last_end.x, last_end.y))
    else:
        # Dla zamkniętego konturu upewniamy się, że ostatni segment domyka się z pierwszym punktem
        first_pt = points[0]
        if math.hypot(last_end.x - first_pt[0], last_end.y - first_pt[1]) > 1e-4:
            points.append((last_end.x, last_end.y))

    return points


def get_contour_bounds(contour: Contour, steps_per_curve: int = 48) -> tuple[float, float, float, float]:
    """
    Zwraca dokładny Bounding Box konturu: (min_x, min_y, max_x, max_y) w mm.
    """
    pts = contour_to_points(contour, steps_per_curve=steps_per_curve)
    if not pts:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def get_contour_dimensions_mm(contour: Contour, steps_per_curve: int = 48) -> tuple[float, float]:
    """Zwraca (szerokość_mm, wysokość_mm) konturu."""
    min_x, min_y, max_x, max_y = get_contour_bounds(contour, steps_per_curve=steps_per_curve)
    return (max_x - min_x, max_y - min_y)


def transform_contour(
    contour: Contour,
    sx: float = 1.0,
    sy: float = 1.0,
    dx: float = 0.0,
    dy: float = 0.0,
) -> Contour:
    """Skaluje i przesuwa wszystkie punkty (w tym punkty kontrolne krzywych Béziera) konturu."""
    new_segments: list[PathSegment] = []
    for seg in contour.segments:
        start = seg.start.scaled(sx, sy).translated(dx, dy)
        end = seg.end.scaled(sx, sy).translated(dx, dy)
        cp1 = seg.cp1.scaled(sx, sy).translated(dx, dy) if seg.cp1 else None
        cp2 = seg.cp2.scaled(sx, sy).translated(dx, dy) if seg.cp2 else None
        new_segments.append(
            PathSegment(
                type=seg.type,
                start=start,
                end=end,
                cp1=cp1,
                cp2=cp2,
            )
        )
    return Contour(segments=new_segments, is_closed=contour.is_closed)


def calibrate_piece_anisotropic(piece: PatternPiece) -> PatternPiece:
    """
    Kalibruje element wykroju (skalowanie anizotropowe 2D):
    1. Przesuwa lewy dolny róg BoundingBoxa do punktu (0, 0).
    2. Przeskalowuje oś X oraz oś Y niezależnie, aby szerokość i wysokość
       wynosiły DOKŁADNIE (target_width_mm, target_height_mm) co do 0.001 mm.
    3. Jeśli element nie ma zdefiniowanej nitki prostej (grainline), generuje ją pionowo w osi elementu.
    """
    min_x, min_y, max_x, max_y = get_contour_bounds(piece.outer_contour, steps_per_curve=64)
    curr_w = max_x - min_x
    curr_h = max_y - min_y

    if curr_w <= 1e-6 or curr_h <= 1e-6:
        raise ValueError(f"Element '{piece.name}' ma zerowe wymiary konturu ({curr_w} x {curr_h}).")

    # Krok 1: Normalizacja do (0, 0) i skalowanie zgrubne
    sx = piece.target_width_mm / curr_w
    sy = piece.target_height_mm / curr_h

    def _apply(c: Contour) -> Contour:
        shifted = transform_contour(c, sx=1.0, sy=1.0, dx=-min_x, dy=-min_y)
        return transform_contour(shifted, sx=sx, sy=sy, dx=0.0, dy=0.0)

    new_outer = _apply(piece.outer_contour)
    new_internals = [
        line.model_copy(update={"contour": _apply(line.contour)})
        for line in piece.internal_lines
    ]

    # Wyznaczamy domyślną nitkę prostą (Grainline) w środku elementu, jeśli nie podano
    W = piece.target_width_mm
    H = piece.target_height_mm
    if piece.grainline_start and piece.grainline_end:
        g_start = piece.grainline_start.translated(-min_x, -min_y).scaled(sx, sy)
        g_end = piece.grainline_end.translated(-min_x, -min_y).scaled(sx, sy)
    else:
        margin_y = max(15.0, H * 0.2)
        g_start = Point2D(x=W * 0.5, y=margin_y)
        g_end = Point2D(x=W * 0.5, y=H - margin_y)

    return piece.model_copy(
        update={
            "outer_contour": new_outer,
            "cut_contour": None,  # Resetujemy naddatek na szew po zmianie gabarytow elementu
            "internal_lines": new_internals,
            "grainline_start": g_start,
            "grainline_end": g_end,
        }
    )


def mirror_piece_x(piece: PatternPiece, suffix: str = "_2") -> PatternPiece:
    """
    Tworzy odbicie lustrzane elementu wykroju względem osi X (dla par symetrycznych x2).
    Obsługuje kontur szycia (outer_contour), kontur cięcia (cut_contour),
    linie wewnętrzne, karbiki (notches), nitkę prostą oraz zachowuje naddatki na szwy i role krawędzi.
    Zwraca znormalizowany element (min_x = 0, min_y = 0).
    """
    min_x, min_y, max_x, max_y = get_contour_bounds(piece.effective_boundary, steps_per_curve=48)
    W = max_x - min_x

    def flip_pt(p: Optional[Point2D]) -> Optional[Point2D]:
        if p is None:
            return None
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
    new_notches = [
        Notch(
            id=f"{n.id}{suffix}",
            position=flip_pt(n.position),  # type: ignore[arg-type]
            direction=Point2D(x=-n.direction.x, y=n.direction.y) if n.direction else None,
            length_mm=n.length_mm,
            notch_type=n.notch_type,
            label=n.label,
        )
        for n in piece.notches
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
        notches=new_notches,
        grainline_start=flip_pt(piece.grainline_start),
        grainline_end=flip_pt(piece.grainline_end),
        seam_allowance_mm=piece.seam_allowance_mm,
        fabric_note=piece.fabric_note,
    )


def expand_project_pieces(
    project: PatternProject,
    mirror_symmetric: bool = True,
) -> list[PatternPiece]:
    """
    Rozwija elementy projektu uwzględniając mnożniki ilości (quantity x1, x2).
    Dla quantity > 1 tworzy ponumerowane kopie, opcjonalnie odbijając drugą sztukę lustrzanie.
    """
    expanded: list[PatternPiece] = []
    for p in project.pieces:
        if p.quantity <= 1:
            expanded.append(p.model_copy(update={"quantity": 1}))
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


def unfold_piece_on_fold(piece: PatternPiece) -> PatternPiece:
    """
    Rozkłada element wykroju wzdłuż krawędzi zgięcia (Fold Edge / Doblez) do pełnego elementu 1:1.
    - Identyfikuje segment z rolą 'fold'.
    - Odbija pozostały obrys symetrycznie względem prostej wyznaczonej przez linię zgięcia.
    - Łączy obrys oryginalny z odbitym w jeden spójny, zamknięty kontur zewnętrzny.
    - Dodaje oryginalną linię zgięcia jako InternalLine (line_type='fold').
    - Odbija nacięcia (notches) oraz linie wewnętrzne.
    - Normalizuje współrzędne tak, aby min_x = 0, min_y = 0.
    """
    fold_idx = piece.fold_segment_index
    if fold_idx is None:
        raise ValueError(
            f"Element '{piece.name}' nie posiada krawędzi o roli 'fold' (zgięcie materiału)."
        )

    segs = piece.outer_contour.segments
    fold_seg = segs[fold_idx]
    A = fold_seg.start
    B = fold_seg.end

    dx = B.x - A.x
    dy = B.y - A.y
    L = math.hypot(dx, dy)
    if L < 1e-6:
        raise ValueError(f"Krawędź zgięcia w '{piece.name}' ma zerową długość.")

    ux = dx / L
    uy = dy / L

    def reflect_pt(p: Point2D) -> Point2D:
        vx = p.x - A.x
        vy = p.y - A.y
        t = vx * ux + vy * uy
        qx = A.x + t * ux
        qy = A.y + t * uy
        return Point2D(x=2.0 * qx - p.x, y=2.0 * qy - p.y)

    def reflect_segment_reversed(s: PathSegment) -> PathSegment:
        new_start = reflect_pt(s.end)
        new_end = reflect_pt(s.start)
        new_cp1 = reflect_pt(s.cp2) if s.cp2 else None
        new_cp2 = reflect_pt(s.cp1) if s.cp1 else None
        return PathSegment(
            type=s.type,
            start=new_start,
            end=new_end,
            cp1=new_cp1,
            cp2=new_cp2,
            seam_allowance_mm=s.seam_allowance_mm,
            edge_role=s.edge_role,
        )

    # Obrys bez krawędzi zgięcia: od B do A
    n = len(segs)
    other_segs: list[PathSegment] = []
    for i in range(1, n):
        idx = (fold_idx + i) % n
        other_segs.append(segs[idx])

    # Odbite segmenty w odwróconej kolejności: od A do B
    mirrored_segs = [reflect_segment_reversed(s) for s in reversed(other_segs)]

    # Łączymy: other_segs (B -> A) + mirrored_segs (A -> B)
    combined_segments = other_segs + mirrored_segs
    new_outer_contour = Contour(segments=combined_segments, is_closed=True)

    # Oryginalna linia zgięcia staje się wewnętrzną osią symetrii
    fold_internal = InternalLine(
        id=f"{piece.id}_fold_axis",
        label="Linia zgięcia (Fold)",
        line_type="fold",
        contour=Contour(
            segments=[PathSegment.line(A, B, edge_role="fold")],
            is_closed=False,
        ),
    )

    new_internals = list(piece.internal_lines) + [fold_internal]
    for il in piece.internal_lines:
        refl_segs = []
        for s in il.contour.segments:
            refl_segs.append(
                PathSegment(
                    type=s.type,
                    start=reflect_pt(s.start),
                    end=reflect_pt(s.end),
                    cp1=reflect_pt(s.cp1) if s.cp1 else None,
                    cp2=reflect_pt(s.cp2) if s.cp2 else None,
                    seam_allowance_mm=s.seam_allowance_mm,
                    edge_role=s.edge_role,
                )
            )
        new_internals.append(
            InternalLine(
                id=f"{il.id}_unfold_mirrored",
                label=il.label,
                line_type=il.line_type,
                contour=Contour(segments=refl_segs, is_closed=il.contour.is_closed),
            )
        )

    new_notches = list(piece.notches)
    for notch in piece.notches:
        refl_pos = reflect_pt(notch.position)
        refl_dir = reflect_pt(notch.direction) if notch.direction else None
        new_notches.append(
            Notch(
                id=f"{notch.id}_unfold_mirrored",
                position=refl_pos,
                direction=refl_dir,
                length_mm=notch.length_mm,
                notch_type=notch.notch_type,
                label=notch.label,
            )
        )

    min_x, min_y, max_x, max_y = get_contour_bounds(new_outer_contour, steps_per_curve=48)
    dx0, dy0 = -min_x, -min_y
    new_w_mm = max_x - min_x
    new_h_mm = max_y - min_y

    def norm_pt(p: Optional[Point2D]) -> Optional[Point2D]:
        return Point2D(x=p.x + dx0, y=p.y + dy0) if p else None

    norm_outer = transform_contour(new_outer_contour, sx=1.0, sy=1.0, dx=dx0, dy=dy0)
    norm_internals = [
        InternalLine(
            id=il.id,
            label=il.label,
            line_type=il.line_type,
            contour=transform_contour(il.contour, sx=1.0, sy=1.0, dx=dx0, dy=dy0),
        )
        for il in new_internals
    ]
    norm_notches = [
        Notch(
            id=n.id,
            position=norm_pt(n.position),  # type: ignore[arg-type]
            direction=n.direction,
            length_mm=n.length_mm,
            notch_type=n.notch_type,
            label=n.label,
        )
        for n in new_notches
    ]

    return piece.model_copy(
        update={
            "name": f"{piece.name} (Unfolded)",
            "target_width_cm": round(new_w_mm / MM_PER_CM, 2),
            "target_height_cm": round(new_h_mm / MM_PER_CM, 2),
            "outer_contour": norm_outer,
            "cut_contour": None,
            "internal_lines": norm_internals,
            "notches": norm_notches,
            "grainline_start": norm_pt(piece.grainline_start),
            "grainline_end": norm_pt(piece.grainline_end),
        }
    )


def contour_to_shapely_polygon(contour: Contour, steps_per_curve: int = 32) -> Polygon:
    """Konwertuje zamknięty Contour na obiekt Shapely Polygon (do walidacji pola i kolizji)."""
    pts = contour_to_points(contour, steps_per_curve=steps_per_curve)
    if len(pts) < 3:
        raise ValueError(f"Wielokąt wymaga co najmniej 3 punktów, otrzymano {len(pts)}.")
    return Polygon(pts)


def contour_to_shapely_linestring(contour: Contour, steps_per_curve: int = 32) -> LineString:
    """Konwertuje otwarty lub zamknięty Contour na obiekt Shapely LineString."""
    pts = contour_to_points(contour, steps_per_curve=steps_per_curve)
    if contour.is_closed and pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return LineString(pts)
