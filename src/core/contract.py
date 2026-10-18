"""
Klocek 0: Wspólny Kontrakt Danych (PatternProject JSON Schema).

Ten moduł definiuje ustandaryzowane struktury danych, którymi wymieniają się
wszystkie klocki aplikacji:
- Ekstraktor AI / Wektoryzator obrazu
- Interaktywny edytor wektorowy (Paper.js / React)
- Silnik geometrii i szwów (Shapely / Clipper2)
- Eksporter do Clo3D (DXF-AAMA oraz Wektorowy PDF 1:1)
- Silnik Nestingu i Kafelkowania na strony A4

Wewnętrzny układ współrzędnych CAD operuje w milimetrach (mm), gdzie:
1 cm = 10.0 mm
1 cal (inch) = 25.4 mm = 72.0 punktów PDF (pt)
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, Field


MM_PER_CM: float = 10.0
PT_PER_MM: float = 72.0 / 25.4  # ~2.834645669291339 pt/mm
PT_PER_CM: float = 72.0 / 2.54  # ~28.346456692913385 pt/cm


class Point2D(BaseModel):
    """Punkt w przestrzeni 2D wyrażony w milimetrach (mm)."""

    x: float
    y: float

    def to_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def scaled(self, sx: float, sy: float) -> Point2D:
        return Point2D(x=self.x * sx, y=self.y * sy)

    def translated(self, dx: float, dy: float) -> Point2D:
        return Point2D(x=self.x + dx, y=self.y + dy)


EdgeRole = Literal["seam", "fold", "cut", "hem", "elastic", "raw"]

# Stałe typów linii krawieckich i oznaczeń
LINE_TYPE_CUT: str = "cut"
LINE_TYPE_FOLD: str = "fold"
LINE_TYPE_SEAM: str = "seam"
LINE_TYPE_SLIT: str = "slit"
LINE_TYPE_INTERNAL_CUT: str = "internal_cut"
LINE_TYPE_DART: str = "dart"


class Notch(BaseModel):
    """
    Karbik / nacięcie montażowe na obrysie elementu wykroju (AAMA Layer 2).
    Wskazuje punkt i opcjonalny wektor kierunku nacięcia na materiale w celu spasowania elementów.
    """

    id: str
    position: Point2D
    direction: Optional[Point2D] = None
    length_mm: float = Field(default=5.0, ge=1.0, description="Długość nacięcia w mm")
    notch_type: Literal["slit", "v_notch", "t_notch"] = "slit"
    label: str = ""

    def translated(self, dx: float, dy: float) -> Notch:
        return self.model_copy(update={"position": self.position.translated(dx, dy)})


class PathSegment(BaseModel):
    """
    Pojedynczy segment ścieżki geometrycznej:
    - 'line': odcinek prosty od `start` do `end`
    - 'cubic_bezier': krzywa Béziera 3. stopnia od `start` do `end`
      z punktami kontrolnymi `cp1` oraz `cp2`.
    - `seam_allowance_mm`: indywidualny naddatek na szew w mm (jeśli None, używany naddatek elementu).
    - `edge_role`: rola krawędzi ('seam', 'fold', 'hem', 'elastic', 'raw').
    """

    type: Literal["line", "cubic_bezier"] = "line"
    start: Point2D
    end: Point2D
    cp1: Optional[Point2D] = None
    cp2: Optional[Point2D] = None
    seam_allowance_mm: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Indywidualny naddatek na szew w mm. Jeśli None, pobiera domyślną wartość z PatternPiece.seam_allowance_mm.",
    )
    edge_role: EdgeRole = Field(
        default="seam",
        description="Rola technologiczna krawędzi: 'seam', 'fold', 'hem', 'elastic', 'raw'.",
    )

    @classmethod
    def line(
        cls,
        start: tuple[float, float] | Point2D,
        end: tuple[float, float] | Point2D,
        seam_allowance_mm: Optional[float] = None,
        edge_role: EdgeRole = "seam",
    ) -> PathSegment:
        s = start if isinstance(start, Point2D) else Point2D(x=start[0], y=start[1])
        e = end if isinstance(end, Point2D) else Point2D(x=end[0], y=end[1])
        return cls(
            type="line",
            start=s,
            end=e,
            seam_allowance_mm=seam_allowance_mm,
            edge_role=edge_role,
        )

    @classmethod
    def bezier(
        cls,
        start: tuple[float, float] | Point2D,
        cp1: tuple[float, float] | Point2D,
        cp2: tuple[float, float] | Point2D,
        end: tuple[float, float] | Point2D,
        seam_allowance_mm: Optional[float] = None,
        edge_role: EdgeRole = "seam",
    ) -> PathSegment:
        s = start if isinstance(start, Point2D) else Point2D(x=start[0], y=start[1])
        c1 = cp1 if isinstance(cp1, Point2D) else Point2D(x=cp1[0], y=cp1[1])
        c2 = cp2 if isinstance(cp2, Point2D) else Point2D(x=cp2[0], y=cp2[1])
        e = end if isinstance(end, Point2D) else Point2D(x=end[0], y=end[1])
        return cls(
            type="cubic_bezier",
            start=s,
            cp1=c1,
            cp2=c2,
            end=e,
            seam_allowance_mm=seam_allowance_mm,
            edge_role=edge_role,
        )


class Contour(BaseModel):
    """Ciągła ścieżka składająca się z odcinków prostych i krzywych Béziera."""

    segments: list[PathSegment] = Field(default_factory=list)
    is_closed: bool = True

    @classmethod
    def from_polyline(cls, points: list[tuple[float, float]], is_closed: bool = True) -> Contour:
        """Tworzy kontur złożony z odcinków prostych łączących kolejne punkty."""
        if len(points) < 2:
            raise ValueError("Kontur wymaga co najmniej 2 punktów.")
        segs: list[PathSegment] = []
        for i in range(len(points) - 1):
            segs.append(PathSegment.line(points[i], points[i + 1]))
        if is_closed and points[0] != points[-1]:
            segs.append(PathSegment.line(points[-1], points[0]))
        return cls(segments=segs, is_closed=is_closed)


InternalLineType = Literal[
    "internal", "dart", "slit", "internal_cut", "fold", "cut_guide", "elastic"
]


class InternalLine(BaseModel):
    """Linia wewnętrzna na elemencie wykroju (np. zaszywka, pęknięcie/rozcięcie, linia zgięcia)."""

    id: str
    label: str = ""
    line_type: InternalLineType = "internal"
    contour: Contour


class PatternPiece(BaseModel):
    """Pojedynczy element wykroju krawieckiego (np. 'Fuelle superior', 'Visera')."""

    id: str
    name: str
    quantity: int = Field(default=1, ge=1, description="Liczba sztuk do wycięcia (np. x1, x2)")
    target_width_cm: float = Field(gt=0, description="Docelowa szerokość elementu w cm")
    target_height_cm: float = Field(gt=0, description="Docelowa wysokość elementu w cm")
    outer_contour: Contour  # Net sewing line
    cut_contour: Optional[Contour] = Field(
        default=None,
        description="Zewnętrzny obrys cięcia z uwzględnieniem naddatków na szwy (Cut Line).",
    )
    internal_lines: list[InternalLine] = Field(default_factory=list)
    notches: list[Notch] = Field(
        default_factory=list,
        description="Karbiki montażowe na obrysie (AAMA Layer 2).",
    )
    grainline_start: Optional[Point2D] = None
    grainline_end: Optional[Point2D] = None
    seam_allowance_mm: float = Field(default=0.0, ge=0.0)
    fabric_note: str = ""

    @property
    def has_cut_contour(self) -> bool:
        return self.cut_contour is not None and len(self.cut_contour.segments) > 0

    @property
    def effective_boundary(self) -> Contour:
        """Zwraca cut_contour jeśli istnieje, w przeciwnym razie outer_contour."""
        return self.cut_contour if self.has_cut_contour else self.outer_contour

    @property
    def target_width_mm(self) -> float:
        return self.target_width_cm * MM_PER_CM

    @property
    def target_height_mm(self) -> float:
        return self.target_height_cm * MM_PER_CM

    @property
    def fold_segment_index(self) -> Optional[int]:
        """Zwraca indeks segmentu outer_contour z rolą 'fold', jeśli istnieje."""
        for idx, seg in enumerate(self.outer_contour.segments):
            if seg.edge_role == "fold":
                return idx
        return None

    @property
    def has_fold(self) -> bool:
        return self.fold_segment_index is not None


class PatternProject(BaseModel):
    """Główny dokument projektu wykroju krawieckiego (PatternProject.json)."""

    project_name: str
    source_image: str = ""
    units: Literal["mm"] = "mm"
    has_seam_allowance: bool = False
    pieces: list[PatternPiece] = Field(default_factory=list)

    def save_json(self, file_path: str | Path) -> Path:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load_json(cls, file_path: str | Path) -> PatternProject:
        path = Path(file_path)
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
