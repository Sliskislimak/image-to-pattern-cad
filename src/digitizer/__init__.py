"""
Digitizer package for Image-to-Pattern CAD Stage 2.
"""

from src.digitizer.scale_recovery import (
    CalibrationMethod,
    PointInput,
    ScaleResult,
    auto_detect_scale,
    calibrate_from_two_points,
    calibrate_piece_by_dimensions,
    compute_anisotropic_scale,
    detect_calibration_square,
    extract_dpi_scale,
    get_fallback_scale,
    parse_tailoring_text,
    scale_contour,
)
from src.digitizer.vectorizer import (
    PixelToMMTransform,
    SegmentedPiece,
    VectorizationResult,
    build_piece_mask,
    detect_corners,
    fit_cubic_bezier,
    load_image,
    max_deviation_to_contour,
    resample_closed_polyline,
    segment_pattern_pieces,
    segment_pattern_pieces_detailed,
    vectorize_contour,
    vectorize_contour_detailed,
)
from src.digitizer.semantic_classifier import (
    FoldEdgeHint,
    SeamMode,
    add_internal_line,
    add_notch,
    add_notch_at_ratio,
    apply_auto_unfold,
    classify_seam_mode,
    compute_normal_at_point,
    detect_fold_edge,
    detect_notches,
    extract_internal_features,
)
from src.digitizer.pipeline import (
    DigitizerPipeline,
    generate_diagnostic_overlays,
)

__all__ = [
    # Scale Recovery (Milestone 1)
    "CalibrationMethod",
    "PointInput",
    "ScaleResult",
    "calibrate_from_two_points",
    "detect_calibration_square",
    "extract_dpi_scale",
    "get_fallback_scale",
    "auto_detect_scale",
    "parse_tailoring_text",
    "compute_anisotropic_scale",
    "calibrate_piece_by_dimensions",
    "scale_contour",
    # Vectorizer (Milestone 2)
    "SegmentedPiece",
    "PixelToMMTransform",
    "VectorizationResult",
    "load_image",
    "build_piece_mask",
    "segment_pattern_pieces",
    "segment_pattern_pieces_detailed",
    "resample_closed_polyline",
    "detect_corners",
    "fit_cubic_bezier",
    "vectorize_contour",
    "vectorize_contour_detailed",
    "max_deviation_to_contour",
    # Semantic Classifier (Milestone 3)
    "SeamMode",
    "FoldEdgeHint",
    "classify_seam_mode",
    "detect_fold_edge",
    "apply_auto_unfold",
    "extract_internal_features",
    "detect_notches",
    "add_notch",
    "add_notch_at_ratio",
    "add_internal_line",
    "compute_normal_at_point",
    # Pipeline & Diagnostics (Milestone 4)
    "DigitizerPipeline",
    "generate_diagnostic_overlays",
]


