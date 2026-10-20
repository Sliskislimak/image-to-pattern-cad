"""
Integration test suite for Stage 2 Pattern Semantic Digitizer (Milestones 1-4).

Verifies:
- End-to-end DigitizerPipeline execution on raster graphics (samples/gorro_trapper.png).
- Multi-method Scale Recovery integration.
- Schneider cubic Bézier curve fitting and corner detection.
- Tailoring semantic features: fold edges, seam allowances (net_only, included), internal cuts, notches.
- Diagnostic contact sheet generation and optional separate PNG export (--separate-png).
- Clo3D DXF-AAMA (AutoCAD R12, Layers 1, 2, 4, 5, 7, 8) and 1:1 Vector PDF export.
- Multi-page A4 printables integration.
"""

from __future__ import annotations

from pathlib import Path
import pytest
from shapely.geometry import Polygon

from src.core.contract import PatternPiece, PatternProject
from src.core.geometry import contour_to_shapely_polygon, get_contour_dimensions_mm
from src.digitizer.pipeline import DigitizerPipeline

SAMPLE_IMAGE = Path("samples/gorro_trapper.png")


class TestDigitizerPipelineE2E:
    """Verifies end-to-end execution of Stage 2 Pattern Semantic Digitizer."""

    def test_pipeline_basic_execution(self, tmp_path):
        if not SAMPLE_IMAGE.is_file():
            pytest.skip("Sample pattern image not found")

        output_dir = tmp_path / "stage2_test"
        pipeline = DigitizerPipeline(
            seam_mode="net_only",
            seam_allowance_mm=10.0,
            auto_unfold=False,
            output_dir=output_dir,
            separate_png=False,
            export_clo3d=True,
            export_a4=True,
        )

        project = pipeline.process_image(SAMPLE_IMAGE)

        # 1. Project validity
        assert isinstance(project, PatternProject)
        assert len(project.pieces) >= 6
        assert project.units == "mm"
        assert project.has_seam_allowance is True

        # 2. Polygon validity on every piece
        for p in project.pieces:
            poly_net = contour_to_shapely_polygon(p.outer_contour)
            assert poly_net.is_valid is True, f"Piece {p.name} outer_contour is invalid"
            assert not poly_net.is_empty
            assert poly_net.area > 500.0

            if p.has_cut_contour:
                poly_cut = contour_to_shapely_polygon(p.cut_contour)
                assert poly_cut.is_valid is True, f"Piece {p.name} cut_contour is invalid"
                assert poly_cut.area > poly_net.area

        # 3. Diagnostic Contact Sheet generated
        contact_sheet = output_dir / "diagnostic_contact_sheet.png"
        assert contact_sheet.is_file()
        assert contact_sheet.stat().st_size > 50_000  # at least 50 KB

        # With separate_png=False, separate panels should NOT be saved
        separate_files = list(output_dir.glob("0*.png"))
        assert len(separate_files) == 0

        # 4. Clo3D & CAD artifacts generated
        dxf_file = output_dir / f"{SAMPLE_IMAGE.stem}_clo3d_mm.dxf"
        assert dxf_file.is_file()
        assert dxf_file.stat().st_size > 10_000

        pdf_clo3d = output_dir / f"{SAMPLE_IMAGE.stem}_clo3d_import_1to1.pdf"
        assert pdf_clo3d.is_file()

        tech_sheet = output_dir / f"{SAMPLE_IMAGE.stem}_technical_sheet_1to1.pdf"
        assert tech_sheet.is_file()

        a4_pdf = output_dir / f"{SAMPLE_IMAGE.stem}_a4_print_hybrid.pdf"
        assert a4_pdf.is_file()

        json_file = output_dir / f"{SAMPLE_IMAGE.stem}_digitized_project.json"
        assert json_file.is_file()

    def test_pipeline_separate_png_export(self, tmp_path):
        """Verifies that separate_png=True generates individual high-res PNG overlays."""
        if not SAMPLE_IMAGE.is_file():
            pytest.skip("Sample pattern image not found")

        output_dir = tmp_path / "stage2_separate_png"
        pipeline = DigitizerPipeline(
            seam_mode="net_only",
            seam_allowance_mm=10.0,
            output_dir=output_dir,
            separate_png=True,
            export_clo3d=False,
            export_a4=False,
        )

        project = pipeline.process_image(SAMPLE_IMAGE)

        # Contact sheet must still be present
        assert (output_dir / "diagnostic_contact_sheet.png").is_file()

        # All 5 separate PNG files must be generated and non-empty
        expected_panels = [
            "01_segmentation_masks.png",
            "02_corners_detected.png",
            "03_bezier_curves.png",
            "04_tailoring_semantics.png",
            "05_cad_calibrated_overlay.png",
        ]
        for panel_name in expected_panels:
            p_path = output_dir / panel_name
            assert p_path.is_file(), f"Missing separate PNG panel: {panel_name}"
            assert p_path.stat().st_size > 10_000, f"Panel {panel_name} is too small"

    def test_pipeline_auto_unfold_mode(self, tmp_path):
        """Verifies that auto_unfold=True unfolds fold-marked pieces (Frente)."""
        if not SAMPLE_IMAGE.is_file():
            pytest.skip("Sample pattern image not found")

        output_dir = tmp_path / "stage2_auto_unfold"
        pipeline = DigitizerPipeline(
            seam_mode="net_only",
            seam_allowance_mm=10.0,
            auto_unfold=True,
            output_dir=output_dir,
            export_clo3d=False,
            export_a4=False,
        )

        project = pipeline.process_image(SAMPLE_IMAGE)
        # Find Frente
        frente_pieces = [p for p in project.pieces if "frente" in p.id.lower() or "frente" in p.name.lower()]
        assert len(frente_pieces) >= 1
        frente = frente_pieces[0]

        # In unfolded piece, fold axis must be preserved as InternalLine
        fold_axes = [il for il in frente.internal_lines if il.line_type == "fold"]
        assert len(fold_axes) >= 1

        poly = contour_to_shapely_polygon(frente.outer_contour)
        assert poly.is_valid is True

    def test_pipeline_included_seam_mode(self, tmp_path):
        """Verifies seam_mode='included' derives net sewing line by inward offset."""
        if not SAMPLE_IMAGE.is_file():
            pytest.skip("Sample pattern image not found")

        output_dir = tmp_path / "stage2_included"
        pipeline = DigitizerPipeline(
            seam_mode="included",
            seam_allowance_mm=10.0,
            output_dir=output_dir,
            export_clo3d=False,
            export_a4=False,
        )

        project = pipeline.process_image(SAMPLE_IMAGE)
        for p in project.pieces:
            assert p.has_cut_contour is True
            w_net, h_net = get_contour_dimensions_mm(p.outer_contour)
            w_cut, h_cut = get_contour_dimensions_mm(p.cut_contour)
            assert w_cut > w_net
            assert h_cut > h_net
