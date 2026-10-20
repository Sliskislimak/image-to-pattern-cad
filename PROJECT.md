# Project: Image-to-Pattern CAD Stage 2 — Pattern Semantic Digitizer

## Architecture
The Stage 2 Pattern Semantic Digitizer transforms raw raster sewing pattern images (PNG/JPG, e.g., `samples/gorro_trapper.png`) into CAD-ready tailoring assets (`PatternProject` with `PatternPiece`, DXF-AAMA, 1:1 Vector PDF, and A4 printables).

```
[Input Raster Image] (e.g. samples/gorro_trapper.png)
       │
       ▼
[1. Scale Recovery Engine] (src/digitizer/scale_recovery.py)
  ├─ Scenario A: Anisotropic dimension scaling & multilingual text parser
  ├─ Scenario B: Two-point reference calibration (user or automated points + real distance)
  └─ Scenario C: 5.0 x 5.0 cm calibration square quad detector & image DPI metadata
       │  Output: ScaleResult (mm_per_px_x, mm_per_px_y, is_isotropic, confidence, metadata)
       ▼
[2. Segmentation & Schneider Vectorizer] (src/digitizer/vectorizer.py)
  ├─ Color/luminance thresholding & background suppression
  ├─ Sharp corner / vertex turning-angle detection
  ├─ Collinear straight edge detection (PathSegment.line)
  └─ Philip J. Schneider's least-squares cubic Bézier fitting (PathSegment.cubic_bezier)
       │  Output: Piece pixel/mm contours & boundary segments
       ▼
[3. Semantic Feature Classifier] (src/digitizer/semantic_classifier.py)
  ├─ Seam allowance modes: 'net_only' (outward offset), 'included' (inward offset), 'dual_detected'
  ├─ Fold edge detection & configurable auto_unfold (Layer 5 vs 1:1 unfolded piece)
  ├─ Internal cuts, darts & slits (AAMA Layer 4, e.g., red V-darts on Orejera Compuesta)
  └─ Tailoring notches (AAMA Layer 2 with outward normal vectors)
       │  Output: Standard PatternProject & PatternPiece models
       ▼
[4. Pipeline, CLI & Exporters Integration] (src/digitizer/pipeline.py, run_digitizer.py)
  ├─ CLI options: --input, --scale-method, --seam-mode, --auto-unfold, --global-seam-mm
  ├─ Diagnostic overlays in output/stage2_digitizer/ (segmentation, corners, beziers, semantics, rulers)
  ├─ Clo3D DXF-AAMA AutoCAD R12 export (Layers 1, 2, 4, 5, 7, 8)
  ├─ Clo3D 1:1 Vector PDF
  └─ A4 Home Tiler multi-page printable PDF (Hybrid & Poster modes)
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F01 | Two-Point Calibration | Computes isotropic scale `mm_per_px = (L_cm * 10) / ||P2 - P1||_2` from two points and declared real distance. | M1 | survey |
| F02 | Calibration Square Detection | Finds 50x50 mm reference square using contour quad approximation (`approxPolyDP`, 4 vertices, convexity, aspect ratio 0.95–1.05, right angles). | M1 | survey |
| F03 | DPI Metadata Scale Extraction | Extracts physical resolution from image header (PNG pHYs chunk, EXIF X/YResolution) to `mm_per_px = 25.4 / DPI`. | M1 | survey |
| F04 | Tailoring Text Dimension Parser | Parses Spanish, Polish, English annotation strings for dimensions (`W x H`), decimals (`12,5`, `19.5`), multipliers (`x1`, `x2`), keywords (`doblez`, `elastizar`). | M1 | survey |
| F05 | Anisotropic Piece Calibration | Wraps `src/core/geometry.py:calibrate_piece_anisotropic` to scale pieces independently along X and Y axes to match declared dimensions ($W_{\text{target}}, H_{\text{target}}$). | M1 | survey |
| F06 | Raster Segmentation & Background Suppression | Grayscale/HSV thresholding, morphological noise filtering, and contour extraction separating pieces from background/watermark on `samples/gorro_trapper.png`. | M2 | survey |
| F07 | Sharp Corner / Vertex Detection | Identifies geometric corners along pixel contours using turning-angle curvature analysis ($k=5$ px, angle $\ge 35^\circ$), partitioning contour into smooth edge chains. | M2 | survey |
| F08 | Schneider Cubic Bézier Curve Fitting | Optimal least-squares cubic Bézier curve fitting using chord parameterization, end-tangent estimation, Newton-Raphson parameter tuning, and recursive subdivision at max error ($< 0.5$ mm). | M2 | survey |
| F09 | Collinear Straight Edge Detection | Identifies point chains whose deviation from secant chord is below threshold ($< 0.3$ mm), outputting `PathSegment.line` directly. | M2 | survey |
| F10 | Seam Allowance Mode: `"net_only"` | Treats digitized contour as finished sewing line (`outer_contour`), generating outer cut boundary (`cut_contour`) via Seam Allowance Engine with outward offset. | M3 | survey |
| F11 | Seam Allowance Mode: `"included"` | Treats digitized contour as cut line (`cut_contour`), deriving inner sewing line (`outer_contour`) via inward offset. | M3 | survey |
| F12 | Seam Allowance Mode: `"dual_detected"` | Detects and pairs concentric contours from raster images with pre-drawn double lines (solid cut line + dashed sewing line). | M3 | survey |
| F13 | Fold Edge Detection & Annotation | Identifies fold edges from text cues (`"doblez"`, `"fold"`, `"lbf"`) or symmetry axes, assigning `edge_role="fold"` and `seam_allowance_mm=0.0`. | M3 | survey |
| F14 | Configurable Auto-Unfold | Supports both CAD-native fold export (`auto_unfold=False`, fold edge on AAMA Layer 5) and full 1:1 unfolded symmetrical piece (`auto_unfold=True`, via `unfold_piece_on_fold`). | M3 | survey |
| F15 | Internal Cuts, Darts & Slits Classification | Isolates internal strokes/markings inside piece boundary (e.g., red V-darts and horizontal 8cm line on *Orejera Compuesta*) and maps to `InternalLine` on AAMA Layer 4. | M3 | survey |
| F16 | Tailoring Notch Detection | Detects alignment ticks along contour, calculates unit normal direction $\vec{n} = (-t_y, t_x)$, and maps to `Notch` objects on AAMA Layer 2. | M3 | survey |
| F17 | End-to-End Digitizer Pipeline | Coordinates loading raster image $\to$ scale recovery $\to$ vectorization $\to$ semantic classification $\to$ `PatternProject` generation. | M4 | survey |
| F18 | Visual Diagnostic Suite | Generates 5 high-resolution diagnostic images in `output/stage2_digitizer/` visualizing segmentation masks, corner vertices, Bézier control polygons, semantic features, and calibrated rulers. | M4 | survey |
| F19 | Clo3D DXF-AAMA Dual-Contour Export | Exports digitized project to AutoCAD R12 DXF with AAMA Layers: 1 (Cut), 2 (Notches), 4 (Internal/Sewing line), 5 (Fold axis), 7 (Grainline), 8 (Text). | M4 | survey |
| F20 | A4 Multi-Page Tiler Integration | Routes digitized pieces into `src/nesting/a4_nester.py` and `src/exporters/a4_tiler_pdf.py` for Hybrid & Poster home printing with 50x50 mm calibration square. | M4 | survey |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Multi-Method Scale Recovery Engine | F01, F02, F03, F04, F05 (`src/digitizer/scale_recovery.py`) | none | DONE |
| M2 | Raster Segmentation & Schneider Bézier Vectorizer | F06, F07, F08, F09 (`src/digitizer/vectorizer.py`) | M1 | DONE |
| M3 | Tailoring Line & Semantic Feature Classifier | F10, F11, F12, F13, F14, F15, F16 (`src/digitizer/semantic_classifier.py`) | M2 | DONE |
| M4 | End-to-End Pipeline, CLI Runner, Overlays, Tests & Docs | F17, F18, F19, F20 (`src/digitizer/pipeline.py`, `run_digitizer.py`, `output/stage2_digitizer/`, `tests/test_stage2_digitizer.py`, `README.md`) | M3 | DONE |
| M5 | Comprehensive Verification, Dual-Track E2E & Hardening | Full suite pass (83 existing + Stage 2 digitizer tests, 0 failures), forensic audit, sample image validation | M4 | DONE |

## Interface Contracts

### `src/digitizer/scale_recovery.py`
```python
from typing import Literal, Optional, Tuple, Dict, Any
from pydantic import BaseModel, Field
from src.core.contract import Point2D, PatternPiece

class ScaleResult(BaseModel):
    mm_per_px_x: float = Field(gt=0.0)
    mm_per_px_y: float = Field(gt=0.0)
    is_isotropic: bool = Field(default=True)
    calibration_method: Literal[
        "dimension_anisotropic",
        "two_point_reference",
        "calibration_square",
        "dpi_metadata",
        "fallback_default",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def mm_per_px(self) -> float:
        return 0.5 * (self.mm_per_px_x + self.mm_per_px_y)

def calibrate_from_two_points(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    real_distance_cm: float
) -> ScaleResult: ...

def detect_calibration_square(
    image: Any, # np.ndarray
    target_square_mm: float = 50.0
) -> Optional[ScaleResult]: ...

def extract_dpi_scale(
    image_path_or_bytes: Any
) -> Optional[ScaleResult]: ...

def parse_tailoring_text(
    text: str
) -> Dict[str, Any]: ...

def calibrate_piece_by_dimensions(
    piece: PatternPiece,
    target_width_cm: float,
    target_height_cm: float
) -> PatternPiece: ...
```

### `src/digitizer/vectorizer.py`
```python
from typing import List, Tuple, Optional
import numpy as np
from src.core.contract import Point2D, PathSegment, Contour

def segment_pattern_pieces(
    image: np.ndarray,
    min_area_px: float = 1000.0,
    hsv_tan_filter: bool = True
) -> List[np.ndarray]: ...

def detect_corners(
    contour_pts: np.ndarray,
    angle_thresh_deg: float = 35.0,
    window_k: int = 5
) -> List[int]: ...

def fit_cubic_bezier(
    points: np.ndarray,
    max_error_mm: float = 0.5,
    max_subdivision: int = 8,
    collinear_tol_mm: float = 0.3
) -> List[PathSegment]: ...

def vectorize_contour(
    contour_pts: np.ndarray,
    scale: ScaleResult,
    max_error_mm: float = 0.5
) -> Contour: ...
```

### `src/digitizer/semantic_classifier.py`
```python
from typing import List, Optional
from src.core.contract import PatternPiece, Contour, InternalLine, Notch

def classify_seam_mode(
    piece: PatternPiece,
    mode: Literal["net_only", "included", "dual_detected"],
    seam_allowance_mm: float = 10.0,
    inner_contour: Optional[Contour] = None
) -> PatternPiece: ...

def detect_fold_edge(
    piece: PatternPiece,
    text_hint: Optional[str] = None
) -> PatternPiece: ...

def apply_auto_unfold(
    piece: PatternPiece,
    auto_unfold: bool = False
) -> PatternPiece: ...

def extract_internal_features(
    image: np.ndarray,
    piece: PatternPiece,
    scale: ScaleResult
) -> PatternPiece: ...

def detect_notches(
    piece: PatternPiece,
    tick_candidates: List[Any]
) -> PatternPiece: ...
```

### `src/digitizer/pipeline.py`
```python
from typing import Optional, Literal
from pathlib import Path
from src.core.contract import PatternProject

class DigitizerPipeline:
    def __init__(
        self,
        scale_method: Literal["dimension_anisotropic", "two_point_reference", "calibration_square", "dpi_metadata", "auto"] = "auto",
        seam_mode: Literal["net_only", "included", "dual_detected"] = "net_only",
        auto_unfold: bool = False,
        global_seam_mm: float = 10.0,
        output_dir: Optional[Path] = None,
    ): ...

    def process_image(
        self,
        image_path: Path,
        declared_dimensions: Optional[dict] = None
    ) -> PatternProject: ...
```

## Code Layout
- `src/digitizer/`: Stage 2 implementation package
  - `src/digitizer/__init__.py`: Package exports
  - `src/digitizer/scale_recovery.py`: Scale recovery engine (Scenarios A, B, C, text parser)
  - `src/digitizer/vectorizer.py`: Segmentation, corner detection, Schneider Bézier curve fitter
  - `src/digitizer/semantic_classifier.py`: Seam modes, fold detection, auto-unfold, internal lines, notches
  - `src/digitizer/pipeline.py`: End-to-end pipeline & diagnostic overlay generator
- `run_digitizer.py`: CLI executable interface for Stage 2
- `output/stage2_digitizer/`: Diagnostic visualization output directory
- `tests/test_stage2_digitizer.py`: Comprehensive test suite for Stage 2
- `README.md`: Documentation updates detailing Stage 2 usage, architecture, and CLI options
