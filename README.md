# Image-to-Pattern CAD: Raster-to-CAD Pattern Digitizer & Nester

System konwersji rastrowych grafik wykrojów krawieckich z internetu (JPG / PNG) do produkcyjnych plików wektorowych w skali 1:1 (**DXF-AAMA** oraz **Wektorowy PDF**) dla programu **Clo3D**, z funkcją automatycznego upakowania (**2D Nesting**) i kafelkowania (**Tiling**) na arkusze **A4** do druku domowego.

---

## 🎯 Cel Projektu

Wykroje krawieckie publikowane w internecie (np. na blogach, w formatach JPG/PNG) nie nadają się do bezpośredniego załadowania do profesjonalnych programów 3D takich jak **Clo3D**, ponieważ:
1. **Clo3D wymaga plików wektorowych**: akceptuje wyłącznie pliki **DXF** (w standardzie odzieżowym **AAMA/ASTM**) lub wektorowe pliki **Adobe PDF**.
2. **Brak skali globalnej (Pułapka rysunków z internetu)**: Autorzy grafik często rysują elementy poglądowo w różnej skali pikseli (np. na referencyjnym wykroju czapki *Cubre boca* opisane jako 28 cm jest w pikselach węższe niż *Orejera Compuesta* opisana jako 25 cm).
3. **Wymiary większe niż arkusz A4**: Elementy odzieży (np. pas środkowy czapki o długości 35 cm) nie mieszczą się na pojedynczej kartce A4 ($21{,}0 \times 29{,}7\text{ cm}$) i wymagają inteligentnego podziału ze znacznikami klejenia lub ciasnego upakowania mniejszych części dla oszczędności papieru.

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

## 🛠️ Wykorzystane Technologie i Uzasadnienie Wyboru

| Biblioteka / Narzędzie | Rola w projekcie | Dlaczego właśnie to narzędzie? |
| :--- | :--- | :--- |
| **`ezdxf`** | Generowanie plików DXF-AAMA | Najlepsza i najbardziej stabilna biblioteka DXF w Pythonie. Pozwala na niskopoziomowe tworzenie struktur bloków AAMA i encji R12 bez konieczności instalowania oprogramowania AutoCAD. |
| **`PyMuPDF` (`pymupdf` / `fitz`)** | Silnik generowania PDF 1:1 | Oparty na ultrawydajnym silniku C MuPDF. Tworzy idealnie ostre, matematyczne wektory (linie i krzywe Béziera) w standardzie PostScript. |
| **`shapely` 2.0** | Obliczenia geometryczne 2D | Standard w geometrii obliczeniowej. Służy do wyliczania Bounding Boxów, pól powierzchni oraz wyznaczania punktu reprezentatywnego (`representative_point`) wewnątrz wklęsłych kształtów (np. nausznika) do idealnego pozycjonowania etykiet. |
| **`pydantic` V2** | Kontrakt danych (JSON Schema) | Gwarantuje ścisłą walidację typów, konwersję jednostek oraz automatyczną serializację/deserializację projektów pomiędzy Pythonem a TypeScriptem. |
| **`pyclipper` (Clipper2)** | Zapas na szwy (przygotowany pod Etap 2) | Najwydajniejszy silnik C++ do odsuwania konturów (offsettingu) z funkcją `JT_MITER` zapobiegającą powstawaniu ostrych szpiców na rogach. |
| **`pytest`** | Testy jednostkowe i regresyjne | Zapewnia automatyczną weryfikację poprawności wymiarów z tolerancją poniżej $0{,}05\text{ mm}$. |

---

## 📂 Struktura Projektu

```
image to stencile/
├── samples/
│   └── gorro_trapper.png              # Referencyjny wykrój czapki z internetu
├── src/
│   ├── core/
│   │   ├── contract.py                # KLOCEK 0: Kontrakt danych (PatternProject JSON)
│   │   └── geometry.py                # Silnik 2D: krzywe Béziera, BoundingBox, skalowanie anizotropowe
│   ├── presets/
│   │   └── gorro_trapper.py           # Precyzyjny model 6 elementów czapki w skali 1:1
│   └── exporters/
│       ├── dxf_exporter.py            # KLOCEK 1A: Eksporter DXF-AAMA / ASTM dla Clo3D
│       └── pdf_exporter.py            # KLOCEK 1B: Eksporter Wektorowego PDF 1:1 dla Clo3D
├── tests/
│   └── test_stage1_clo3d_export.py    # Zestaw testów automatycznych (100% zdanych)
├── output/
│   └── stage1_clo3d/                  # Wygenerowane pliki produkcyjne (DXF, PDF, JSON, PNG)
├── run_stage1.py                      # Skrypt uruchomieniowy generujący komplet plików
├── requirements.txt                   # Zależności projektu
└── README.md
```

---

## 🚀 Jak Uruchomić i Przetestować?

### 1. Instalacja zależności
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Uruchomienie testów jednostkowych
```bash
pytest -v
```

### 3. Wygenerowanie plików produkcyjnych dla Clo3D
```bash
python run_stage1.py
```

### 4. Import do programu Clo3D:
- **Metoda 1 (Najszybsza – PDF):** W Clo3D wybierz `File -> Import -> Adobe PDF`, wskaż plik [`output/stage1_clo3d/gorro_trapper_clo3d_import_full_set_1to1.pdf`](output/stage1_clo3d/gorro_trapper_clo3d_import_full_set_1to1.pdf) i ustaw skalę na **100% (mm)**.
- **Metoda 2 (Przemysłowa – DXF):** W Clo3D wybierz `File -> Import -> DXF (AAMA/ASTM)`, wskaż plik [`output/stage1_clo3d/gorro_trapper_clo3d_full_set_mm.dxf`](output/stage1_clo3d/gorro_trapper_clo3d_full_set_mm.dxf) i wybierz jednostkę **mm**.

---

## 🗺️ Mapa Drogowa Kolejnych Etapów

- [x] **Etap 1**: Fundament Architektury, Kontrakt Danych `PatternProject` i Eksporter do Clo3D (DXF-AAMA + Wektorowy PDF 1:1).
- [ ] **Etap 2**: Konfigurowalny silnik zapasów na szwy (`pyclipper` z `JT_MITER`) oraz automatyczny **2D Nesting** i kafelkowanie (**Tiling**) na kartki **A4** z marginesami klejenia.
- [ ] **Etap 3**: Moduł Computer Vision / AI (`vtracer` + `SAM 2` + `fitCurves`) do automatycznego wycinania i wektoryzacji elementów bezpośrednio z wgranego zdjęcia.
- [ ] **Etap 4**: Moduł AI Vision (Gemini API) do automatycznego odczytu wymiarów, adnotacji i mnożników z grafiki.
- [ ] **Etap 5**: Interaktywny edytor w przeglądarce (**React + TypeScript + Paper.js**) z manipulacją węzłami i podglądem na żywo.
