# Image-to-Pattern CAD: Raster-to-CAD Pattern Digitizer & Nester

System konwersji rastrowych grafik wykrojów krawieckich z internetu (JPG / PNG) do produkcyjnych plików wektorowych w skali 1:1 (**DXF-AAMA** oraz **Wektorowy PDF**) dla programu **Clo3D**, z funkcją automatycznego upakowania (**2D Nesting**) i kafelkowania (**Tiling**) na arkusze **A4** do druku domowego.

---

## 🎯 Cel Projektu

Wykroje krawieckie publikowane w internecie (np. na blogach, w formatach JPG/PNG) nie nadają się do bezpośredniego załadowania do profesjonalnych programów 3D takich jak **Clo3D**, ponieważ:
1. **Clo3D wymaga plików wektorowych**: akceptuje wyłącznie pliki **DXF** (w standardzie odzieżowym **AAMA/ASTM**) lub wektorowe pliki **Adobe PDF**.
2. **Brak skali globalnej (Pułapka rysunków z internetu)**: Autorzy grafik często rysują elementy poglądowo w różnej skali pikseli (np. na referencyjnym wykroju czapki *Cubre boca* opisane jako 28 cm jest w pikselach węższe niż *Orejera Compuesta* opisana jako 25 cm).
3. **Wymiary większe niż arkusz A4**: Elementy odzieży (np. pas środkowy czapki o długości 35 cm) nie mieszczą się na pojedynczej kartce A4 ($21{,}0 \times 29{,}7\text{ cm}$) i wymagają inteligentnego podziału ze znacznikami klejenia lub ciasnego upakowania mniejszych części dla oszczędności papieru.
4. **Brak naddatków na szwy (Seam Allowances)**: Rysunki z internetu prezentują wyłącznie linię szycia netto. Do fizycznego skrojenia tkaniny i montażu w szwalni lub w symulatorze 3D niezbędny jest precyzyjny naddatek na szew (np. $10\text{ mm}$ na szew standardowy, $15\text{ mm}$ na ściągacz elastyczny, $0\text{ mm}$ na krawędź zagięcia materiału).

Nasz program rozwiązuje te problemy w **architekturze potokowej (Pipeline / Klocki LEGO)**.

---

## 🧱 ETAP 1: Fundament Architektury & Eksporter do Clo3D

W Etapie 1 powstał w pełni funkcjonalny, przetestowany silnik CAD generujący gotowe pliki produkcyjne w skali 1:1 dla referencyjnego wykroju czapki zimowej **"Gorro Trapper con Tapabocas"**.

### Co dokładnie zostało wprowadzone i dlaczego?

#### 1. Klocek 0: Wspólny Kontrakt Danych (`src/core/contract.py`)
- **Po co:** Aby program był składany z niezależnych klocków, wszystkie moduły (AI, edytor UI, silnik geometrii, eksportery) muszą posługiwać się jednym, niezmiennym językiem.
- **Implementacja:** Zdefiniowano kontrakt [`PatternProject`](src/core/contract.py) oparty na **Pydantic V2**. Reprezentuje on:
  - Punkty 2D (`Point2D`) w milimetrach,
  - Segmenty ścieżek (`PathSegment`) obsługujące zarówno odcinki proste (`line`), jak i gładkie krzywe Béziera 3. stopnia (`cubic_bezier`),
  - Linie wewnętrzne (`InternalLine`) dla zaszywek i linii cięcia,
  - Oś nitki prostej (`grainline`) oraz mnożniki wykroju (`quantity` np. x1, x2).
- **Format zapisu:** Kompletny projekt zapisuje się do pliku JSON ([`gorro_trapper_project.json`](output/stage1_clo3d/gorro_trapper_project.json)), który może być bezpośrednio wczytywany przez frontend w React / Paper.js lub backend w Pythonie.

#### 2. Silnik Kalibracji Anizotropowej (`src/core/geometry.py`)
- **Po co:** Aby zneutralizować brak proporcji na grafikach rastrowych.
- **Implementacja:** Funkcja `calibrate_piece_anisotropic` przelicza Bounding Box każdego elementu i skaluje niezależnie oś $X$ oraz oś $Y$:
  $$S_x = \frac{W_{\text{cel, mm}}}{W_{\text{aktualne, mm}}}, \quad S_y = \frac{H_{\text{cel, mm}}}{H_{\text{aktualne, mm}}}$$
  Dzięki temu krzywe zachowują swój organiczny charakter, ale ich gabaryty zewnętrzne osiągają **dokładność $0{,}00\text{ mm}$**.

#### 3. Model Wzorcowy Czapki Trapper (`src/presets/gorro_trapper.py`)
Precyzyjne, matematyczne odwzorowanie wszystkich 6 części czapki z przesłanej grafiki z zachowaniem wcięć konstrukcyjnych:
1. **Fuelle superior** ($13{,}0 \times 35{,}0\text{ cm}$, `x1`): góra prosta, dolne rogi zaokrąglone z wcięciem $2{,}5\text{ cm}$.
2. **Frente** ($12{,}5 \times 11{,}0\text{ cm}$, `x1`): górna krawędź wklęsła o $2\text{ cm}$ w środku.
3. **Visera** ($20{,}0 \times 11{,}0\text{ cm}$, `x2`): daszek z dolnymi wcięciami narożnymi $3\text{ cm}$ i wklęsłym łukiem czołowym.
4. **Cubre boca** ($28{,}0 \times 14{,}0\text{ cm}$, `x1`): maska ochronna z 4 wcięciami narożnymi $3\text{ cm}$ i łukami ściągaczy ("elastizar").
5. **Lateral** ($10{,}0 \times 20{,}0\text{ cm}$, `x2`): profilowany bok główki z wcięciami $3\text{ cm}$, $6\text{ cm}$, $1{,}5\text{ cm}$, $3{,}5\text{ cm}$ i $5\text{ cm}$.
6. **Orejera Compuesta** ($25{,}0 \times 19{,}5\text{ cm}$, `x2`): złożony nausznik z wewnętrznymi liniami cięcia/zaszywek $V$ i podziału poziomego $8\text{ cm}$.

#### 4. Klocek 1A: Eksporter DXF-AAMA / ASTM (`src/exporters/dxf_exporter.py`)
- **Po co:** Clo3D preferuje format **DXF-AAMA** (odzieżowy standard CAD).
- **Zastosowane standardy:**
  - Wersja **AutoCAD R12 (AC1009)** – wymagana przez krawieckie parsery CAD dla klasycznych polilinii `POLYLINE` 2D.
  - Każdy wykrój jest definiowany jako unikalny `BLOCK` i osadzany na `MODELSPACE`.
  - **Warstwa `1` (BOUNDARY)**: zamknięty obrys cięcia.
  - **Warstwa `4` (INTERNAL)**: linie wewnętrzne i zaszywki.
  - **Warstwa `7` (GRAINLINE)**: oś nitki prostej.
  - **Warstwa `8` (TEXT)**: metadane elementu i ilość sztuk.
  - Generuje zarówno wersję w milimetrach (`_mm.dxf`), centymetrach (`_cm.dxf`), jak i **pełny zestaw do szycia 3D (9 elementów)**, gdzie części `x2` są od razu rozwinięte w symetrycznym odbiciu lustrzanym (Lewa/Prawa strona).

#### 5. Klocek 1B: Eksporter Wektorowego PDF 1:1 (`src/exporters/pdf_exporter.py`)
- **Po co:** Najwygodniejszy sposób importu do Clo3D (`File -> Import -> Adobe PDF`).
- **Cechy kluczowe:**
  - Rysuje natywne krzywe Béziera PostScript w dokładnej skali ($1\text{ mm} = \frac{72}{25{,}4} \approx 2{,}83464567\text{ pt}$).
  - **Brak ramki strony**: Clo3D nie traktuje marginesu strony jako prostokątnego kawałka materiału.
  - Generuje także **Arkusz Techniczny 1:1** z beżowym wypełnieniem, węzłami CAD oraz kwadratem kontrolnym skali $5{,}0 \times 5{,}0\text{ cm}$.

---

## 🧠 NOWY ETAP 2: Semantyczna Dygitalizacja Wykroju (Pattern Semantic Digitizer)

W Nowym Etapie 2 powstał zaawansowany, a zarazem lekki silnik Computer Vision i geometrii analitycznej (`src/digitizer/`), który przekształca surową grafikę rastrową z internetu (JPG / PNG, np. [`samples/gorro_trapper.png`](samples/gorro_trapper.png)) w zweryfikowany model CAD [`PatternProject`](src/core/contract.py) w skali 1:1, ze szczegółowym rozpoznaniem technologii krawieckiej:

```
[Surowa grafika JPG / PNG]
           │
           ▼
[1. Silnik Odzyskiwania Skali (Scale Recovery)] (src/digitizer/scale_recovery.py)
  ├─ Scenariusz A: Wymiary tekstowe W x H (anizotropowe) + parser adnotacji (ES/PL/EN)
  ├─ Scenariusz B: Dwupunktowa kalibracja referencyjna (p1, p2 + długość fizyczna)
  └─ Scenariusz C: Automatyczna detekcja kwadratu kontrolnego 50x50 mm & metadane DPI
           │  Wynik: ScaleResult (mm_per_px_x, mm_per_px_y, is_isotropic, confidence)
           ▼
[2. Segmentacja Rastrowa & Wektoryzator Schneidera] (src/digitizer/vectorizer.py)
  ├─ Filtracja barwna HSV z wykrywaniem dominanty i filtrem flat-fill ratio
  ├─ Detekcja ostrych narożników kątem skrętu (turning angle) i tłumieniem NMS
  ├─ Wykrywanie krawędzi prostoliniowych (PathSegment.line)
  └─ Dopasowanie krzywych sześciennych Béziera metodą najmniejszych kwadratów Schneidera
           │  Wynik: Kontury CAD w mm (orientacja CAD: oś Y w górę, origin w (0,0))
           ▼
[3. Semantyczny Klasyfikator Krawiecki] (src/digitizer/semantic_classifier.py)
  ├─ Tryby szwów: 'net_only' (odsunięcie na zewnątrz), 'included' (do wewnątrz), 'dual_detected'
  ├─ Rozpoznawanie zgięć materiału ('doblez' / 'fold') z konfigurowalnym Auto-Unfold
  ├─ Izolacja wewnętrznych rozcięć i zaszewek V (InternalLine na AAMA Layer 4)
  └─ Karbiki montażowe (Notch na AAMA Layer 2 z wektorami normalnymi)
           │  Wynik: Kompletny model PatternProject z PatternPiece
           ▼
[4. Potok E2E, Diagnostyka Wizualna & Eksportery] (src/digitizer/pipeline.py, run_digitizer.py)
  ├─ Zbiorczy publikacyjny arkusz diagnostyczny (diagnostic_contact_sheet.png)
  ├─ Opcjonalny przełącznik na osobne pliki PNG (--separate-png)
  ├─ Eksporter Clo3D DXF-AAMA AutoCAD R12 (Warstwy 1, 2, 4, 5, 7, 8)
  ├─ Eksporter Clo3D 1:1 Vector PDF
  └─ Kafelkowanie do domowego druku wielostronicowego A4 (Hybrid Smart Pack & Tile)
```

### Moduły i Technologie Nowego Etapu 2:

#### 1. Silnik Odzyskiwania Skali (`src/digitizer/scale_recovery.py`)
- **Scenariusz A (Skalowanie wymiarowe)**: Parser wyrażeń regularnych wyciąga liczby z przecinkami i kropkami (`"12,5 cm"`, `"19.5"`, `"28 cm"`), mnożniki (`"x1"`, `"x2"`) oraz słowa kluczowe (`"doblez"`, `"elastizar"`), a następnie skaluje każdy element anizotropowo ($S_x, S_y$), eliminując dysproporcje rysunków internetowych.
- **Scenariusz B (Kalibracja dwupunktowa)**: Funkcja `calibrate_from_two_points(p1, p2, real_distance_cm)` wylicza współczynnik skali z dokładnością do 4 miejsc po przecinku:
  $$\text{mm\_per\_px} = \frac{L_{\text{cm}} \cdot 10}{\|P_2 - P_1\|_2}$$
- **Scenariusz C (Detekcja kwadratu kontrolnego & DPI)**: `detect_calibration_square` automatycznie odnajduje kwadrat testowy $50 \times 50\text{ mm}$ za pomocą `approxPolyDP` i walidacji kątów prostych oraz czyta metadane rozdzielczości z bloków PNG `pHYs` i JPEG EXIF.

#### 2. Segmentacja Rastrowa & Wektoryzator Schneidera (`src/digitizer/vectorizer.py`)
- **Segmentacja odporna na szum**: Automatycznie lokalizuje dominujący odcień szablonu w przestrzeni barwnej HSV, usuwa napisy wymiarowe i znaki wodne operacjami morfologicznymi oraz stosuje metrykę *flat-fill ratio*, która odróżnia jednolicie wypełniony szablon od zdjęć gotowej odzieży.
- **Detekcja narożników (F07)**: Wyznacza dyskretny kąt skrętu cięciw konturu i z zachowaniem tłumienia niemaksymalnego (NMS) dzieli obrys na łańcuchy gładkie i ostre węzły narożne.
- **Algorytm Philipa J. Schneidera (*Graphics Gems I*, F08)**: Aproksymuje próbkowane punkty minimalną liczbą segmentów sześciennych Béziera:
  - Parametryzacja długością cięciwy (*chord length*),
  - Wyznaczanie wektorów stycznych i rozwiązywanie układu najmniejszych kwadratów,
  - Strojenie parametrów metodą Newtona-Raphsona,
  - Rekurencyjny podział w punkcie maksymalnego błędu z gwarancją ciągłości $G^1 / C^0$ i odchyleniem poniżej $0{,}5\text{ mm}$.
- **Wykrywanie linii prostych (F09)**: Segmenty o odchyleniu mniejszym niż tolerancja kolinearności są redukowane do `PathSegment.line`.
- **Układ współrzędnych**: Zastosowano klasę [`PixelToMMTransform`](src/digitizer/vectorizer.py), która mapuje punkty rastra na układ CAD (oś Y skierowana w górę, lewy dolny róg w $(0,0)$).

#### 3. Semantyczny Klasyfikator Krawiecki (`src/digitizer/semantic_classifier.py`)
- **3 Tryby Naddatków na Szwy**:
  - `net_only`: Obrys traktowany jako linia szycia netto $\to$ dorysowanie zewnętrznej linii cięcia brutto (`cut_contour`).
  - `included`: Obrys traktowany jako linia cięcia brutto $\to$ wyznaczenie wewnętrznej linii szycia netto poprzez odsunięcie do wewnątrz (*inward offset*) za pomocą `pyclipper`.
  - `dual_detected`: Parowanie współśrodkowych konturów cięcia i szycia bezpośrednio z grafiki.
- **Detekcja Zgięć & Auto-Unfold**:
  - `detect_fold_edge`: Wykrywa krawędzie zgięcia z tekstu (*doblez*, *fold*, *al lomo*, *lbf*, *zgięcie*) lub geometrii, przypisując segmentowi rolę `edge_role="fold"` oraz `seam_allowance_mm=0.0`.
  - `apply_auto_unfold`: Gdy `auto_unfold=True`, rozkłada element w odbiciu lustrzanym wzdłuż osi zgięcia do pełnego szablonu 1:1, a pierwotną oś zachowuje jako `InternalLine` na **AAMA Layer 5**.
- **Wewnętrzne Cięcia & Karbiki**:
  - Rozpoznaje zaszewki $V$ oraz linie rozcięć na **AAMA Layer 4**.
  - `detect_notches`: Rzutuje znaczniki na obrys i wyznacza jednostkowy wektor normalnej zewnętrznej $\vec{n} = (-t_y, t_x)$ na **AAMA Layer 2**.

#### 4. Narzędzie CLI & Nakładki Diagnostyczne (`run_digitizer.py`)
Uruchomienie potoku percepcji:
```bash
# Standardowe uruchomienie z domyślnym generowaniem zbiorczego arkusza diagnostycznego
python run_digitizer.py

# Eksport z naddatkiem na szew 10 mm oraz rozłożeniem zgięć do pełnego szablonu 1:1
python run_digitizer.py --auto-unfold --seam-allowance-mm 10.0

# Generowanie zbiorczego arkusza ORAZ osobnych plików PNG dla każdego etapu percepcji
python run_digitizer.py --separate-png
```

Generowane pliki w katalogu `output/stage2_digitizer/`:
1. `diagnostic_contact_sheet.png` — zbiorczy, publikacyjny arkusz 6 paneli w siatce $2 \times 3$ z metadanymi i tabelą.
2. `01_segmentation_masks.png` — maski segmentacji i Bounding Boxy (przy fladze `--separate-png`).
3. `02_corners_detected.png` — wykryte narożniki i punkty węzłowe (przy fladze `--separate-png`).
4. `03_bezier_curves.png` — dopasowane krzywe Béziera z punktami kontrolnymi CP1/CP2 (przy fladze `--separate-png`).
5. `04_tailoring_semantics.png` — linie zgięcia, naddatki, zaszewki i karbiki z wektorami normalnymi (przy fladze `--separate-png`).
6. `05_cad_calibrated_overlay.png` — zwymiarowany arkusz CAD 1:1 z kwadratem kalibracyjnym $50 \times 50\text{ mm}$ (przy fladze `--separate-png`).
7. `gorro_trapper_clo3d_mm.dxf` — przemysłowy plik **DXF-AAMA (AutoCAD R12)** z warstwami 1, 2, 4, 5, 7, 8.
8. `gorro_trapper_clo3d_import_1to1.pdf` — czysty wektorowy PDF 1:1 gotowy do importu w Clo3D.
9. `gorro_trapper_technical_sheet_1to1.pdf` — wektorowy arkusz techniczny ze specyfikacją.
10. `gorro_trapper_a4_print_hybrid.pdf` — wielostronicowy szablon do druku A4 z marginesami klejenia i paserami.
11. `gorro_trapper_digitized_project.json` — zserializowany kontrakt danych Pydantic.

---

## ✂️ ETAP 3: Silnik Zapasów na Szwy, Upakowanie 2D (Nesting) i Kafelkowanie A4 (Tiling)

W Etapie 2 system został rozbudowany o kompletną technologię krawiecką i poligraficzną: konfigurowalne naddatki na szwy (globalne oraz per-krawędź), zaawansowany podwójny silnik upakowania 2D i kafelkowania wielostronicowego A4 oraz pełną obsługę podwójnego konturu (linia cięcia + linia szycia netto) we wszystkich eksporterach CAD.

```
                           +----------------------------------------+
                           |  WZORZEC CAD NETTO (outer_contour)    |
                           +----------------------------------------+
                                               |
                                               v
                     +----------------------------------------------------+
                     |         SILNIK NADDATKÓW NA SZWY (R1)              |
                     |  - Uniform: PyclipperOffset (JT_MITER, Limit 2.0)  |
                     |  - Variable: Wektory normalne, Cramer, CT_UNION    |
                     |  - Role: seam (10mm), fold (0mm), elastic (15mm)   |
                     +----------------------------------------------------+
                                               |
                          +--------------------+--------------------+
                          | (Generuje cut_contour brutto)           |
                          v                                         v
+--------------------------------------------------+   +--------------------------------------------------+
|    PODWÓJNY SILNIK NESTINGU & TILINGU A4 (R2)    |   |     EKSPORTERY CLO3D Z PODWÓJNYM KONTUREM (R3)   |
| - Tryb Hybrydowy (Smart Pack & Tile, 13 stron)   |   | - DXF-AAMA R12: Warstwa 1 (Cut), Warstwa 4 (Sew) |
| - Tryb Plakatowy (Full-Sheet Grid, 12 stron)     |   | - PDF 1:1 Vector: Ciągła czarna + Niebieska dash |
| - Znaczniki: Romby R{r}-C{c}, Linie klejenia     |   | - Arkusz Techniczny 1:1 z adnotacjami naddatków  |
| - Minimapa montażowa, Kwadrat 5x5 cm fill=None   |   | - Pipeline run_stage2.py -> 35 plików wyjściowych|
+--------------------------------------------------+   +--------------------------------------------------+
```

---

### 1. R1: Silnik Zapasów na Szwy (Seam Allowance Engine — `src/core/seam_allowance.py`)

Krawiectwo wymaga bezwzględnego rozróżnienia między **linią szycia netto** (docelowy wymiar gotowego wyrobu po zszyciu) a **linią cięcia brutto** (obrys, po którym kroi się tkaninę). 

#### Rozszerzenia Kontraktu Danych (`src/core/contract.py`)
Struktury danych Pydantic V2 zostały wzbogacone o ścisłe reguły typowania bez naruszania kompatybilności wstecznej:
- `PathSegment.seam_allowance_mm: Optional[float] = Field(default=None, ge=0.0)` — opcjonalne nadpisanie naddatku dla konkretnego segmentu.
- `PathSegment.edge_role: EdgeRole = Field(default="seam")` — technologiczna rola krawędzi:
  $$\text{EdgeRole} \in \{\text{"seam"},\, \text{"fold"},\, \text{"hem"},\, \text{"elastic"},\, \text{"raw"}\}$$
- `PatternPiece.cut_contour: Optional[Contour] = Field(default=None)` — wyliczony zewnętrzny obrys cięcia.
- `PatternPiece.has_cut_contour -> bool` — flaga potwierdzająca obecność wygenerowanego obrysu cięcia.
- `PatternPiece.effective_boundary -> Contour` — właściwość zwracająca `cut_contour` jeśli istnieje, lub `outer_contour` (wykorzystywana automatycznie przez algorytmy nestingu, bounding box i detekcji kolizji).
- `PatternProject.has_seam_allowance -> bool` — flaga projektowa informująca eksportery o obecności naddatków.

---

#### Algorytm Offsetu Jednolitego (Uniform Seam Allowance)
Gdy wszystkie krawędzie elementu posiadają ten sam naddatek na szew (np. globalne $+10{,}0\text{ mm}$), system wykorzystuje zoptymalizowany silnik `pyclipper.PyclipperOffset`:
1. **Skalowanie całkowitoliczbowe 1000x**: `CLIPPER_SCALE = 1000.0`. Ponieważ Clipper operuje na 64-bitowych liczbach całkowitych, współrzędne milimetrowe są konwertowane ze skalą $1\text{ mm} = 1000\text{ µm}$. Eliminuje to wszelkie błędy zaokrągleń zmiennoprzecinkowych IEEE 754.
2. **Dołączenie ścieżki**: Kontur po usunięciu duplikatów (`CleanPolygon`) jest rejestrowany przez `AddPath` z typem złącza `JT_MITER` oraz typem pętli `ET_CLOSEDPOLYGON`.
3. **Miter Limit ($2{,}0$)**: Zabezpiecza narożniki przed tworzeniem ostrych, niekontrolowanych iglic (*miter spikes*). Przy kątach ostrych, jeśli wierzchołek odsunięty przekracza $2{,}0 \times d$, algorytm automatycznie ścina narożnik fazą (*miter clip / bevel*).
4. **Dylatacja i normalizacja**: Wynik dylatacji `delta = int(round(seam_mm * CLIPPER_SCALE))` jest skalowany powrotnie do milimetrów. Zwracana jest ścieżka o największym polu powierzchni bezwzględnym `abs(pyclipper.Area(p))`.
5. **Weryfikacja Shapely**: Sprawdzenie `Polygon(cut_pts).is_valid == True`. W przypadku mikroskopijnych samoprzecięć następuje automatyczne uzdrowienie za pomocą `shapely.validation.make_valid`.

*Weryfikacja matematyczna:* Skalibrowany kwadrat $100 \times 100\text{ mm}$ po nałożeniu naddatku $10{,}0\text{ mm}$ powiększa swoje gabaryty do dokładnie $120{,}000 \times 120{,}000\text{ mm}$ ($+20{,}00\text{ mm}$ w obu osiach, błąd $< 0{,}001\text{ mm}$).

---

#### Algorytm Offsetu Zmiennego per-Krawędź (Variable Per-Edge Seam Allowance)
W rzeczywistych wykrojach odzieżowych różne krawędzie wymagają odmiennych naddatków. Silnik zmiennego offsetu realizuje następujący potok geometryczny:

```
[Dyskretyzacja Segmentów]
        │
        ▼
[Weryfikacja Nawijania CCW/CW (pyclipper.Orientation)]
        │
        ▼
[Wyznaczenie Zewnętrznych Wektorów Normalnych n_i]
        │
        ▼
[Równoległe Odsunięcie Krawędzi: A_i = start + d*n, B_i = end + d*n]
        │
        ▼
[Przecięcie Mitrów Wzorami Cramera na Złączach Krawędzi]
        │
   ┌────┴──────────────────────────┐
   │ ||P_miter - V|| > 2.0 * d_max │
   ▼                               ▼
[Wstawienie P_miter]      [Fazowanie Bevel: Wstawienie B_{i-1} i A_i]
   └────┬──────────────────────────┘
        ▼
[Czyszczenie Pętli Samoprzecięć: Clipper CT_UNION + PFT_NONZERO]
        │
        ▼
[Prezerwacja Krawędzi Zagięcia Fold (Clamping do Y=0)]
        │
        ▼
[Walidacja Topologiczna: Shapely Polygon.is_valid]
```

1. **Rezolucja naddatków technologicznych (`resolve_effective_allowance`)**:
   - `seam_allowance_mm` podane jawnie $\to$ priorytet najwyższy.
   - `edge_role == "fold"` lub `"raw"` $\to 0{,}0\text{ mm}$.
   - `edge_role == "elastic"` lub `"hem"` $\to 15{,}0\text{ mm}$.
   - W pozostałych przypadkach $\to$ globalny naddatek bazowy ($10{,}0\text{ mm}$).
2. **Wektory normalne jednostkowe**: Dla każdego segmentu o wektorze stycznym $\vec{t} = (\frac{\Delta x}{L}, \frac{\Delta y}{L})$ wyznaczany jest wektor normalny skierowany na zewnątrz wielokąta:
   $$\vec{n} = \begin{cases} (t_y, -t_x) & \text{dla konturu zorientowanego przeciwnie do ruchu wskazówek zegara (CCW)} \\ (-t_y, t_x) & \text{dla konturu zorientowanego zgodnie z ruchem wskazówek zegara (CW)} \end{cases}$$
3. **Przecięcia mitrowe wzorami Cramera**:
   Dla sąsiednich odsuniętych krawędzi $i-1$ (od $A_{i-1}$ do $B_{i-1}$) oraz $i$ (od $A_i$ do $B_i$) rozwiązujemy układ równań parametrycznych:
   $$B_{i-1} + \lambda \vec{t}_{i-1} = A_i + \mu \vec{t}_i$$
   Wyznacznik układu wynosi: $\det = t_{(i-1)x} t_{iy} - t_{(i-1)y} t_{ix}$.
   Gdy $|\det| \ge 10^{-4}$ (linie niespójne / nierównoległe):
   $$\lambda = \frac{(A_{ix} - B_{(i-1)x}) t_{iy} - (A_{iy} - B_{(i-1)y}) t_{ix}}{\det}$$
   $$P_{\text{miter}} = B_{i-1} + \lambda \vec{t}_{i-1}$$
4. **Fazowanie Bevel (Bevel Clipping)**:
   Jeżeli odległość $\|P_{\text{miter}} - V_{\text{corner}}\| > \text{miter\_limit} \times \max(d_{i-1}, d_i)$, narożnik jest ścinany fazą poprzez wstawienie do wielokąta punktu końcowego krawędzi poprzedniej $B_{i-1}$ oraz punktu startowego krawędzi bieżącej $A_i$.
5. **Topologiczne czyszczenie samoprzecięć**:
   Zmienny offset na wklęsłych wcięciach i łukach generuje lokalne pętle samoprzecinające (tzw. rybie ogony / *swallowtails*). Kontur jest przekazywany do `pyclipper.Pyclipper` i czyszczony operacją sumy boolowskiej `CT_UNION` z regułą wypełnienia `PFT_NONZERO`.
6. **Prezerwacja krawędzi zagięcia (Fold Preservation)**:
   Dla krawędzi zgięcia materiału (`edge_role="fold"`, np. dolna podstawa elementu *Frente*) naddatek wynosi dokładnie $0{,}0\text{ mm}$. Aby zapobiec mikro-wybrzuszeniom wynikającym z dylatacji sąsiednich krawędzi pionowych, algorytm rygorystycznie wyrównuje wierzchołki brzegowe do prostej zagięcia ($Y = 0{,}000\text{ mm}$ z tolerancją $< 0{,}01\text{ mm}$).
7. **Krawędzie ściągaczy elastycznych (Elastic Allowance)**:
   Na elemencie *Cubre boca* łuki górny i dolny mają rolę `elastic` ($+15{,}0\text{ mm}$ na tunel gumki), podczas gdy boki mają naddatek standardowy $+10{,}0\text{ mm}$. Wymiary zewnętrzne powiększają się o $+20{,}0\text{ mm}$ na szerokości i $+30{,}0\text{ mm}$ na wysokości.

---

### 2. R2: Podwójny Silnik Upakowania 2D i Kafelkowania Wielostronicowego A4 (`src/nesting/a4_nester.py` & `src/exporters/a4_tiler_pdf.py`)

Kluczowy moduł do druku szablonów na standardowych drukarkach biurowych/domowych w formacie **ISO A4** ($210{,}0 \times 297{,}0\text{ mm}$). Obsługuje dwa komplementarne tryby pracy:

| Cecha | Tryb Hybrydowy (`mode="hybrid"`) | Tryb Plakatowy (`mode="poster"`) |
| :--- | :--- | :--- |
| **Koncepcja przewodnia** | *Smart Pack & Tile* (inteligentna minimalizacja klejenia) | *Full-Sheet Grid Tile* (pełna plansza zoptymalizowana na zużycie materiału) |
| **Elementy mieszczące się w A4** | Drukowane w całości na pojedynczych kartkach bez rozcinania | Upakowane razem na dużej planszy i cięte siatką kafelków |
| **Elementy ponadwymiarowe** | Dzielone na minimalną liczbę sąsiednich arkuszy A4 | Część wspólnej regularnej siatki A4 |
| **Liczba stron (Gorro Trapper)** | **13 stron A4** | **12 stron A4** (siatka $4 \times 3$) |
| **Główne zastosowanie** | Wygodne, szybkie szycie DIY (mniej klejenia elementów) | Optymalne zużycie papieru i podgląd pełnego układu krojczego |

---

#### Tryb Hybrydowy (Smart Pack & Tile)
1. **Rozwinięcie mnożników i symetrii**: Wszystkie elementy projektu są rozwijane z uwzględnieniem ilości (`quantity`). Elementy parzyste `x2` (*Visera*, *Lateral*, *Orejera Compuesta*) otrzymują swoje symetryczne odbicia lustrzane w osi $X$ (`mirror_piece_x`), zachowując metadane segmentów i naddatki.
2. **Ortogonalny klasyfikator rozmiaru (`classify_piece_fit`)**:
   Dla każdego z 9 elementów Bounding Box konturu cięcia (`effective_boundary`) jest testowany w oknie zadruku arkusza A4:
   $$W_{\text{printable}} = 210{,}0 - 2 \times 10{,}0 = 190{,}0\text{ mm}, \quad H_{\text{printable}} = 297{,}0 - 2 \times 10{,}0 = 277{,}0\text{ mm}$$
   Sprawdzenie odbywa się w 4 rotacjach: $0^\circ, 90^\circ, 180^\circ, 270^\circ$.
   - **Elementy całe (Whole Pieces)**: 5 części (*Frente*, 2x *Visera*, 2x *Lateral*) mieści się w oknie i jest pakowane na pojedynczych arkuszach A4 bez żadnych cięć montażowych.
   - **Elementy ponadwymiarowe (Oversized Pieces)**: 4 części (*Fuelle superior* $150 \times 370\text{ mm}$ z naddatkiem, *Cubre boca* $300 \times 170\text{ mm}$ z naddatkiem oraz 2x *Orejera Compuesta* $270 \times 215\text{ mm}$ z naddatkiem) przekraczają okno i są dzielone na siatkę kafelków z zakładką klejenia $15{,}0\text{ mm}$.

---

#### Tryb Plakatowy (Full-Sheet Grid Tile)
1. **Półkowy Nesting 2D (Shelf Packing Best-Fit)**: Wszystkie 9 elementów wykroju zostaje ciasno rozmieszczonych na jednolitej planszy produkcyjnej o szerokości do $700\text{ mm}$. Algorytm testuje rotacje $0^\circ$ oraz $90^\circ$, dopasowując elementy do poziomych półek z marginesem separacji $15\text{ mm}$.
2. **Kafelkowanie regularną siatką**: Wygenerowana plansza zostaje pocięta na regularną siatkę arkuszy A4 o wymiarach $4 \text{ kolumny} \times 3 \text{ rzędy} = 12 \text{ stron A4}$. Każda granica podziału otrzymuje zakładkę na klej o szerokości $15{,}0\text{ mm}$.

---

#### Przemysłowy Szablon Wydruku A4 (Cechy i Znaczniki Montażowe)
Każda strona A4 w generowanym pliku PDF ([`gorro_trapper_a4_print_hybrid.pdf`](output/stage2_a4_and_seams/gorro_trapper_a4_print_hybrid.pdf) i [`gorro_trapper_a4_print_poster.pdf`](output/stage2_a4_and_seams/gorro_trapper_a4_print_poster.pdf)) zawiera komplet profesjonalnych oznaczeń:
- **Ścisłe wymiary ISO A4**: $210{,}0 \times 297{,}0\text{ mm} = 595{,}28 \times 841{,}89\text{ pt}$ (z tolerancją poniżej $\pm 0{,}5\text{ pt}$).
- **Ramka marginesu drukarki**: Wycofana o $10{,}0\text{ mm}$ od krawędzi fizycznej arkusza, zabezpieczająca rysunek przed obcięciem przez rolki transportowe drukarki.
- **Linie i pasy klejenia**: Szary pas zakładki o szerokości $15{,}0\text{ mm}$ z przerywaną linią montażową i czytelnym napisem `"LINIA SKLEJENIA / GLUE LINE (15 mm)"`.
- **Diamenty montażowe (Alignment Diamonds / Pasery)**: Romby o promieniu $5\text{ mm}$ z wycentrowanym krzyżem centrującym oraz współrzędnymi kafelków (np. `"R1-C1 | R1-C2"`). Etykiety tekstowe posiadają zabezpieczenie przed obcięciem przy prawym marginesie arkusza – jeśli romb leży po prawej stronie kartki, tekst jest automatycznie renderowany po jego lewej stronie.
- **Minimapa montażowa (Assembly Mini-Map)**: W nagłówku każdego arkusza kafelkowanego znajduje się schematyczna miniatura siatki $R \times C$ z wyraźnym wyróżnieniem i przekreśleniem bieżącej kartki. Co istotne, tło minimapy posiada właściwość `fill=None` (jest przezroczyste), dzięki czemu nie zasłania linii wykroju leżących w tle.
- **Kwadrat kalibracyjny 1:1 ($5{,}0 \times 5{,}0\text{ cm}$)**: Precyzyjny kwadrat kontrolny ($50 \times 50\text{ mm} = 141{,}73 \times 141{,}73\text{ pt}$) z wewnętrznym krzyżem milimetrowym i instrukcją sprawdzenia skali linijką przed krojeniem. Kwadrat posiada przezroczyste tło (`fill=None`), eliminując ryzyko przysłonięcia linii wykroju w gęstych układach plakatowych.
- **Podwójne kontury na wydruku**:
  - **Linia cięcia brutto**: solidna czarna linia o grubości $1{,}25\text{ pt}$.
  - **Linia szycia netto**: niebieska przerywana linia o grubości $0{,}9\text{ pt}$ (wzór kresek `[4 2] 0`).
  - **Etykiety technologiczne**: oznaczenia naddatków przy krawędziach (`+10 mm`, `+15 mm elastizar`, `0 mm dobles/fold`).
- **Gwarancja Bezkolizyjności (Non-Overlap Guarantee)**: Na wieloczęściowych arkuszach elementy zachowują bezwzględny brak kolizji (`poly1.intersection(poly2).area < 1e-2 mm²`).
- **Eliminacja Błędnych Cięciw (Anti-Spurious Diagonal Chords w PyMuPDF)**: W bibliotece PyMuPDF domyślne wywołanie `shape.finish()` zamykało otwarte polilinie powstałe z przycięcia elementu do krawędzi kafelka, kreśląc fałszywą cięciwę skośną w poprzek wykroju. Problem został w 100% rozwiązany poprzez wymuszenie parametru `closePath=False` na wszystkich otwartych liniach `LineString` i `MultiLineString`.

---

### 3. R3: Eksportery Clo3D z Podwójnym Konturem, Skrypt Runner i Testy

Wszystkie moduły eksportujące zostały zaktualizowane, by w pełni wspierać podwójne kontury, symetrię i standardy CAD.

#### Standard DXF-AAMA / ASTM z Podwójnym Konturem (`src/exporters/dxf_exporter.py`)
Podczas importu pliku DXF do programu **Clo3D** (`File -> Import -> DXF (AAMA/ASTM)`):
- **Warstwa `1` (BOUNDARY)**: otrzymuje zamknięty obrys cięcia brutto (`cut_contour`). Clo3D traktuje go jako zewnętrzny brzeg wykroju i tworzy z niego fizyczny płat tkaniny.
- **Warstwa `4` (INTERNAL)**: otrzymuje zamknięty obrys szycia netto (`outer_contour`) oraz linie wewnętrzne/zaszywki (`internal_lines`). W Clo3D linia ta staje się wewnętrzną linią bazową, do której natychmiast można przypisać narzędzie zszywania (*Segment Sewing* lub *Free Sewing*) w przestrzeni 3D!
- **Warstwa `7` (GRAINLINE)**: linia nitki prostej ze strzałką kierunkową.
- **Warstwa `8` (TEXT)**: metadane elementu, ilość sztuk i adnotacje krawieckie.
- *Kompatybilność wsteczna:* Jeśli wykrój nie posiada naddatków na szwy, linia `outer_contour` jest eksportowana na Warstwie `1` (dokładnie jak w Etapie 1).

System generuje warianty:
- `gorro_trapper_seams_clo3d_master_mm.dxf` — 6 części bazowych (jednostka mm),
- `gorro_trapper_seams_clo3d_full_set_mm.dxf` — kompletny zestaw 9 części z odbiciami symetrycznymi (jednostka mm),
- `gorro_trapper_seams_clo3d_full_set_cm.dxf` — kompletny zestaw 9 części w centymetrach.

---

#### Wektorowy PDF 1:1 i Arkusz Techniczny 1:1 (`src/exporters/pdf_exporter.py`)
- **Wektorowy PDF 1:1 dla Clo3D** ([`gorro_trapper_seams_clo3d_import_full_set_1to1.pdf`](output/stage2_a4_and_seams/gorro_trapper_seams_clo3d_import_full_set_1to1.pdf)): Czysty, bezramkowy plik wektorowy PostScript, gdzie obrys cięcia narysowany jest ciągłą czarną linią ($1{,}0\text{ pt}$), a linia szycia niebieską linią przerywaną ($0{,}8\text{ pt}$, `[4 2] 0`).
- **Arkusz Techniczny 1:1** ([`gorro_trapper_seams_technical_sheet_1to1.pdf`](output/stage2_a4_and_seams/gorro_trapper_seams_technical_sheet_1to1.pdf)): Zawiera beżowe wypełnienie materiału (`rgb(0.89, 0.78, 0.56)`), brązowy obrys cięcia, niebieską linię szycia z czerwonymi punktami węzłowymi CAD, linie wymiarowe netto/brutto oraz etykiety naddatków przy każdym segmencie.

---

#### Skrypt Uruchomieniowy Etapu 2 (`run_stage2.py`)
Główny skrypt potokowy integruje wszystkie klocki Etapu 2. Jedno polecenie generuje **35 gotowych plików produkcyjnych** w katalogu `output/stage2_a4_and_seams/`:
1. `gorro_trapper_stage2_project.json` — kontrakt danych JSON z naddatkami na szwy,
2. 3 pliki **DXF-AAMA** (master mm, full set mm, full set cm),
3. 2 pliki **Wektorowe PDF 1:1** dla Clo3D (master i full set),
4. 1 plik **Wektorowego Arkusza Technicznego 1:1 PDF** + podgląd rastrowy PNG 150 DPI,
5. 1 wielostronicowy **PDF A4 Trybu Hybrydowego** (13 stron) + 13 podglądów PNG (150 DPI),
6. 1 wielostronicowy **PDF A4 Trybu Plakatowego** (12 stron) + 12 podglądów PNG (150 DPI).

---

#### Rygorystyczny Zestaw Testów Automatycznych
Wszystkie funkcjonalności Etapu 2 zostały pokryte testami automatycznymi w `tests/test_stage2_seams_and_a4.py` (23 testy jednostkowe i integracyjne) oraz testami odpornościowymi (adversarial) w `tests/test_adversarial_*.py`.

**Wynik całego repozytorium: 78 testów zdanych w 100% (0 błędów, 0 ostrzeżeń):**
- 5 testów: `tests/test_stage1_clo3d_export.py` (regresja Etapu 1),
- 23 testy: `tests/test_stage2_seams_and_a4.py` (oficjalne kryteria akceptacji Etapu 2),
- 17 testów: `tests/test_adversarial_seam_allowance.py` (ekstremalne kąty, wielokąty gwieździste, dylatacje 50/100mm, zwijanie szczelin),
- 20 testów: `tests/test_adversarial_nesting.py` (rotacje ortogonalne, bezkolizyjność kształtów wklęsłych, niestandardowe marginesy),
- 13 testów: `tests/test_adversarial_nesting_tiling.py` (brak cięciw diagonalnych, ochrona przed zasłanianiem, kafelkowanie ultra-długich elementów).

---

## 💻 ETAP 4: Interaktywny Cyfrowy Warsztat Krawiecki (CAD Studio Web UI)

W Etapie 4 powstała kompletna, interaktywna aplikacja webowa CAD uruchamiana jednym poleceniem (`python run_server.py`), łącząca zaawansowany silnik backendowy **FastAPI** z dedykowanym interfejsem graficznym **Tailoring CAD Studio (SPA)**:

```
[Przeglądarka Web SPA] (HTML5 / Tailwind / Lucide / Canvas 2D)
         │
         │  REST API (JSON / FormData / Binary Downloads)
         ▼
[Serwer Aplikacji FastAPI] (src/ui/server.py & run_server.py)
   ├─ GET  /api/health & /api/presets
   ├─ GET/POST /api/project (Zarządzanie stanem sesji CAD)
   ├─ POST /api/digitize (Wgrywanie plików JPG/PNG & Potok Schneidera)
   ├─ POST /api/pieces/{id}/unfold (1-klik rozkładanie zgięć)
   ├─ POST /api/pieces/{id}/mirror (Odbicie lustrzane X)
   ├─ POST /api/pieces/{id}/seam-allowance (Suwak naddatków per-element)
   ├─ POST /api/pieces/{id}/segments/{idx}/role (Zmiana roli: fold, elastic, hem, seam)
   ├─ POST /api/nesting/calculate (Podgląd kafelkowania A4 Hybrid & Poster)
   └─ GET  /api/export/* (DXF-AAMA, 1:1 Clo3D PDF, Arkusz Techniczny, A4 Print PDF, JSON)
```

### Najważniejsze Cechy Cyfrowego Studia CAD:
1. **Wektorowy Canvas 2D w Skali 1:1**:
   - Płynna nawigacja: Pan (przeciąganie myszą ze spacją lub środkowym przyciskiem) oraz Zoom kółkiem myszy skupiony wokół kursora,
   - Adaptacyjna siatka milimetrowa ($10\text{ mm}$, $50\text{ mm}$, $100\text{ mm}$) z układem współrzędnych CAD (początek w lewym dolnym rogu, oś Y w górę),
   - Prezentacja tkaniny (półprzezroczysty fill), linii cięcia brutto (solid amber), linii szycia netto (dashed cyan), punktów węzłowych, krzywych Béziera, zaszewek i karbików montażowych,
   - Inspekcja węzłów kontrolnych CP1/CP2 krzywych sześciennych Béziera.
2. **Edycja Krawiecka na Żywo (Live Inspector)**:
   - **1-Klik Unfold**: zaznacz element z linią zgięcia materiału i kliknij *"Rozłóż (Unfold)"*, aby uzyskać pełny, rozłożony szablon 1:1 gotowy do wykrojenia,
   - **Odbicie Lustrzane**: 1-klikowe generowanie symetrycznego elementu,
   - **Role Krawędzi**: kliknij dowolny segment, aby zmienić jego rolę (`seam`, `fold`, `hem`, `elastic`, `raw`) lub przypisać indywidualny naddatek na szew,
   - **Skalowanie Anizotropowe**: wprowadź docelowe wymiary $W \times H\text{ cm}$, aby natychmiast wyzerować błędy zniekształceń rysunków rastrowych.
3. **Podgląd Wydruku A4 (Nesting & Tiling Preview)**:
   - Wizualna siatka wszystkich wygenerowanych arkuszy A4 z podziałem na strony, marginesami $10\text{ mm}$, zakładkami klejenia $15\text{ mm}$, paserami i kwadratem $5 \times 5\text{ cm}$.
4. **Wbudowany Dygitalizator (Drag & Drop)**:
   - Wgrywanie dowolnej grafiki z internetu z poziomu okna modalnego z wyborem trybu naddatków i tolerancji wektoryzacji.
5. **Eksporter Produkcyjny 1-Klik**:
   - Szybkie pobieranie gotowych plików DXF-AAMA R12, Clo3D 1:1 Vector PDF, Arkusza Technicznego PDF, szablonów A4 oraz kontraktu JSON.

#### Jak Uruchomić Studio CAD:
```powershell
python run_server.py
```
Aplikacja automatycznie otworzy przeglądarkę pod adresem `http://127.0.0.1:8000`.

---

| Biblioteka / Narzędzie | Wersja | Rola w Etapie 1 | Rola w Etapie 2 | Dlaczego właśnie to narzędzie? |
| :--- | :--- | :--- | :--- | :--- |
| **`pyclipper`** | 1.4.0 | Przygotowanie pod naddatki | **Kluczowy silnik naddatków na szwy**: dylatacja konturów `JT_MITER` (`MiterLimit=2.0`), orientacja CCW/CW, czyszczenie samoprzecięć `CT_UNION`. | Niezrównana wydajność natywnego C++ (Clipper2) i stabilność obliczeń na 64-bitowych liczbach całkowitych ze skalą 1000x (precyzja 1 µm). |
| **`shapely`** | 2.2.0 | Wyliczanie Bounding Box i punktu etykiet | **Geometria 2D, nesting i walidacja**: wyliczanie przecięć kafelków A4, wyrocznia bezkolizyjności (`intersection.area < 1e-2`), `Polygon.is_valid`, `make_valid`. | Standard branżowy geometrii obliczeniowej oparty na silniku GEOS C. |
| **`PyMuPDF` (`fitz`)** | 1.28.2 | Wektorowy PDF 1:1 dla Clo3D | **Wielostronicowy eksporter A4 PDF**: renderowanie paserów, minimap montażowych, kwadratu skali 5x5 cm oraz generowanie podglądów rastrowych PNG (150 DPI). | Błyskawiczny silnik C MuPDF, bezpośrednia obsługa PostScriptowych stylów linii (`dashes="[4 2] 0"`) i precyzyjne sterowanie zamykaniem ścieżek (`closePath=False`). |
| **`ezdxf`** | 1.4.5 | Struktura bloków DXF-AAMA | **Dual-contour DXF-AAMA**: separacja linii cięcia na Warstwę 1 i linii szycia na Warstwę 4 standardu odzieżowego. | Precyzyjna kontrola nad nagłówkami AutoCAD R12 i warstwami AAMA bez konieczności posiadania oprogramowania Autodesk. |
| **`pydantic`** | 2.13.1 | Kontrakt danych `PatternProject` | **Rozszerzenie kontraktu**: walidacja ról krawędzi (`EdgeRole`), właściwości `effective_boundary`, `cut_contour`, `seam_allowance_mm`. | Niezawodna walidacja typów w czasie wykonania i natychmiastowa serializacja JSON Schema dla frontendu. |
| **`pytest`** | 9.1.1 | Testy jednostkowe Etapu 1 | **Kompleksowa weryfikacja automatyczna**: 78 testów weryfikujących tolerancje geometryczne ($\pm 0{,}25\text{ mm}$ i $\pm 0{,}5\text{ pt}$). | Standard testowania w Pythonie z bogatym ekosystemem asercji i parametryzacji testów. |

---

## 📂 Struktura Projektu

```
image to stencile/
├── samples/
│   └── gorro_trapper.png                        # Referencyjny wykrój czapki z internetu
├── src/
│   ├── core/
│   │   ├── contract.py                          # KLOCEK 0: Kontrakt danych Pydantic V2 (Point2D, Contour, PatternPiece)
│   │   ├── geometry.py                          # Silnik 2D: krzywe Béziera, BoundingBox, skalowanie anizotropowe
│   │   └── seam_allowance.py                    # KLOCEK 2: Silnik naddatków na szwy (Pyclipper JT_MITER + Variable Cramer)
│   ├── nesting/
│   │   └── a4_nester.py                         # KLOCEK 3A: Silnik 2D Nestingu (Tryb Hybrydowy i Plakatowy)
│   ├── presets/
│   │   └── gorro_trapper.py                     # Wzorcowy model 6 części czapki z rolami krawędzi (fold, elastic)
│   └── exporters/
│       ├── a4_tiler_pdf.py                      # KLOCEK 3B: Eksporter wielostronicowego PDF A4 z paserami i minimapą
│       ├── dxf_exporter.py                      # KLOCEK 1A: Eksporter DXF-AAMA R12 dla Clo3D (Warstwa 1 i 4)
│       └── pdf_exporter.py                      # KLOCEK 1B: Eksporter Wektorowego PDF 1:1 i Arkusza Technicznego
├── tests/
│   ├── test_stage1_clo3d_export.py              # Testy bazowe Etapu 1 (5 testów)
│   ├── test_stage2_seams_and_a4.py              # Oficjalne testy akceptacyjne Etapu 2 (23 testy)
│   ├── test_adversarial_seam_allowance.py       # Testy odpornościowe silnika szwów (17 testów)
│   ├── test_adversarial_nesting.py              # Testy odpornościowe nestingu i rotacji (20 testów)
│   └── test_adversarial_nesting_tiling.py       # Testy odpornościowe kafelkowania A4 (13 testów)
├── output/
│   ├── stage1_clo3d/                            # Artefakty Etapu 1 (DXF, PDF, JSON, PNG)
│   └── stage2_a4_and_seams/                     # Komplet 35 artefaktów produkcyjnych Etapu 2:
│       ├── gorro_trapper_stage2_project.json    # Kontrakt JSON z obrysami cięcia
│       ├── gorro_trapper_seams_clo3d_master_mm.dxf
│       ├── gorro_trapper_seams_clo3d_full_set_mm.dxf
│       ├── gorro_trapper_seams_clo3d_full_set_cm.dxf
│       ├── gorro_trapper_seams_clo3d_import_master_1to1.pdf
│       ├── gorro_trapper_seams_clo3d_import_full_set_1to1.pdf
│       ├── gorro_trapper_seams_technical_sheet_1to1.pdf
│       ├── gorro_trapper_seams_technical_preview.png
│       ├── gorro_trapper_a4_print_hybrid.pdf    # Szablon A4 Tryb Hybrydowy (13 stron)
│       ├── gorro_trapper_a4_print_poster.pdf    # Szablon A4 Tryb Plakatowy (12 stron)
│       ├── hybrid_page_01.png ... hybrid_page_13.png
│       └── poster_page_01.png ... poster_page_12.png
├── run_stage1.py                                # Skrypt uruchomieniowy Etapu 1
├── run_stage2.py                                # Skrypt uruchomieniowy Etapu 2 (generuje 35 plików)
├── requirements.txt                             # Zależności projektu
└── README.md                                    # Pełna dokumentacja architektoniczna i podręcznik użytkownika
```

---

## 🚀 Jak Uruchomić i Przetestować?

### 1. Instalacja zależności
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Uruchomienie testów automatycznych
Aby uruchomić pełny zestaw 78 testów regresyjnych i odpornościowych:
```powershell
pytest -v
```

Aby uruchomić dedykowany zestaw testów kryteriów akceptacji Etapu 2 (23 testy):
```powershell
pytest tests/test_stage2_seams_and_a4.py -v
```

### 3. Wygenerowanie artefaktów Etapu 2 (Naddatki, Clo3D i A4)
Uruchomienie głównego skryptu potokowego Etapu 2:
```powershell
python run_stage2.py
```
Skrypt wygeneruje w katalogu `output/stage2_a4_and_seams/` komplet 35 plików w czasie poniżej 5 sekund.

### 4. Wygenerowanie artefaktów bazowych Etapu 1 (opcjonalnie)
```powershell
python run_stage1.py
```

---

### 5. Import plików z naddatkami do programu Clo3D:

#### Metoda 1: Import Wektorowego PDF 1:1 (Najwygodniejszy)
1. W programie Clo3D wybierz `File -> Import -> Adobe PDF`.
2. Wskaż plik [`output/stage2_a4_and_seams/gorro_trapper_seams_clo3d_import_full_set_1to1.pdf`](output/stage2_a4_and_seams/gorro_trapper_seams_clo3d_import_full_set_1to1.pdf).
3. W oknie dialogowym importu ustaw:
   - **Scale**: `100% (mm)`,
   - Zaznacz opcję importu linii wewnętrznych (*Import as Internal Lines*).
4. Clo3D zaimportuje komplet 9 części o wymiarach cięcia brutto, z precyzyjnie naniesioną niebieską linią szycia netto wewnątrz każdego elementu.

#### Metoda 2: Przemysłowy Standard DXF-AAMA
1. W programie Clo3D wybierz `File -> Import -> DXF (AAMA/ASTM)`.
2. Wskaż plik [`output/stage2_a4_and_seams/gorro_trapper_seams_clo3d_full_set_mm.dxf`](output/stage2_a4_and_seams/gorro_trapper_seams_clo3d_full_set_mm.dxf).
3. Wybierz jednostkę **mm** oraz standard **AAMA**.
4. Clo3D przypisze Warstwę `1` jako zewnętrzny brzeg tkaniny (*Pattern Outline*), a Warstwę `4` jako linie wewnętrzne (*Internal Lines* / linie szycia).

---

### 6. Instrukcja Domowego Druku Szablonów A4 (DIY):

1. Otwórz plik [`output/stage2_a4_and_seams/gorro_trapper_a4_print_hybrid.pdf`](output/stage2_a4_and_seams/gorro_trapper_a4_print_hybrid.pdf) (lub wersję plakatową [`gorro_trapper_a4_print_poster.pdf`](output/stage2_a4_and_seams/gorro_trapper_a4_print_poster.pdf)) w darmowym programie Adobe Acrobat Reader lub przeglądarce.
2. **Kluczowe ustawienie drukarki**: W oknie drukowania wybierz **Skala: 100%** lub **Faktyczny rozmiar (Actual Size)**. *Nigdy nie wybieraj opcji "Dopasuj do strony" (Fit to page)*!
3. **Weryfikacja skali**: Po wydrukowaniu strony 1 zmierz linijką **Kwadrat Kalibracyjny ($5{,}0 \times 5{,}0\text{ cm}$)**. Kwadrat musi mieć idealnie $50\text{ mm} \times 50\text{ mm}$.
4. **Montaż elementów kafelkowanych**:
   - Przytnij prawą lub dolną krawędź kartki wzdłuż przerywanej linii klejenia.
   - Nałóż klej na pas zakreślony jako `"LINIA SKLEJENIA / GLUE LINE (15 mm)"`.
   - Zsuń ze sobą sąsiednie arkusze, dopasowując połówki **rombów pasowania (Alignment Diamonds)** tak, aby utworzyły idealny diament z identyczną etykietą (np. `R1-C1 | R1-C2`).
   - Wytnij elementy nożycami po **ciągłej czarnej linii cięcia**.
   - Prowadź ścieg maszyny do szycia wzdłuż **niebieskiej przerywanej linii szycia**.

---

---

## 🗺️ Zaktualizowana Mapa Drogowa Projektu (Roadmap)

Zgodnie z uaktualnioną metodyką inżynierii krawieckiej, potok przetwarzania został zorganizowany według naturalnej logiki powstawania wykroju:

```mermaid
flowchart TD
    subgraph E1 ["Etap 1: Fundament CAD & Semantyka Krawiecka [UKOŃCZONY]"]
        A1["Kontrakt danych Pydantic V2<br/>(Typy: CUT, FOLD, SEAM, SLIT, DART, NOTCH)"]
        A2["Silnik geometrii 2D & Unfold<br/>(Kalibracja anizotropowa, rozkładanie zgięć unfold)"]
        A3["Eksportery Clo3D DXF-AAMA R12<br/>(Warstwy: 1 Boundary, 2 Notches, 4 Internal, 5 Fold, 7 Grainline, 8 Text)"]
    end

    subgraph E2 ["NOWY ETAP 2: Percepcja, Skala & Semantyka Obrazu [NOWY PRIORYTET]"]
        B1["1. Odzyskiwanie skali (Scale Recovery)<br/>• Wymiary tekstowe (OCR)<br/>• Kalibracja 2 punktów użytkownika ('to ma 40 cm')<br/>• Kwadrat kontrolny 5x5 cm / DPI"]
        B2["2. Klasyfikacja semantyczna linii<br/>• Linia cięcia (Cut)<br/>• Linia zgięcia (Fold / Doblez)<br/>• Wewnętrzne rozcięcia i zaszewki (Slits / Darts)<br/>• Flaga zapasów: czy szwy są już wrysowane na grafice?<br/>• Karbiki pasowania (Notches)"]
        B3["3. Wektoryzacja rastra (Raster-to-Vector)<br/>• Ekstrakcja konturów OpenCV / vtracer<br/>• Dopasowanie krzywych Béziera algorytmem Schneidera (fitCurves)"]
        B1 --> B2 --> B3
    end

    subgraph E3 ["Etap 3: Zapas na Szew, 2D Nesting & Kafelkowanie A4 [UKOŃCZONY]"]
        C1["Silnik zapasów na szwy (Pyclipper + Variable Cramer)"]
        C2["2D Nesting (Hybrid Smart Pack + Poster Grid)"]
        C3["Wielostronicowy PDF A4 z zakładkami 15mm i paserami R-C"]
    end

    subgraph E4 ["Etap 4: Wizualny Edytor / UI (React + Paper.js / FastAPI)"]
        D1["Interaktywny Canvas w przeglądarce"]
        D2["Edycja węzłów, zmiana roli krawędzi (1-klik Unfold na linii fold)"]
        D3["Zintegrowany eksport do Clo3D i A4 PDF z poziomu UI"]
    end

    E1 --> E2 --> E3 --> E4
```

- [x] **Etap 1: Fundament Architektury, Kontrakt Danych & Standard Clo3D DXF-AAMA**
  - Kompletny kontrakt `PatternProject` z obsługą typów `CUT`, `FOLD`, `SEAM`, `SLIT`, `NOTCH`.
  - Pełna obsługa warstw **DXF-AAMA** (Warstwa `1` Boundary, `2` Notches, `4` Internal, `5` Fold, `7` Grainline, `8` Text).
  - Algorytm rozkładania symetrii na linii zgięcia (`unfold_piece_on_fold`).
  - Eksportery wektorowe PDF 1:1 oraz DXF-AAMA (mm / cm) dla Clo3D.
- [x] **NOWY ETAP 2: Percepcja, Skala & Semantyka Obrazu (Pattern Semantic Digitizer) [UKOŃCZONY]**
  - Wczytywanie dowolnej grafiki wykroju z internetu (JPG / PNG).
  - Odzyskiwanie skali 1:1 (`src/digitizer/scale_recovery.py`):
    - Scenariusz A: skalowanie anizotropowe z wymiarów i wielojęzyczny parser adnotacji krawieckich (hiszpański, polski, angielski),
    - Scenariusz B: dwupunktowa kalibracja izotropowa (`calibrate_from_two_points`),
    - Scenariusz C: automatyczna detekcja kwadratu kontrolnego $50 \times 50\text{ mm}$ (`detect_calibration_square`) oraz odczyt DPI z metadanych PNG pHYs i JPEG EXIF.
  - Wektoryzacja rastra i dopasowanie krzywych (`src/digitizer/vectorizer.py`):
    - Segmentacja wypełnień HSV z tłumieniem napisów i znaku wodnego oraz filtrem *flat-fill ratio*,
    - Detekcja narożników geometrycznych analizą dyskretnego kąta skrętu (*turning angle*),
    - Optymalne dopasowanie krzywych sześciennych Béziera metodą najmniejszych kwadratów Philipa J. Schneidera (*Graphics Gems I*).
  - Semantyczny klasyfikator krawiecki (`src/digitizer/semantic_classifier.py`):
    - 3 tryby naddatków na szew: `net_only` (odsunięcie na zewnątrz), `included` (odsunięcie do wewnątrz), `dual_detected` (parowanie konturów),
    - Detekcja linii zgięcia (`LINE_TYPE_FOLD`) i konfigurowalne rozkładanie symetrii (`auto_unfold`),
    - Wewnętrzne linie cięcia i zaszewki $V$ (`InternalLine` na warstwie AAMA Layer 4),
    - Karbiki montażowe (`Notch` na warstwie AAMA Layer 2 z wektorami normalnymi $\vec{n}$).
  - Pełny potok E2E i nakładki diagnostyczne (`src/digitizer/pipeline.py` & `run_digitizer.py`):
    - Publikacyjny zbiorczy arkusz diagnostyczny (`diagnostic_contact_sheet.png`),
    - Przełącznik na osobne pliki PNG (`--separate-png`),
    - Automatyczny eksport do Clo3D DXF-AAMA, PDF 1:1 oraz kafelkowania A4.
- [x] **Etap 3: Silnik Zapasów na Szwy, 2D Nesting i Kafelkowanie A4 do Druku Domowego**
  - Jednolity i zmienny (per-krawędź) silnik odsunięć szwów (`PyclipperOffset` `JT_MITER` + wektory normalne z miterem Cramera).
  - Tryb Hybrydowy (pakowanie całych elementów na A4 + kafelkowanie ponadwymiarowych).
  - Tryb Plakatowy (siatka $4 \times 3$ arkuszy ze wspólnego arkusza).
  - Zakładki klejenia $15\text{ mm}$, romby pasowania $R-C$, mini-mapa montażowa i kwadrat kalibracyjny $50 \times 50\text{ mm}$.
- [x] **Etap 4: Wizualny Edytor / UI (FastAPI + Interaktywne Studio CAD 1:1) [UKOŃCZONY]**
  - Kompletny zintegrowany serwer aplikacji webowej (`run_server.py`, `src/ui/server.py`).
  - Interaktywny 2D Vector CAD Canvas (`src/ui/static/app.js`):
    - Skala fizyczna 1:1 z podziałką milimetrową i adaptacyjną siatką CAD,
    - Płynny Pan i Zoom kółkiem myszy z centrowaniem na kursorze,
    - Renderowanie linii cięcia (solid amber), linii szycia (dashed cyan), punktów węzłowych, krzywych Béziera CP1/CP2, zaszewek i karbików montażowych.
  - Edycja semantyki krawieckiej na żywo:
    - 1-klik Unfold wzdłuż linii zgięcia (`unfold_piece_on_fold`),
    - Odbicie lustrzane w osi X (`mirror_piece_x`),
    - Zmiana ról krawędzi (`seam`, `fold`, `hem`, `elastic`, `raw`) oraz naddatku per-krawędź,
    - Suwaki naddatków na szew (globalne oraz per-element) w czasie rzeczywistym.
  - Wizualny podgląd kafelkowania A4:
    - Prezentacja wszystkich wygenerowanych arkuszy A4 z marginesami 10 mm, pasami klejenia 15 mm i paserami $R-C$.
  - Wbudowany dygitalizator (Drag & Drop):
    - Wgrywanie surowych grafik z natychmiastową percepcją i wektoryzacją Schneidera.
  - Szybki 1-klik eksport z poziomu menu UI:
    - Clo3D DXF-AAMA (AutoCAD R12), Clo3D 1:1 PDF, Arkusz Techniczny PDF, wielostronicowy A4 Print PDF, kontrakt JSON.


