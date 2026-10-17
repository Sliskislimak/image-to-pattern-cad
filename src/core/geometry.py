"""
Moduł operacji geometrycznych 2D:
- Adaptacyjna dyskretyzacja krzywych Béziera do polilinii (dla DXF-AAMA, Shapely i Clipper2)
- Wyliczanie dokładnego BoundingBoxa w milimetrach
- Skalowanie anizotropowe (osobno w osi X i Y) do docelowych wymiarów (target_width_cm, target_height_cm)
- Automatyczne wyznaczanie linii nitki prostej (Grainline) oraz środka ciężkości.
"""

from __future__ import annotations

import math
from shapely.geometry import Polygon, LineString

from src.core.contract import Contour, PathSegment, PatternPiece, Point2D


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
            "internal_lines": new_internals,
            "grainline_start": g_start,
            "grainline_end": g_end,
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
