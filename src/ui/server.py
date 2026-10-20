"""
FastAPI Backend Server & REST API for Image-to-Pattern CAD Studio.

Provides endpoints for:
- Project session management and presets loading
- Raster pattern image upload & automatic digitization pipeline
- Real-time tailoring modifications (1-click unfold, mirror, edge role assignment, seam allowance recalculation)
- 2D nesting calculation for A4 home printing (Hybrid & Poster modes)
- Production exports: DXF-AAMA (AutoCAD R12), Clo3D 1:1 Vector PDF, Technical Sheet, and A4 Tiled PDF
- Static single-page CAD Studio hosting
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.core.contract import (
    EdgeRole,
    InternalLine,
    Notch,
    PathSegment,
    PatternPiece,
    PatternProject,
    Point2D,
)
from src.core.geometry import (
    calibrate_piece_anisotropic,
    get_contour_dimensions_mm,
    mirror_piece_x,
    unfold_piece_on_fold,
)
from src.core.seam_allowance import (
    apply_seam_allowance_to_piece,
    apply_seam_allowance_to_project,
)
from src.digitizer.pipeline import DigitizerPipeline
from src.exporters.a4_tiler_pdf import export_a4_pdf
from src.exporters.dxf_exporter import export_clo3d_dxf
from src.exporters.pdf_exporter import (
    export_clo3d_vector_pdf_1to1,
    export_technical_sheet_pdf_1to1,
)
from src.nesting.a4_nester import nest_hybrid_mode, nest_poster_mode
from src.presets.gorro_trapper import create_gorro_trapper_project

logger = logging.getLogger("pattern_cad_server")


# ===========================================================================
# Request & Response Schemas
# ===========================================================================


class SeamAllowanceRequest(BaseModel):
    seam_allowance_mm: float = Field(ge=0.0, le=50.0, default=10.0)


class GlobalSeamRequest(BaseModel):
    seam_allowance_mm: float = Field(ge=0.0, le=50.0, default=10.0)


class SegmentRoleRequest(BaseModel):
    edge_role: EdgeRole = Field(default="seam")
    seam_allowance_mm: Optional[float] = Field(default=None, ge=0.0, le=50.0)


class AnisotropicScaleRequest(BaseModel):
    target_width_cm: float = Field(gt=0.0)
    target_height_cm: float = Field(gt=0.0)


class QuantityRequest(BaseModel):
    quantity: int = Field(ge=1, le=10, default=1)


class NestingCalculateRequest(BaseModel):
    mode: Literal["hybrid", "poster"] = Field(default="hybrid")
    page_w_mm: float = Field(default=210.0)
    page_h_mm: float = Field(default=297.0)
    margin_mm: float = Field(default=10.0)
    overlap_mm: float = Field(default=15.0)


# ===========================================================================
# Project State Manager
# ===========================================================================


class ProjectSession:
    """In-memory active project state manager for the interactive CAD session."""

    def __init__(self) -> None:
        self.project: PatternProject = create_gorro_trapper_project()
        self.latest_diagnostic_sheet: Optional[Path] = None
        self._check_initial_artifacts()

    def _check_initial_artifacts(self) -> None:
        default_contact = Path("output/stage2_digitizer/diagnostic_contact_sheet.png")
        if default_contact.is_file():
            self.latest_diagnostic_sheet = default_contact.resolve()

    def reset_to_preset(self, preset_name: str) -> PatternProject:
        if preset_name in ("gorro_trapper_cad", "default"):
            self.project = create_gorro_trapper_project()
        elif preset_name == "gorro_trapper_digitized":
            cached_json = Path("output/stage2_digitizer/gorro_trapper_digitized_project.json")
            if cached_json.is_file():
                self.project = PatternProject.load_json(cached_json)
            else:
                # Fallback to base
                self.project = create_gorro_trapper_project()
        else:
            raise ValueError(f"Nieznany wzorzec: {preset_name}")
        return self.project

    def find_piece(self, piece_id: str) -> Tuple[int, PatternPiece]:
        for idx, p in enumerate(self.project.pieces):
            if p.id == piece_id:
                return idx, p
        raise KeyError(f"Element o ID '{piece_id}' nie istnieje w projekcie.")


# Global session instance
session = ProjectSession()


# ===========================================================================
# Application Factory
# ===========================================================================


def create_app() -> FastAPI:
    """Creates and configures the FastAPI application instance."""
    app = FastAPI(
        title="Image-to-Pattern CAD Studio API",
        version="1.0.0",
        description="Backend API and Interactive Web CAD Studio for sewing pattern digitization, seam engineering, and 1:1 export.",
    )

    # CORS configuration for development flexibility
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -----------------------------------------------------------------------
    # System & Presets Endpoints
    # -----------------------------------------------------------------------

    @app.get("/api/health")
    def get_health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "version": "1.0.0",
            "project_name": session.project.project_name,
            "pieces_count": len(session.project.pieces),
            "has_seam_allowances": session.project.has_seam_allowance,
        }

    @app.get("/api/presets")
    def get_presets() -> List[Dict[str, Any]]:
        return [
            {
                "id": "gorro_trapper_cad",
                "name": "Gorro Trapper con Tapabocas (Wzorzec Matematyczny CAD)",
                "description": "Precyzyjny referencyjny wykrój czapki zimowej z maską (6 elementów bazowych, role krawędzi fold/elastic).",
                "pieces_count": 6,
            },
            {
                "id": "gorro_trapper_digitized",
                "name": "Gorro Trapper con Tapabocas (Zdygitalizowany z PNG)",
                "description": "Wykrój z automatyczną percepcją rastra, wektoryzacją Schneidera i dopasowaniem skali z pliku samples/gorro_trapper.png.",
                "pieces_count": len(session.project.pieces),
            },
        ]

    @app.post("/api/presets/{preset_id}/load")
    def load_preset(preset_id: str) -> Dict[str, Any]:
        try:
            proj = session.reset_to_preset(preset_id)
            return {
                "status": "success",
                "message": f"Załadowano wzorzec: {preset_id}",
                "project": proj.model_dump(),
            }
        except ValueError as err:
            raise HTTPException(status_code=404, detail=str(err))

    # -----------------------------------------------------------------------
    # Project Endpoints
    # -----------------------------------------------------------------------

    @app.get("/api/project")
    def get_project() -> Dict[str, Any]:
        return session.project.model_dump()

    @app.post("/api/project")
    def update_project(project_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            session.project = PatternProject.model_validate(project_data)
            return {"status": "success", "project": session.project.model_dump()}
        except Exception as err:
            raise HTTPException(status_code=400, detail=f"Błąd walidacji kontraktu: {err}")

    @app.post("/api/project/global-seam")
    def set_global_seam(req: GlobalSeamRequest) -> Dict[str, Any]:
        session.project = apply_seam_allowance_to_project(
            session.project,
            global_seam_allowance_mm=req.seam_allowance_mm,
        )
        return {
            "status": "success",
            "message": f"Zastosowano globalny naddatek na szew {req.seam_allowance_mm:.1f} mm.",
            "project": session.project.model_dump(),
        }

    # -----------------------------------------------------------------------
    # Pattern Piece Tailoring Operations
    # -----------------------------------------------------------------------

    @app.post("/api/pieces/{piece_id}/unfold")
    def unfold_piece_endpoint(piece_id: str) -> Dict[str, Any]:
        try:
            idx, piece = session.find_piece(piece_id)
            unfolded = unfold_piece_on_fold(piece).model_copy(update={"id": piece.id})
            new_pieces = list(session.project.pieces)
            new_pieces[idx] = unfolded
            session.project = session.project.model_copy(update={"pieces": new_pieces})
            return {
                "status": "success",
                "message": f"Pomyślnie rozłożono element '{piece.name}' wzdłuż linii zgięcia.",
                "piece": unfolded.model_dump(),
            }
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err))
        except Exception as err:
            raise HTTPException(status_code=400, detail=f"Nie można rozłożyć elementu: {err}")

    @app.post("/api/pieces/{piece_id}/mirror")
    def mirror_piece_endpoint(piece_id: str) -> Dict[str, Any]:
        try:
            idx, piece = session.find_piece(piece_id)
            mirrored = mirror_piece_x(piece).model_copy(update={"id": piece.id})
            new_pieces = list(session.project.pieces)
            new_pieces[idx] = mirrored
            session.project = session.project.model_copy(update={"pieces": new_pieces})
            return {
                "status": "success",
                "message": f"Utworzono symetryczne odbicie lustrzane elementu '{piece.name}'.",
                "piece": mirrored.model_dump(),
            }
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err))

    @app.post("/api/pieces/{piece_id}/seam-allowance")
    def set_piece_seam_allowance(piece_id: str, req: SeamAllowanceRequest) -> Dict[str, Any]:
        try:
            idx, piece = session.find_piece(piece_id)
            updated_piece = piece.model_copy(update={"seam_allowance_mm": req.seam_allowance_mm})
            recalculated = apply_seam_allowance_to_piece(
                updated_piece,
                global_seam_allowance_mm=req.seam_allowance_mm,
            )
            new_pieces = list(session.project.pieces)
            new_pieces[idx] = recalculated
            session.project = session.project.model_copy(update={"pieces": new_pieces})
            return {
                "status": "success",
                "message": f"Zaktualizowano naddatek elementu '{piece.name}' do {req.seam_allowance_mm:.1f} mm.",
                "piece": recalculated.model_dump(),
            }
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err))

    @app.post("/api/pieces/{piece_id}/segments/{seg_idx}/role")
    def set_segment_role(
        piece_id: str,
        seg_idx: int,
        req: SegmentRoleRequest,
    ) -> Dict[str, Any]:
        try:
            idx, piece = session.find_piece(piece_id)
            segs = list(piece.outer_contour.segments)
            if seg_idx < 0 or seg_idx >= len(segs):
                raise IndexError(f"Indeks segmentu {seg_idx} poza zakresem [0..{len(segs)-1}].")

            old_seg = segs[seg_idx]
            new_seg = old_seg.model_copy(
                update={
                    "edge_role": req.edge_role,
                    "seam_allowance_mm": req.seam_allowance_mm,
                }
            )
            segs[seg_idx] = new_seg

            new_contour = piece.outer_contour.model_copy(update={"segments": segs})
            updated_piece = piece.model_copy(update={"outer_contour": new_contour})

            # Recalculate cut contour with new edge role
            recalculated = apply_seam_allowance_to_piece(
                updated_piece,
                global_seam_allowance_mm=updated_piece.seam_allowance_mm,
            )
            new_pieces = list(session.project.pieces)
            new_pieces[idx] = recalculated
            session.project = session.project.model_copy(update={"pieces": new_pieces})

            return {
                "status": "success",
                "message": f"Zmieniono rolę segmentu #{seg_idx} na '{req.edge_role}'.",
                "piece": recalculated.model_dump(),
            }
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err))
        except IndexError as err:
            raise HTTPException(status_code=400, detail=str(err))

    @app.post("/api/pieces/{piece_id}/scale-anisotropic")
    def scale_piece_endpoint(piece_id: str, req: AnisotropicScaleRequest) -> Dict[str, Any]:
        try:
            idx, piece = session.find_piece(piece_id)
            piece_with_targets = piece.model_copy(
                update={
                    "target_width_cm": req.target_width_cm,
                    "target_height_cm": req.target_height_cm,
                }
            )
            scaled = calibrate_piece_anisotropic(piece_with_targets)
            # Reapply seam allowance if it was present
            if piece.has_cut_contour or piece.seam_allowance_mm > 0.0:
                scaled = apply_seam_allowance_to_piece(scaled, global_seam_allowance_mm=piece.seam_allowance_mm)

            new_pieces = list(session.project.pieces)
            new_pieces[idx] = scaled
            session.project = session.project.model_copy(update={"pieces": new_pieces})
            return {
                "status": "success",
                "message": f"Przeskalowano element '{piece.name}' do {req.target_width_cm:.1f} x {req.target_height_cm:.1f} cm.",
                "piece": scaled.model_dump(),
            }
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err))

    @app.post("/api/pieces/{piece_id}/quantity")
    def set_piece_quantity(piece_id: str, req: QuantityRequest) -> Dict[str, Any]:
        try:
            idx, piece = session.find_piece(piece_id)
            updated = piece.model_copy(update={"quantity": req.quantity})
            new_pieces = list(session.project.pieces)
            new_pieces[idx] = updated
            session.project = session.project.model_copy(update={"pieces": new_pieces})
            return {
                "status": "success",
                "message": f"Zmieniono ilość sztuk elementu '{piece.name}' na x{req.quantity}.",
                "piece": updated.model_dump(),
            }
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err))

    # -----------------------------------------------------------------------
    # Raster Pattern Digitization Endpoint
    # -----------------------------------------------------------------------

    @app.post("/api/digitize")
    async def digitize_image_endpoint(
        file: UploadFile = File(...),
        seam_mode: Literal["net_only", "included", "dual_detected"] = Query("net_only"),
        seam_allowance_mm: float = Query(10.0),
        auto_unfold: bool = Query(False),
        max_bezier_error_mm: float = Query(0.5),
    ) -> Dict[str, Any]:
        """
        Receives an uploaded pattern image (PNG/JPG), processes it through DigitizerPipeline,
        and replaces the active project with the digitized result.
        """
        suffix = Path(file.filename or "uploaded.png").suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
            raise HTTPException(status_code=400, detail="Obsługiwane formaty graficzne: PNG, JPG, JPEG, BMP, WEBP.")

        output_dir = Path("output/ui_digitizer").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save uploaded bytes to temporary file
        temp_img_path = output_dir / f"upload_{file.filename}"
        try:
            with open(temp_img_path, "wb") as f_out:
                shutil.copyfileobj(file.file, f_out)

            pipeline = DigitizerPipeline(
                scale_method="auto",
                seam_mode=seam_mode,
                seam_allowance_mm=seam_allowance_mm,
                auto_unfold=auto_unfold,
                output_dir=output_dir,
                separate_png=True,
                export_clo3d=True,
                export_a4=True,
                max_bezier_error_mm=max_bezier_error_mm,
            )

            digitized_project = pipeline.process_image(temp_img_path)
            session.project = digitized_project

            contact_path = output_dir / "diagnostic_contact_sheet.png"
            if contact_path.is_file():
                session.latest_diagnostic_sheet = contact_path

            return {
                "status": "success",
                "message": f"Pomyślnie zdygitalizowano {len(digitized_project.pieces)} elementów wykroju.",
                "pieces_count": len(digitized_project.pieces),
                "project": digitized_project.model_dump(),
                "has_diagnostic_sheet": contact_path.is_file(),
            }
        except Exception as err:
            logger.exception("Błąd dygitalizacji obrazu")
            raise HTTPException(status_code=500, detail=f"Błąd podczas dygitalizacji wykroju: {err}")

    # -----------------------------------------------------------------------
    # 2D Nesting & Tiling Preview Endpoint
    # -----------------------------------------------------------------------

    @app.post("/api/nesting/calculate")
    def calculate_nesting(req: NestingCalculateRequest) -> Dict[str, Any]:
        try:
            if req.mode == "hybrid":
                result = nest_hybrid_mode(
                    session.project,
                    page_w_mm=req.page_w_mm,
                    page_h_mm=req.page_h_mm,
                    margin_mm=req.margin_mm,
                    overlap_mm=req.overlap_mm,
                )
            else:
                result = nest_poster_mode(
                    session.project,
                    page_w_mm=req.page_w_mm,
                    page_h_mm=req.page_h_mm,
                    margin_mm=req.margin_mm,
                    overlap_mm=req.overlap_mm,
                )

            # Build serializable response
            pages_data = []
            for p in result.pages:
                pages_data.append(
                    {
                        "page_number": p.page_index + 1,
                        "page_index": p.page_index,
                        "row": p.row,
                        "col": p.col,
                        "total_rows": p.total_rows,
                        "total_cols": p.total_cols,
                        "is_tiled": p.is_tiled,
                        "piece_name": p.piece_name,
                        "placements_count": len(p.placed_pieces),
                        "has_calibration_square": (p.page_index == 0),
                        "placements": [
                            {
                                "piece_name": pl.piece.name,
                                "x_mm": pl.x_mm,
                                "y_mm": pl.y_mm,
                                "rotation_deg": pl.rotation_deg,
                            }
                            for pl in p.placed_pieces
                        ],
                    }
                )

            return {
                "status": "success",
                "mode": result.mode,
                "total_pages": result.total_pages,
                "pages": pages_data,
            }
        except Exception as err:
            raise HTTPException(status_code=500, detail=f"Błąd obliczania nestingu: {err}")

    # -----------------------------------------------------------------------
    # Production Exports (Instant Download)
    # -----------------------------------------------------------------------

    @app.get("/api/export/dxf")
    def export_dxf(
        unit: Literal["mm", "cm"] = Query("mm"),
        expand: bool = Query(False),
    ) -> Response:
        try:
            with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            export_clo3d_dxf(session.project, tmp_path, unit=unit, expand_quantities=expand)
            data = tmp_path.read_bytes()
            try:
                tmp_path.unlink()
            except OSError:
                pass

            filename = f"{session.project.project_name.lower().replace(' ', '_')}_{unit}.dxf"
            return Response(
                content=data,
                media_type="application/dxf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as err:
            raise HTTPException(status_code=500, detail=f"Błąd eksportu DXF: {err}")

    @app.get("/api/export/pdf-clo3d")
    def export_pdf_clo3d(expand: bool = Query(False)) -> Response:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            export_clo3d_vector_pdf_1to1(session.project, tmp_path, expand_quantities=expand)
            data = tmp_path.read_bytes()
            try:
                tmp_path.unlink()
            except OSError:
                pass

            filename = f"{session.project.project_name.lower().replace(' ', '_')}_clo3d_1to1.pdf"
            return Response(
                content=data,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as err:
            raise HTTPException(status_code=500, detail=f"Błąd eksportu Clo3D PDF: {err}")

    @app.get("/api/export/pdf-tech")
    def export_pdf_tech() -> Response:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            export_technical_sheet_pdf_1to1(session.project, tmp_path, expand_quantities=False)
            data = tmp_path.read_bytes()
            try:
                tmp_path.unlink()
            except OSError:
                pass

            filename = f"{session.project.project_name.lower().replace(' ', '_')}_technical_sheet_1to1.pdf"
            return Response(
                content=data,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as err:
            raise HTTPException(status_code=500, detail=f"Błąd eksportu Arkusza Technicznego PDF: {err}")

    @app.get("/api/export/pdf-a4")
    def export_pdf_a4(mode: Literal["hybrid", "poster"] = Query("hybrid")) -> Response:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            if mode == "hybrid":
                nesting_res = nest_hybrid_mode(session.project)
            else:
                nesting_res = nest_poster_mode(session.project)

            export_a4_pdf(nesting_res, tmp_path, render_png_previews=False)
            data = tmp_path.read_bytes()
            try:
                tmp_path.unlink()
            except OSError:
                pass

            filename = f"{session.project.project_name.lower().replace(' ', '_')}_a4_print_{mode}.pdf"
            return Response(
                content=data,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as err:
            raise HTTPException(status_code=500, detail=f"Błąd eksportu A4 PDF: {err}")

    @app.get("/api/export/json")
    def export_json() -> Response:
        json_str = session.project.model_dump_json(indent=2)
        filename = f"{session.project.project_name.lower().replace(' ', '_')}_project.json"
        return Response(
            content=json_str.encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/diagnostics/contact-sheet")
    def get_contact_sheet() -> FileResponse:
        sheet = session.latest_diagnostic_sheet
        if not sheet or not sheet.is_file():
            fallback = Path("output/stage2_digitizer/diagnostic_contact_sheet.png")
            if fallback.is_file():
                sheet = fallback.resolve()
            else:
                raise HTTPException(status_code=404, detail="Brak wygenerowanego arkusza diagnostycznego.")
        return FileResponse(str(sheet), media_type="image/png")

    # -----------------------------------------------------------------------
    # Static SPA Frontend Mounting
    # -----------------------------------------------------------------------

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
