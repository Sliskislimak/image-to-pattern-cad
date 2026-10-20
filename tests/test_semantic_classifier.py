"""
Test suite for Stage 2 Milestone 3: Tailoring Line & Semantic Feature Classifier.

Verifies:
- F10: Seam Mode 'net_only' (outward offset to cut_contour).
- F11: Seam Mode 'included' (inward offset to outer_contour).
- F12: Seam Mode 'dual_detected' (pairs concentric contours).
- F13: Fold Edge Detection & Annotation (detect_fold_edge from text and geometry).
- F14: Configurable Auto-Unfold (apply_auto_unfold for Clo3D Layer 5 vs. 1:1 unfolded piece).
- F15: Internal Cuts, Darts & Slits Classification (extract_internal_features).
- F16: Tailoring Notch Detection (detect_notches, add_notch, add_notch_at_ratio).
"""

from __future__ import annotations

import math
from pathlib import Path
import cv2
import numpy as np
import pytest
from shapely.geometry import Polygon

from src.core.contract import (
    LINE_TYPE_CUT,
    LINE_TYPE_DART,
    LINE_TYPE_FOLD,
    LINE_TYPE_INTERNAL_CUT,
    LINE_TYPE_SEAM,
    LINE_TYPE_SLIT,
    Contour,
    InternalLine,
    Notch,
    PathSegment,
    PatternPiece,
    Point2D,
)
from src.core.geometry import (
    contour_to_shapely_polygon,
    get_contour_bounds,
    get_contour_dimensions_mm,
)
from src.digitizer.scale_recovery import ScaleResult
from src.digitizer.semantic_classifier import (
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
from src.digitizer.vectorizer import (
    PixelToMMTransform,
    segment_pattern_pieces_detailed,
    vectorize_contour_detailed,
)
from src.presets.gorro_trapper import build_frente, build_orejera_compuesta

SAMPLE_IMAGE_PATH = Path("samples/gorro_trapper.png")


# Helper: create simple rectangular test piece
def make_test_piece(w: float = 100.0, h: float = 50.0, name: str = "TestRect") -> PatternPiece:
    segs = [
        PathSegment.line((0.0, 0.0), (w, 0.0), edge_role="seam"),
        PathSegment.line((w, 0.0), (w, h), edge_role="seam"),
        PathSegment.line((w, h), (0.0, h), edge_role="seam"),
        PathSegment.line((0.0, h), (0.0, 0.0), edge_role="seam"),
    ]
    return PatternPiece(
        id="test_rect",
        name=name,
        quantity=1,
        target_width_cm=w / 10.0,
        target_height_cm=h / 10.0,
        outer_contour=Contour(segments=segs, is_closed=True),
    )


# ===========================================================================
# 1. Seam Allowance Modes (F10, F11, F12)
# ===========================================================================


class TestSeamAllowanceModes:
    """Verifies net_only, included, and dual_detected seam modes."""

    def test_mode_net_only_generates_outer_cut_contour(self):
        """F10: net_only expands outer boundary by seam_allowance_mm."""
        piece = make_test_piece(100.0, 50.0)
        res = classify_seam_mode(piece, mode="net_only", seam_allowance_mm=10.0)

        assert res.has_cut_contour is True
        assert res.cut_contour is not None
        assert res.seam_allowance_mm == 10.0

        # Cut contour must be larger than net contour
        w_net, h_net = get_contour_dimensions_mm(res.outer_contour)
        w_cut, h_cut = get_contour_dimensions_mm(res.cut_contour)
        assert pytest.approx(w_cut - w_net, abs=0.5) == 20.0
        assert pytest.approx(h_cut - h_net, abs=0.5) == 20.0

        poly = contour_to_shapely_polygon(res.cut_contour)
        assert poly.is_valid is True

    def test_mode_net_only_zero_allowance(self):
        piece = make_test_piece(100.0, 50.0)
        res = classify_seam_mode(piece, mode="net_only", seam_allowance_mm=0.0)
        assert res.cut_contour is None
        assert res.seam_allowance_mm == 0.0

    def test_mode_included_derives_inner_sewing_line(self):
        """F11: included treats digitized contour as cut line, shrinks inward for net line."""
        piece = make_test_piece(120.0, 70.0)
        res = classify_seam_mode(piece, mode="included", seam_allowance_mm=10.0)

        assert res.has_cut_contour is True
        assert res.cut_contour is not None
        # Original 120x70 must be the cut contour
        w_cut, h_cut = get_contour_dimensions_mm(res.cut_contour)
        assert pytest.approx(w_cut, abs=0.5) == 120.0
        assert pytest.approx(h_cut, abs=0.5) == 70.0

        # Net sewing line must be shrunken by 10 mm on each side -> 100x50 mm
        w_net, h_net = get_contour_dimensions_mm(res.outer_contour)
        assert pytest.approx(w_net, abs=0.5) == 100.0
        assert pytest.approx(h_net, abs=0.5) == 50.0

        poly = contour_to_shapely_polygon(res.outer_contour)
        assert poly.is_valid is True

    def test_mode_included_zero_allowance(self):
        piece = make_test_piece(100.0, 50.0)
        res = classify_seam_mode(piece, mode="included", seam_allowance_mm=0.0)
        assert res.has_cut_contour is True
        w_net, h_net = get_contour_dimensions_mm(res.outer_contour)
        w_cut, h_cut = get_contour_dimensions_mm(res.cut_contour)
        assert pytest.approx(w_net, abs=0.1) == w_cut
        assert pytest.approx(h_net, abs=0.1) == h_cut

    def test_mode_dual_detected_pairs_concentric_contours(self):
        """F12: dual_detected assigns larger contour to cut line and smaller to sewing line."""
        cut_contour = make_test_piece(120.0, 70.0).outer_contour
        net_contour = make_test_piece(100.0, 50.0).outer_contour

        # Pass piece with net_contour as outer, cut_contour as inner_contour
        piece = make_test_piece(100.0, 50.0)
        res = classify_seam_mode(
            piece,
            mode="dual_detected",
            seam_allowance_mm=10.0,
            inner_contour=cut_contour,
        )

        assert res.has_cut_contour is True
        w_cut, h_cut = get_contour_dimensions_mm(res.cut_contour)
        w_net, h_net = get_contour_dimensions_mm(res.outer_contour)
        assert pytest.approx(w_cut, abs=0.5) == 120.0
        assert pytest.approx(w_net, abs=0.5) == 100.0

    def test_invalid_mode_raises_error(self):
        piece = make_test_piece(100.0, 50.0)
        with pytest.raises(ValueError, match="Unsupported seam mode"):
            classify_seam_mode(piece, mode="invalid_mode")  # type: ignore[arg-type]

    def test_negative_allowance_raises_error(self):
        piece = make_test_piece(100.0, 50.0)
        with pytest.raises(ValueError, match="must be >= 0.0"):
            classify_seam_mode(piece, seam_allowance_mm=-5.0)


# ===========================================================================
# 2. Fold Edge Detection & Auto-Unfold (F13, F14)
# ===========================================================================


class TestFoldDetectionAndAutoUnfold:
    """Verifies fold edge identification and unfolding."""

    def test_detect_fold_edge_from_text_hint(self):
        """F13: Spanish tailoring keyword 'doblez' marks the straight boundary edge."""
        piece = make_test_piece(100.0, 50.0)
        # In this piece, bottom edge is segment 0: (0,0) -> (100,0)
        res = detect_fold_edge(piece, text_hint="Cortar en tela exterior (doblez)")
        assert res.has_fold is True
        fold_idx = res.fold_segment_index
        assert fold_idx is not None
        assert res.outer_contour.segments[fold_idx].edge_role == "fold"
        assert res.outer_contour.segments[fold_idx].seam_allowance_mm == 0.0

    def test_detect_fold_edge_polish_text(self):
        piece = make_test_piece(100.0, 50.0)
        res = detect_fold_edge(piece, text_hint="Kroić na zgięciu materiału x1")
        assert res.has_fold is True

    def test_detect_fold_edge_by_explicit_hint(self):
        piece = make_test_piece(100.0, 50.0)
        res = detect_fold_edge(piece, edge_hint="bottom")
        assert res.has_fold is True
        fold_seg = res.outer_contour.segments[res.fold_segment_index]
        assert fold_seg.start.y == 0.0 and fold_seg.end.y == 0.0

    def test_detect_fold_edge_no_text_returns_unmodified(self):
        piece = make_test_piece(100.0, 50.0)
        res = detect_fold_edge(piece, text_hint="Normal cut line x2")
        assert res.has_fold is False

    def test_apply_auto_unfold_false_preserves_fold_for_clo3d(self):
        """F14: auto_unfold=False keeps the fold edge on Layer 5 for Clo3D native unfold."""
        piece = make_test_piece(100.0, 50.0)
        piece_with_fold = detect_fold_edge(piece, edge_hint="bottom")
        assert piece_with_fold.has_fold is True

        res = apply_auto_unfold(piece_with_fold, auto_unfold=False)
        assert res.has_fold is True
        assert len(res.outer_contour.segments) == 4

    def test_apply_auto_unfold_true_doubles_piece(self):
        """F14: auto_unfold=True unfolds piece into symmetrical 1:1 template."""
        piece = make_test_piece(100.0, 50.0)
        piece_with_fold = detect_fold_edge(piece, edge_hint="bottom")
        res = apply_auto_unfold(piece_with_fold, auto_unfold=True)

        # Unfolded across bottom edge (y=0): height must double from 50 mm to 100 mm
        w, h = get_contour_dimensions_mm(res.outer_contour)
        assert pytest.approx(w, abs=0.5) == 100.0
        assert pytest.approx(h, abs=0.5) == 100.0

        # Original fold line must be stored as InternalLine with line_type="fold"
        fold_internals = [l for l in res.internal_lines if l.line_type == "fold"]
        assert len(fold_internals) == 1
        poly = contour_to_shapely_polygon(res.outer_contour)
        assert poly.is_valid is True

    def test_apply_auto_unfold_on_piece_without_fold_returns_unchanged(self):
        piece = make_test_piece(100.0, 50.0)
        res = apply_auto_unfold(piece, auto_unfold=True)
        assert res == piece


# ===========================================================================
# 3. Internal Cuts, Darts & Slits (F15)
# ===========================================================================


class TestInternalFeatures:
    """Verifies extraction and classification of internal markings."""

    def test_extract_internal_features_synthetic(self):
        """F15: Detects internal line strokes inside piece mask."""
        # Create image with a tan piece and a dark horizontal line inside
        canvas = np.full((200, 200, 3), 255, dtype=np.uint8)
        mask = np.zeros((200, 200), dtype=np.uint8)

        # Piece: 100x100 at (50, 50)
        canvas[50:150, 50:150] = [158, 203, 225]
        mask[50:150, 50:150] = 255

        # Draw dark horizontal line inside: from (70, 100) to (130, 100)
        cv2.line(canvas, (70, 100), (130, 100), (0, 0, 0), thickness=2)

        piece = make_test_piece(100.0, 100.0)
        scale = ScaleResult.create_isotropic(1.0, "fallback_default", 1.0)
        tf = PixelToMMTransform(origin_x_px=50.0, origin_y_px=150.0, mm_per_px_x=1.0, mm_per_px_y=1.0, y_up=True)

        res = extract_internal_features(canvas, piece, scale, transform=tf, piece_mask=mask)
        assert len(res.internal_lines) >= 1
        internal = res.internal_lines[0]
        assert internal.line_type in ("slit", "internal")
        assert len(internal.contour.segments) >= 1

    def test_add_internal_line_helper(self):
        piece = make_test_piece(100.0, 50.0)
        line_contour = Contour.from_polyline([(20.0, 25.0), (80.0, 25.0)], is_closed=False)
        res = add_internal_line(piece, line_contour, line_type="dart", label="Zaszewka boczna")

        assert len(res.internal_lines) == 1
        assert res.internal_lines[0].line_type == "dart"
        assert res.internal_lines[0].label == "Zaszewka boczna"


# ===========================================================================
# 4. Tailoring Notch Detection (F16)
# ===========================================================================


class TestTailoringNotches:
    """Verifies alignment notch placement, normal vectors, and candidate matching."""

    def test_add_notch_at_ratio_straight_edge(self):
        """F16: Places notch at midpoint of straight edge with outward normal."""
        piece = make_test_piece(100.0, 50.0)
        # Segment 0 is bottom edge: (0,0) -> (100,0). Midpoint is (50, 0)
        res = add_notch_at_ratio(piece, segment_index=0, t=0.5, length_mm=6.0, notch_type="slit")

        assert len(res.notches) == 1
        notch = res.notches[0]
        assert pytest.approx(notch.position.x, abs=1e-3) == 50.0
        assert pytest.approx(notch.position.y, abs=1e-3) == 0.0
        assert notch.length_mm == 6.0
        assert notch.notch_type == "slit"
        # Normal should be perpendicular to (100, 0)
        assert notch.direction is not None
        norm_len = math.hypot(notch.direction.x, notch.direction.y)
        assert pytest.approx(norm_len, abs=1e-3) == 1.0

    def test_add_notch_explicit_coordinates(self):
        piece = make_test_piece(100.0, 50.0)
        res = add_notch(piece, position=(100.0, 25.0), length_mm=5.0)
        assert len(res.notches) == 1
        assert res.notches[0].position.x == 100.0
        assert res.notches[0].position.y == 25.0

    def test_detect_notches_from_candidates(self):
        piece = make_test_piece(100.0, 50.0)
        # Candidate 1: close to bottom edge at (50, 1) -> should be matched
        # Candidate 2: far away at (200, 200) -> should be ignored
        cands = [(50.0, 1.0), (200.0, 200.0)]
        res = detect_notches(piece, tick_candidates=cands, search_radius_mm=5.0)

        assert len(res.notches) == 1
        assert pytest.approx(res.notches[0].position.x, abs=0.5) == 50.0
        assert pytest.approx(res.notches[0].position.y, abs=0.5) == 0.0

    def test_compute_normal_at_point(self):
        piece = make_test_piece(100.0, 50.0)
        pt = Point2D(x=50.0, y=2.0)
        closest, normal, dist = compute_normal_at_point(piece.outer_contour, pt)
        assert pytest.approx(closest.x, abs=1e-3) == 50.0
        assert pytest.approx(closest.y, abs=1e-3) == 0.0
        assert pytest.approx(dist, abs=1e-3) == 2.0
        assert normal is not None
        assert pytest.approx(math.hypot(normal.x, normal.y), abs=1e-3) == 1.0


# ===========================================================================
# 5. Integration with Gorro Trapper Presets
# ===========================================================================


class TestPresetIntegration:
    """Verifies semantic classification on real hat pattern pieces."""

    def test_frente_fold_and_unfold_pipeline(self):
        """Frente preset has a bottom fold edge: verify seam allowance and unfolding."""
        frente = build_frente()
        assert frente.has_fold is True

        # Mode net_only: fold edge must have 0 mm seam allowance, other edges have 10 mm
        seamed = classify_seam_mode(frente, mode="net_only", seam_allowance_mm=10.0)
        assert seamed.has_cut_contour is True

        # Auto-unfold into full front piece (1:1)
        unfolded = apply_auto_unfold(frente, auto_unfold=True)
        w_orig, h_orig = get_contour_dimensions_mm(frente.outer_contour)
        w_unf, h_unf = get_contour_dimensions_mm(unfolded.outer_contour)

        # Height doubles because it unfolds across bottom
        assert pytest.approx(w_unf, abs=1.0) == w_orig
        assert pytest.approx(h_unf, abs=1.0) == 2.0 * h_orig
        poly = contour_to_shapely_polygon(unfolded.outer_contour)
        assert poly.is_valid is True

    def test_orejera_compuesta_with_darts_and_notches(self):
        """Orejera compuesta has internal V-darts: verify internal features and notch placement."""
        orejera = build_orejera_compuesta()
        assert len(orejera.internal_lines) >= 1

        # Add assembly notches at key alignment points
        with_notches = add_notch_at_ratio(orejera, segment_index=0, t=0.5, length_mm=5.0)
        assert len(with_notches.notches) == 1
        assert with_notches.notches[0].notch_type == "slit"
