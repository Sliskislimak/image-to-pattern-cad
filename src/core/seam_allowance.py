"""
Silnik naddatków na szwy (Seam Allowance Engine) — Etap 2:
- Jednolity naddatek na szew (Uniform Seam Allowance) z użyciem PyclipperOffset (JT_MITER, MiterLimit=2.0)
- Zróżnicowany naddatek na szew (Variable Per-Edge Seam Allowance) z wektorami normalnymi,
  przecięciem mitrowym Cramera, fazowaniem bevel i czyszczeniem topologicznym CT_UNION.
- Obsługa ról technologicznych krawędzi: 'seam', 'fold' (zgięcie 0mm), 'elastic' (ściągacz 15mm), 'hem', 'raw'.
- Walidacja prostych, poprawnych wielokątów z biblioteką Shapely (Polygon.is_valid).
"""

from __future__ import annotations

import math
from typing import Optional
import pyclipper
from shapely.geometry import MultiPolygon, Polygon
from shapely.validation import make_valid

from src.core.contract import Contour, PathSegment, PatternPiece, PatternProject
from src.core.geometry import contour_to_points, flatten_segment


CLIPPER_SCALE: float = 1000.0  # 1 mm = 1000 jednostek całkowitych Clipper (precyzja 1 µm)


def resolve_effective_allowance(seg: PathSegment, default_seam_mm: float) -> float:
    """Wyznacza efektywny naddatek na szew dla segmentu na podstawie nadpisania lub roli."""
    if seg.seam_allowance_mm is not None:
        return float(seg.seam_allowance_mm)
    role = seg.edge_role
    if role in ("fold", "raw"):
        return 0.0
    if role in ("elastic", "hem"):
        return 15.0
    return default_seam_mm


def apply_uniform_seam_allowance(
    piece: PatternPiece,
    seam_mm: float,
    miter_limit: float = 2.0,
) -> PatternPiece:
    """
    Generuje obrys cięcia (cut_contour) o jednolitym naddatku na szew seam_mm.
    Wykorzystuje PyclipperOffset z JT_MITER oraz MiterLimit=2.0.
    """
    if seam_mm < 0.0:
        raise ValueError(f"Naddatek na szew nie może być ujemny, otrzymano {seam_mm} mm.")

    if seam_mm == 0.0:
        pts = contour_to_points(piece.outer_contour, steps_per_curve=48)
        cut_contour = Contour.from_polyline(pts, is_closed=True)
        return piece.model_copy(
            update={
                "cut_contour": cut_contour,
                "seam_allowance_mm": 0.0,
            }
        )

    pts = contour_to_points(piece.outer_contour, steps_per_curve=48)
    if len(pts) < 3:
        raise ValueError(f"Kontur elementu '{piece.name}' ma za mało punktów ({len(pts)}).")

    scaled_pts = [
        (int(round(x * CLIPPER_SCALE)), int(round(y * CLIPPER_SCALE)))
        for x, y in pts
    ]
    scaled_pts = pyclipper.CleanPolygon(scaled_pts)

    pco = pyclipper.PyclipperOffset(miter_limit=miter_limit)
    pco.AddPath(scaled_pts, pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
    delta = int(round(seam_mm * CLIPPER_SCALE))
    solution = pco.Execute(delta)

    if not solution:
        raise ValueError(f"PyclipperOffset zwrócił pusty wynik dla elementu '{piece.name}'.")

    outer_path = max(solution, key=lambda p: abs(pyclipper.Area(p)))
    cut_pts = [(x / CLIPPER_SCALE, y / CLIPPER_SCALE) for x, y in outer_path]

    poly = Polygon(cut_pts)
    if not poly.is_valid:
        poly = make_valid(poly)
        if isinstance(poly, MultiPolygon):
            poly = max(poly.geoms, key=lambda g: g.area)
        cut_pts = list(poly.exterior.coords)[:-1]

    cut_contour = Contour.from_polyline(cut_pts, is_closed=True)
    return piece.model_copy(
        update={
            "cut_contour": cut_contour,
            "seam_allowance_mm": seam_mm,
        }
    )


def apply_variable_seam_allowance(
    piece: PatternPiece,
    default_seam_mm: float = 10.0,
    miter_limit: float = 2.0,
) -> PatternPiece:
    """
    Generuje obrys cięcia (cut_contour) o zróżnicowanym naddatku na szew per segment:
    - Przesunięcie wzdłuż wektora normalnego zewnętrznego dla każdego segmentu.
    - Przecięcie mitrowe wyliczane wzorami Cramera na łączeniach krawędzi.
    - Ścięcie narożnika (bevel / chams), gdy odległość przekracza miter_limit * max(d_prev, d_curr).
    - Czyszczenie samoprzecięć (self-intersections) operatorem CT_UNION w Pyclipper.
    - Zachowanie krawędzi zgięcia (fold 0 mm) idealnie zlicowanych z linią bazową.
    """
    segs = piece.outer_contour.segments
    allowances = [resolve_effective_allowance(s, default_seam_mm) for s in segs]

    # Jeśli wszystkie krawędzie mają identyczny naddatek, delegujemy do szybkiego PyclipperOffset
    if len(set(allowances)) == 1:
        return apply_uniform_seam_allowance(piece, seam_mm=allowances[0], miter_limit=miter_limit)

    edges: list[dict] = []
    steps_per_curve = 32
    for k, seg in enumerate(segs):
        pts = flatten_segment(seg, steps_per_curve=steps_per_curve)
        d = allowances[k]
        next_start = seg.end
        pts_with_end = pts + [next_start]
        for j in range(len(pts)):
            v_curr = (pts_with_end[j].x, pts_with_end[j].y)
            v_next = (pts_with_end[j + 1].x, pts_with_end[j + 1].y)
            dx = v_next[0] - v_curr[0]
            dy = v_next[1] - v_curr[1]
            L = math.hypot(dx, dy)
            if L < 1e-6:
                continue
            edges.append({
                "start": v_curr,
                "end": v_next,
                "dx": dx,
                "dy": dy,
                "L": L,
                "tx": dx / L,
                "ty": dy / L,
                "d": d,
                "role": seg.edge_role,
                "seg_idx": k,
            })

    N = len(edges)
    if N < 3:
        raise ValueError(f"Element '{piece.name}' ma za mało krawędzi ({N}).")

    scaled_V = [
        (int(round(e["start"][0] * CLIPPER_SCALE)), int(round(e["start"][1] * CLIPPER_SCALE)))
        for e in edges
    ]
    is_ccw = pyclipper.Orientation(scaled_V)

    for e in edges:
        tx, ty = e["tx"], e["ty"]
        if is_ccw:
            nx, ny = ty, -tx
        else:
            nx, ny = -ty, tx
        e["nx"] = nx
        e["ny"] = ny
        d = e["d"]
        e["A"] = (e["start"][0] + d * nx, e["start"][1] + d * ny)
        e["B"] = (e["end"][0] + d * nx, e["end"][1] + d * ny)

    out_points: list[tuple[float, float]] = []
    for i in range(N):
        e_prev = edges[(i - 1) % N]
        e_curr = edges[i]
        v_corner = e_curr["start"]

        t1x, t1y = e_prev["tx"], e_prev["ty"]
        t2x, t2y = e_curr["tx"], e_curr["ty"]
        det = t1x * t2y - t1y * t2x

        B_prev = e_prev["B"]
        A_curr = e_curr["A"]
        d_max = max(e_prev["d"], e_curr["d"])

        if abs(det) < 1e-4:
            if abs(e_prev["d"] - e_curr["d"]) < 1e-4:
                out_points.append(A_curr)
            else:
                out_points.append(B_prev)
                out_points.append(A_curr)
        else:
            delta_x = A_curr[0] - B_prev[0]
            delta_y = A_curr[1] - B_prev[1]
            lam = (delta_x * t2y - delta_y * t2x) / det
            p_miter = (B_prev[0] + lam * t1x, B_prev[1] + lam * t1y)
            dist = math.hypot(p_miter[0] - v_corner[0], p_miter[1] - v_corner[1])

            if d_max > 0.0 and dist > miter_limit * d_max:
                # Ścięcie narożnika (bevel clipping)
                out_points.append(B_prev)
                out_points.append(A_curr)
            else:
                out_points.append(p_miter)

    scaled_out = [
        (int(round(x * CLIPPER_SCALE)), int(round(y * CLIPPER_SCALE)))
        for x, y in out_points
    ]
    scaled_out = pyclipper.CleanPolygon(scaled_out)
    pc = pyclipper.Pyclipper()
    pc.AddPath(scaled_out, pyclipper.PT_SUBJECT, True)
    sol = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    if not sol:
        sol = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD)
    if not sol:
        raise ValueError(f"Clipper union zwrócił pusty wynik dla elementu '{piece.name}'.")

    outer = max(sol, key=lambda path: abs(pyclipper.Area(path)))
    final_pts = [(x / CLIPPER_SCALE, y / CLIPPER_SCALE) for x, y in outer]

    # Precyzyjne zlicowanie krawędzi zgięcia (0 mm / fold)
    for e in edges:
        if e["d"] == 0.0 or e["role"] == "fold":
            p1 = e["start"]
            nx, ny = e["nx"], e["ny"]
            clamped: list[tuple[float, float]] = []
            for pt in final_pts:
                dot = (pt[0] - p1[0]) * nx + (pt[1] - p1[1]) * ny
                if 1e-5 < dot < 0.2:
                    clamped.append((pt[0] - dot * nx, pt[1] - dot * ny))
                else:
                    clamped.append(pt)
            final_pts = clamped

    # Walidacja wielokąta z biblioteką Shapely
    poly = Polygon(final_pts)
    if not poly.is_valid:
        poly = make_valid(poly)
        if isinstance(poly, MultiPolygon):
            poly = max(poly.geoms, key=lambda g: g.area)
        final_pts = list(poly.exterior.coords)[:-1]

    cut_contour = Contour.from_polyline(final_pts, is_closed=True)
    return piece.model_copy(
        update={
            "cut_contour": cut_contour,
            "seam_allowance_mm": default_seam_mm,
        }
    )


def apply_seam_allowance_to_piece(
    piece: PatternPiece,
    global_seam_allowance_mm: Optional[float] = None,
    miter_limit: float = 2.0,
) -> PatternPiece:
    """
    Aplikuje naddatek na szew do elementu wykroju.
    Automatycznie wybiera algorytm jednolity lub zróżnicowany (per-edge).
    """
    effective_seam_mm = (
        global_seam_allowance_mm
        if global_seam_allowance_mm is not None
        else (piece.seam_allowance_mm if piece.seam_allowance_mm > 0.0 else 10.0)
    )

    has_custom = any(
        seg.seam_allowance_mm is not None or seg.edge_role != "seam"
        for seg in piece.outer_contour.segments
    )

    if has_custom:
        return apply_variable_seam_allowance(
            piece,
            default_seam_mm=effective_seam_mm,
            miter_limit=miter_limit,
        )
    else:
        return apply_uniform_seam_allowance(
            piece,
            seam_mm=effective_seam_mm,
            miter_limit=miter_limit,
        )


def apply_seam_allowance_to_project(
    project: PatternProject,
    global_seam_allowance_mm: Optional[float] = None,
    miter_limit: float = 2.0,
) -> PatternProject:
    """
    Aplikuje naddatki na szwy do wszystkich elementów projektu.
    Zwraca nowy PatternProject z flagą has_seam_allowance = True.
    """
    updated_pieces = [
        apply_seam_allowance_to_piece(
            piece,
            global_seam_allowance_mm=global_seam_allowance_mm,
            miter_limit=miter_limit,
        )
        for piece in project.pieces
    ]
    return project.model_copy(
        update={
            "pieces": updated_pieces,
            "has_seam_allowance": True,
        }
    )
