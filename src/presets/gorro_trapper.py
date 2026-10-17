"""
Model referencyjny wykroju "Gorro Trapper con Tapabocas" (z przesłanej grafiki).

Odwzorowuje wszystkie 6 elementów konstrukcyjnych z górnej części arkusza
wraz z ich dokładnymi wymiarami w centymetrach/milimetrach, wcięciami narożnymi,
mnożnikami (x1, x2) oraz liniami wewnętrznymi:
1. Fuelle superior (13 cm x 35 cm, x1)
2. Frente (12,5 cm x 11 cm, x1)
3. Visera (20 cm x 11 cm, x2)
4. Cubre boca (28 cm x 14 cm, x1)
5. Lateral (10 cm x 20 cm, x2)
6. Orejera Compuesta (25 cm x 19,5 cm, x2)
"""

from __future__ import annotations

from src.core.contract import (
    Contour,
    InternalLine,
    PathSegment,
    PatternPiece,
    PatternProject,
    Point2D,
)
from src.core.geometry import calibrate_piece_anisotropic


def build_fuelle_superior() -> PatternPiece:
    """
    1. Fuelle superior (x1):
    - Wymiary: 13 cm (130 mm) szerokości x 35 cm (350 mm) wysokości.
    - Góra płaska (130 mm), boki proste pionowe, dół zaokrąglony z wcięciem 2,5 cm (25 mm) na rogach.
    """
    segments = [
        # Lewy bok (od dołu do góry)
        PathSegment.line((0.0, 25.0), (0.0, 350.0)),
        # Górna krawędź (13 cm)
        PathSegment.line((0.0, 350.0), (130.0, 350.0)),
        # Prawy bok (od góry do wcięcia 2,5 cm od dołu)
        PathSegment.line((130.0, 350.0), (130.0, 25.0)),
        # Dolny łuk (prawa połowa -> środek dołu (65, 0))
        PathSegment.bezier((130.0, 25.0), (110.0, 8.0), (88.0, 0.0), (65.0, 0.0)),
        # Dolny łuk (środek dołu (65, 0) -> lewy dolny punkt (0, 25))
        PathSegment.bezier((65.0, 0.0), (42.0, 0.0), (20.0, 8.0), (0.0, 25.0)),
    ]
    piece = PatternPiece(
        id="fuelle_superior",
        name="Fuelle superior",
        quantity=1,
        target_width_cm=13.0,
        target_height_cm=35.0,
        outer_contour=Contour(segments=segments, is_closed=True),
        fabric_note="En tela exterior y forro (x1)",
    )
    return calibrate_piece_anisotropic(piece)


def build_frente() -> PatternPiece:
    """
    2. Frente (x1):
    - Wymiary: 12,5 cm (125 mm) szerokości x 11 cm (110 mm) wysokości.
    - Dół i boki proste, górna krawędź wklęsła o 2 cm (20 mm) w środku (y = 90 mm).
    """
    segments = [
        # Dolna krawędź
        PathSegment.line((0.0, 0.0), (125.0, 0.0)),
        # Prawa krawędź
        PathSegment.line((125.0, 0.0), (125.0, 110.0)),
        # Górny wklęsły łuk (od prawej do środka y=90)
        PathSegment.bezier((125.0, 110.0), (102.0, 96.0), (82.0, 90.0), (62.5, 90.0)),
        # Górny wklęsły łuk (od środka y=90 do lewej góry)
        PathSegment.bezier((62.5, 90.0), (43.0, 90.0), (23.0, 96.0), (0.0, 110.0)),
        # Lewa krawędź
        PathSegment.line((0.0, 110.0), (0.0, 0.0)),
    ]
    piece = PatternPiece(
        id="frente",
        name="Frente",
        quantity=1,
        target_width_cm=12.5,
        target_height_cm=11.0,
        outer_contour=Contour(segments=segments, is_closed=True),
        fabric_note="En tela exterior y forro (x1)",
    )
    return calibrate_piece_anisotropic(piece)


def build_visera() -> PatternPiece:
    """
    3. Visera (x2 - daszek):
    - Wymiary: 20 cm (200 mm) szerokości x 11 cm (110 mm) wysokości.
    - Góra lekko wklęsła, dolne rogi mocno zaokrąglone (wcięcie 3 cm = 30 mm).
    """
    segments = [
        # Lewy bok (od zaokrąglenia 30 mm w górę)
        PathSegment.line((0.0, 35.0), (0.0, 110.0)),
        # Górna wklęsła krawędź (lewa -> środek -> prawa)
        PathSegment.bezier((0.0, 110.0), (35.0, 100.0), (68.0, 96.0), (100.0, 96.0)),
        PathSegment.bezier((100.0, 96.0), (132.0, 96.0), (165.0, 100.0), (200.0, 110.0)),
        # Prawy bok
        PathSegment.line((200.0, 110.0), (200.0, 35.0)),
        # Dolny łuk daszka z wcięciem narożnym 3 cm
        PathSegment.bezier((200.0, 35.0), (182.0, 8.0), (145.0, 0.0), (100.0, 0.0)),
        PathSegment.bezier((100.0, 0.0), (55.0, 0.0), (18.0, 8.0), (0.0, 35.0)),
    ]
    piece = PatternPiece(
        id="visera",
        name="Visera",
        quantity=2,
        target_width_cm=20.0,
        target_height_cm=11.0,
        outer_contour=Contour(segments=segments, is_closed=True),
        fabric_note="En tela exterior (x2)",
    )
    return calibrate_piece_anisotropic(piece)


def build_cubre_boca() -> PatternPiece:
    """
    4. Cubre boca (x1 - osłona ust):
    - Wymiary: 28 cm (280 mm) szerokości x 14 cm (140 mm) wysokości.
    - Wszystkie 4 narożniki mają wcięcie 3 cm (30 mm) w pionie, tworząc kształt beczkowaty.
    """
    segments = [
        # Lewa pionowa krawędź (od y=30 do y=110, długość 8 cm)
        PathSegment.line((0.0, 30.0), (0.0, 110.0)),
        # Górny łuk ("elastizar") z y=110 do szczytu y=140 w środku (x=140) i z powrotem do y=110
        PathSegment.bezier((0.0, 110.0), (45.0, 132.0), (92.0, 140.0), (140.0, 140.0)),
        PathSegment.bezier((140.0, 140.0), (188.0, 140.0), (235.0, 132.0), (280.0, 110.0)),
        # Prawa pionowa krawędź (od y=110 do y=30)
        PathSegment.line((280.0, 110.0), (280.0, 30.0)),
        # Dolny łuk ("elastizar") z y=30 przez dół y=0 w środku (x=140) do (0, 30)
        PathSegment.bezier((280.0, 30.0), (235.0, 8.0), (188.0, 0.0), (140.0, 0.0)),
        PathSegment.bezier((140.0, 0.0), (92.0, 0.0), (45.0, 8.0), (0.0, 30.0)),
    ]
    piece = PatternPiece(
        id="cubre_boca",
        name="Cubre boca",
        quantity=1,
        target_width_cm=28.0,
        target_height_cm=14.0,
        outer_contour=Contour(segments=segments, is_closed=True),
        grainline_start=Point2D(x=60.0, y=70.0),
        grainline_end=Point2D(x=220.0, y=70.0),
        fabric_note="En tela exterior - elastizar arriba y abajo (x1)",
    )
    return calibrate_piece_anisotropic(piece)


def build_lateral() -> PatternPiece:
    """
    5. Lateral (x2 - bok główki czapki):
    - Wymiary: 10 cm (100 mm) szerokości x 20 cm (200 mm) wysokości.
    - Lewa krawędź prosta (20 cm), prawa profilowana (wcięcia 3 cm, 6 cm na górze; 1,5 cm w środku; 3,5 i 5 cm na dole).
    """
    segments = [
        # Lewa prosta pionowa krawędź (20 cm)
        PathSegment.line((0.0, 0.0), (0.0, 200.0)),
        # Górny odcinek i zaokrąglenie prawego górnego rogu (wcięcia 3 cm i 6 cm)
        PathSegment.bezier((0.0, 200.0), (55.0, 200.0), (100.0, 182.0), (100.0, 140.0)),
        # Przejście przez środkowe przewężenie (1,5 cm -> x=85 mm w połowie wysokości)
        PathSegment.bezier((100.0, 140.0), (100.0, 115.0), (90.0, 95.0), (85.0, 75.0)),
        # Dolny łuk zamykający do (0, 0) (wcięcie 3,5 cm / 5 cm)
        PathSegment.bezier((85.0, 75.0), (78.0, 42.0), (45.0, 10.0), (0.0, 0.0)),
    ]
    piece = PatternPiece(
        id="lateral",
        name="Lateral",
        quantity=2,
        target_width_cm=10.0,
        target_height_cm=20.0,
        outer_contour=Contour(segments=segments, is_closed=True),
        fabric_note="En tela exterior y forro (x2)",
    )
    return calibrate_piece_anisotropic(piece)


def build_orejera_compuesta() -> PatternPiece:
    """
    6. Orejera Compuesta (x2 - nausznik złożony):
    - Wymiary: 25 cm (250 mm, podział 16 cm + 9 cm) szerokości x 19,5 cm (195 mm) wysokości.
    - Zawiera linie wewnętrzne cięcia/zaszywki 'V' oraz podział poziomy '8'.
    """
    segments = [
        # Górna prawa krawędź pozioma (9 cm: od x=160 do x=250 na wysokości y=195)
        PathSegment.line((160.0, 195.0), (250.0, 195.0)),
        # Prawa krawędź pionowa (do wcięcia dolnego 2 cm -> y=20)
        PathSegment.line((250.0, 195.0), (250.0, 20.0)),
        # Dolna krawędź łukowa od prawego dołu (250, 20) do zaokrąglenia paska nausznika po lewej
        PathSegment.bezier((250.0, 20.0), (185.0, 22.0), (95.0, 8.0), (25.0, 0.0)),
        # Zaokrąglony czubek nausznika po lewej stronie (szerokość paska 5 cm = 50 mm)
        PathSegment.bezier((25.0, 0.0), (5.0, 0.0), (0.0, 12.0), (0.0, 28.0)),
        PathSegment.bezier((0.0, 28.0), (0.0, 44.0), (12.0, 56.0), (32.0, 64.0)),
        # Wklęsły profil przedni nausznika (według odkładanych odcinków 4 cm, 4,5 cm, 2 cm od przekątnej)
        PathSegment.bezier((32.0, 64.0), (78.0, 82.0), (112.0, 102.0), (132.0, 135.0)),
        PathSegment.bezier((132.0, 135.0), (145.0, 156.0), (154.0, 175.0), (160.0, 195.0)),
    ]

    # Linie wewnętrzne z rysunku konstrukcyjnego (czerwone linie podziału A / V)
    internal_lines = [
        InternalLine(
            id="orejera_horizontal_8",
            label="Line 8cm",
            line_type="cut_guide",
            contour=Contour.from_polyline([(132.0, 115.0), (250.0, 115.0)], is_closed=False),
        ),
        InternalLine(
            id="orejera_dart_v_lower",
            label="V-cut 1",
            line_type="dart",
            contour=Contour.from_polyline([(132.0, 115.0), (218.0, 20.0)], is_closed=False),
        ),
        InternalLine(
            id="orejera_dart_v_upper",
            label="V-cut 2 (3cm)",
            line_type="dart",
            contour=Contour.from_polyline([(145.0, 148.0), (248.0, 20.0)], is_closed=False),
        ),
    ]

    piece = PatternPiece(
        id="orejera_compuesta",
        name="Orejera Compuesta",
        quantity=2,
        target_width_cm=25.0,
        target_height_cm=19.5,
        outer_contour=Contour(segments=segments, is_closed=True),
        internal_lines=internal_lines,
        grainline_start=Point2D(x=195.0, y=45.0),
        grainline_end=Point2D(x=195.0, y=175.0),
        fabric_note="En tela exterior y forro (x2)",
    )
    return calibrate_piece_anisotropic(piece)


def create_gorro_trapper_project() -> PatternProject:
    """Tworzy kompletny projekt PatternProject dla czapki 'Gorro Trapper con Tapabocas'."""
    pieces = [
        build_fuelle_superior(),
        build_frente(),
        build_visera(),
        build_cubre_boca(),
        build_lateral(),
        build_orejera_compuesta(),
    ]
    return PatternProject(
        project_name="Gorro Trapper con Tapabocas",
        source_image="samples/gorro_trapper.png",
        units="mm",
        has_seam_allowance=False,
        pieces=pieces,
    )
