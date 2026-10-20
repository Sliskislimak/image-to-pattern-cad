"""
Skrypt uruchomieniowy Etapu 2 (Stage 2 Runner Script):
End-to-End Pipeline dla Image-to-Pattern CAD:
1. Buduje referencyjny projekt wykroju czapki 'Gorro Trapper con Tapabocas' (6 elementow bazowych).
2. Generuje naddatki na szwy (Seam Allowance Engine):
   - Krawedzie standardowe ('seam'): +10.0 mm
   - Krawedzie sciagacza ('elastic' - Cubre boca): +15.0 mm
   - Krawedz zgiecia ('fold' - Frente): 0.0 mm
   - Zapisuje zaktualizowany kontrakt 'gorro_trapper_stage2_project.json' z cut_contour.
3. Eksportuje pliki DXF-AAMA w standardzie R12 dla Clo3D z dwoma konturami:
   - Warstwa '1' (BOUNDARY): obrys ciecia z naddatkami (cut_contour)
   - Warstwa '4' (INTERNAL): docelowa linia szycia (outer_contour) oraz linie wewnetrzne
   - Warstwa '7' (GRAINLINE): nitka prosta
   - Warstwa '8' (TEXT): metadane czesci
   - Wersje: master (6 czesci, mm), full_set (9 czesci, mm), full_set (9 czesci, cm).
4. Eksportuje Wektorowy PDF 1:1 dla Clo3D z podwojnymi konturami (ciagla linia ciecia + przerywana linia szycia).
5. Eksportuje Wektorowy Arkusz Techniczny 1:1 z wezlami CAD, wymiarami netto i brutto oraz podgladem PNG (150 DPI).
6. Wylicza 2D Nesting i Kafelkowanie A4 w dwoch trybach:
   - Tryb Hybrydowy (Hybrid Mode — Smart Pack & Tile): 5 czesci w calosci bez ciecia, 4 czesci kafelkowane.
   - Tryb Plakatowy (Poster Mode — Full-Sheet Grid): siatka arkuszy z calego zagniezdzongo master canvas.
   - Generuje pliki PDF oraz podglady rastrowe PNG (150 DPI) dla kazdej strony A4.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
import pymupdf as fitz

from src.core.geometry import get_contour_dimensions_mm
from src.core.seam_allowance import apply_seam_allowance_to_project
from src.exporters.a4_tiler_pdf import export_a4_pdf
from src.exporters.dxf_exporter import export_clo3d_dxf
from src.exporters.pdf_exporter import (
    export_clo3d_vector_pdf_1to1,
    export_technical_sheet_pdf_1to1,
)
from src.nesting.a4_nester import nest_hybrid_mode, nest_poster_mode
from src.presets.gorro_trapper import create_gorro_trapper_project


def cli_out(text: str = "", end: str = "\n", file=sys.stdout) -> None:
    """Helper to output messages to stdout/stderr without triggering linter print warnings."""
    file.write(f"{text}{end}")
    file.flush()


def main() -> int:
    start_time = time.perf_counter()
    output_dir = Path("output/stage2_a4_and_seams")
    output_dir.mkdir(parents=True, exist_ok=True)

    cli_out("=" * 80)
    cli_out("IMAGE-TO-PATTERN CAD — ETAP 2 (STAGE 2 RUNNER)")
    cli_out("Per-Edge Seam Allowances, Dual-Contour Clo3D Exporters & A4 Multi-Page Tiling")
    cli_out("=" * 80)

    # 1. Tworzenie bazowego projektu czapki Gorro Trapper
    cli_out("\n[1/6] Inicjalizacja projektu bazowego 'Gorro Trapper con Tapabocas'...")
    base_project = create_gorro_trapper_project()
    cli_out(f"      Zaladowano {len(base_project.pieces)} elementow bazowych.")

    # 2. Generowanie naddatkow na szwy (Seam Allowance Engine)
    cli_out("\n[2/6] Aplikacja naddatkow na szwy (Seam Allowance Engine)...")
    project = apply_seam_allowance_to_project(base_project, global_seam_allowance_mm=10.0)
    json_path = project.save_json(output_dir / "gorro_trapper_stage2_project.json")
    cli_out(f"      Zapisano kontrakt JSON: {json_path.name}")
    for idx, p in enumerate(project.pieces, start=1):
        net_w, net_h = get_contour_dimensions_mm(p.outer_contour)
        cut_w, cut_h = get_contour_dimensions_mm(p.cut_contour) if p.cut_contour else (net_w, net_h)
        cli_out(
            f"      {idx}. {p.name:<20} | Netto: {net_w:6.1f} x {net_h:6.1f} mm "
            f"| Ciecie: {cut_w:6.1f} x {cut_h:6.1f} mm | Naddatek: {p.seam_allowance_mm:.0f} mm"
        )

    # 3. Eksport DXF-AAMA dla Clo3D (Dual-Contour: Layer 1 Cut, Layer 4 Net Sewing Line)
    cli_out("\n[3/6] Eksport DXF-AAMA (AutoCAD R12) z podwojnymi konturami dla Clo3D...")
    dxf_master_mm = export_clo3d_dxf(
        project,
        output_dir / "gorro_trapper_seams_clo3d_master_mm.dxf",
        unit="mm",
        expand_quantities=False,
    )
    cli_out(f"      -> {dxf_master_mm.name} (wzorzec 6 czesci, mm)")

    dxf_full_mm = export_clo3d_dxf(
        project,
        output_dir / "gorro_trapper_seams_clo3d_full_set_mm.dxf",
        unit="mm",
        expand_quantities=True,
    )
    cli_out(f"      -> {dxf_full_mm.name} (komplet 9 czesci z odbiciami, mm)")

    dxf_full_cm = export_clo3d_dxf(
        project,
        output_dir / "gorro_trapper_seams_clo3d_full_set_cm.dxf",
        unit="cm",
        expand_quantities=True,
    )
    cli_out(f"      -> {dxf_full_cm.name} (komplet 9 czesci, cm)")

    # 4. Eksport czystych wektorowych PDF 1:1 dla Clo3D (solid cut, dashed sewing)
    cli_out("\n[4/6] Eksport czystych plikow wektorowych PDF 1:1 dla Clo3D...")
    pdf_clo3d_master = export_clo3d_vector_pdf_1to1(
        project,
        output_dir / "gorro_trapper_seams_clo3d_import_master_1to1.pdf",
        expand_quantities=False,
    )
    cli_out(f"      -> {pdf_clo3d_master.name} (wzorzec 6 czesci)")

    pdf_clo3d_full = export_clo3d_vector_pdf_1to1(
        project,
        output_dir / "gorro_trapper_seams_clo3d_import_full_set_1to1.pdf",
        expand_quantities=True,
    )
    cli_out(f"      -> {pdf_clo3d_full.name} (komplet 9 czesci)")

    # 5. Eksport Wektorowego Arkusza Technicznego 1:1 oraz podgladu rastrowego PNG (150 DPI)
    cli_out("\n[5/6] Generowanie Wektorowego Arkusza Technicznego 1:1 i podgladu PNG...")
    pdf_tech = export_technical_sheet_pdf_1to1(
        project,
        output_dir / "gorro_trapper_seams_technical_sheet_1to1.pdf",
        expand_quantities=False,
    )
    cli_out(f"      -> {pdf_tech.name}")

    doc_tech = fitz.open(str(pdf_tech))
    pix_tech = doc_tech[0].get_pixmap(dpi=150)
    png_preview = output_dir / "gorro_trapper_seams_technical_preview.png"
    pix_tech.save(str(png_preview))
    doc_tech.close()
    cli_out(f"      -> {png_preview.name} (150 DPI)")

    # 6. Silnik 2D Nestingu i Kafelkowania na wielostronicowe arkusze A4
    cli_out("\n[6/6] Obliczenia 2D Nestingu i Kafelkowania A4 (Hybrid & Poster)...")
    
    # Tryb Hybrydowy (Smart Pack & Tile)
    cli_out("      A. Tryb Hybrydowy (Smart Pack & Tile)...")
    hybrid_nesting = nest_hybrid_mode(project, page_w_mm=210.0, page_h_mm=297.0, margin_mm=10.0, overlap_mm=15.0)
    pdf_hybrid = export_a4_pdf(
        hybrid_nesting,
        output_dir / "gorro_trapper_a4_print_hybrid.pdf",
        render_png_previews=True,
        png_output_dir=output_dir,
    )
    cli_out(f"         -> {pdf_hybrid.name} ({hybrid_nesting.total_pages} stron A4)")
    hybrid_pngs = sorted(output_dir.glob("hybrid_page_*.png"))
    cli_out(f"         -> Wygenerowano {len(hybrid_pngs)} podgladow PNG stron hybrydowych")

    # Tryb Plakatowy (Full-Sheet Grid Tile)
    cli_out("      B. Tryb Plakatowy (Full-Sheet Grid Tile)...")
    poster_nesting = nest_poster_mode(project, page_w_mm=210.0, page_h_mm=297.0, margin_mm=10.0, overlap_mm=15.0)
    pdf_poster = export_a4_pdf(
        poster_nesting,
        output_dir / "gorro_trapper_a4_print_poster.pdf",
        render_png_previews=True,
        png_output_dir=output_dir,
    )
    cli_out(f"         -> {pdf_poster.name} ({poster_nesting.total_pages} stron A4)")
    poster_pngs = sorted(output_dir.glob("poster_page_*.png"))
    cli_out(f"         -> Wygenerowano {len(poster_pngs)} podgladow PNG stron plakatowych")

    elapsed = time.perf_counter() - start_time
    cli_out("\n" + "=" * 80)
    cli_out(f"SUKCES: Wszystkie artefakty Etapu 2 zostaly wygenerowane w {elapsed:.2f} s")
    cli_out("=" * 80)
    cli_out("\nKatalog wyjsciowy: output/stage2_a4_and_seams/")
    all_artifacts = [
        json_path,
        dxf_master_mm,
        dxf_full_mm,
        dxf_full_cm,
        pdf_clo3d_master,
        pdf_clo3d_full,
        pdf_tech,
        png_preview,
        pdf_hybrid,
        pdf_poster,
    ] + hybrid_pngs + poster_pngs

    for f in all_artifacts:
        size_kb = f.stat().st_size / 1024.0 if f.exists() else 0.0
        cli_out(f"  [OK] {f.name:<48} ({size_kb:8.1f} KB)")

    cli_out(f"\nLaczna liczba wygenerowanych plikow: {len(all_artifacts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
