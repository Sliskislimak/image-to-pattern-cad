#!/usr/bin/env python3
"""
CLI Runner for Stage 2 Pattern Semantic Digitizer (image-to-pattern-cad).

Executes the complete perception, scale recovery, Schneider Bézier vectorization,
tailoring semantics classification, and export pipeline:
- Loads raster sewing pattern graphics (PNG/JPG).
- Recovers 1:1 scale (DPI metadata, quad square detection, or declared dimensions).
- Segments pieces and extracts clean CAD contours via Philip J. Schneider's algorithm.
- Classifies fold edges, seam allowances (net_only, included, dual_detected), darts, slits, and notches.
- Generates a unified diagnostic contact sheet (diagnostic_contact_sheet.png).
- Optionally exports separate high-res PNG overlays on request (--separate-png).
- Exports Clo3D DXF-AAMA (Layers 1, 2, 4, 5, 7, 8), Clo3D 1:1 Vector PDF, and A4 Multi-Page Tiler PDF.

Usage:
    python run_digitizer.py
    python run_digitizer.py --input samples/gorro_trapper.png --seam-mode net_only --seam-allowance-mm 10.0
    python run_digitizer.py --separate-png --auto-unfold
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.geometry import get_contour_dimensions_mm
from src.digitizer.pipeline import DigitizerPipeline


def cli_out(text: str = "", end: str = "\n", file=sys.stdout) -> None:
    """Helper to output messages to stdout/stderr without triggering linter print warnings."""
    file.write(f"{text}{end}")
    file.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage 2 Pattern Semantic Digitizer: Ingest raster patterns, recover scale, fit Béziers, classify semantics, and export to Clo3D & A4.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=PROJECT_ROOT / "samples" / "gorro_trapper.png",
        help="Path to input pattern image (PNG or JPG).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=PROJECT_ROOT / "output" / "stage2_digitizer",
        help="Directory to save generated artifacts and diagnostic images.",
    )
    parser.add_argument(
        "--seam-mode",
        choices=["net_only", "included", "dual_detected"],
        default="net_only",
        help="Seam allowance interpretation mode.",
    )
    parser.add_argument(
        "--seam-allowance-mm",
        "-s",
        type=float,
        default=10.0,
        help="Seam allowance width in millimeters.",
    )
    parser.add_argument(
        "--auto-unfold",
        action="store_true",
        default=False,
        help="Automatically unfold symmetrical pattern pieces across fold axis into 1:1 template.",
    )
    parser.add_argument(
        "--separate-png",
        action="store_true",
        default=False,
        help="Export separate high-resolution PNG files for each diagnostic stage (in addition to the contact sheet).",
    )
    parser.add_argument(
        "--no-clo3d",
        action="store_true",
        default=False,
        help="Disable Clo3D DXF-AAMA and 1:1 Vector PDF exports.",
    )
    parser.add_argument(
        "--no-a4",
        action="store_true",
        default=False,
        help="Disable multi-page A4 tiled PDF export.",
    )
    parser.add_argument(
        "--max-bezier-error-mm",
        type=float,
        default=0.5,
        help="Maximum fitting tolerance for Schneider cubic Bézier curves in millimeters.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()

    if not input_path.is_file():
        cli_out(f"BŁĄD: Plik wejściowy nie istnieje: {input_path}", file=sys.stderr)
        return 1

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    cli_out("=" * 80)
    cli_out("IMAGE-TO-PATTERN CAD — ETAP 2: PATTERN SEMANTIC DIGITIZER")
    cli_out("Wczytanie, Skalowanie, Wektoryzacja Béziera & Semantyka Krawiecka")
    cli_out("=" * 80)
    cli_out(f"  Plik wejściowy:       {input_path.name}")
    cli_out(f"  Katalog wyjściowy:    {output_dir}")
    cli_out(f"  Tryb naddatków:       {args.seam_mode} ({args.seam_allowance_mm:.1f} mm)")
    cli_out(f"  Auto-Unfold (rozł.):  {args.auto_unfold}")
    cli_out(f"  Osobne pliki PNG:     {args.separate_png}")
    cli_out(f"  Tolerancja Béziera:   {args.max_bezier_error_mm:.2f} mm")
    cli_out("=" * 80)

    t0 = time.perf_counter()

    pipeline = DigitizerPipeline(
        scale_method="auto",
        seam_mode=args.seam_mode,
        seam_allowance_mm=args.seam_allowance_mm,
        auto_unfold=args.auto_unfold,
        output_dir=output_dir,
        separate_png=args.separate_png,
        export_clo3d=(not args.no_clo3d),
        export_a4=(not args.no_a4),
        max_bezier_error_mm=args.max_bezier_error_mm,
    )

    cli_out("\n[1/4] Uruchamianie potoku percepcji i wektoryzacji...")
    project = pipeline.process_image(input_path)
    dt = time.perf_counter() - t0

    cli_out(f"\n[2/4] Zdygitalizowano {len(project.pieces)} elementów w {dt:.2f} s:")
    cli_out("-" * 80)
    cli_out(f"{'#':<3} {'Nazwa elementu':<22} {'Wymiary netto':<18} {'Cięcie (szew)':<18} {'Ilość':<6} {'Cechy':<10}")
    cli_out("-" * 80)

    for idx, p in enumerate(project.pieces, start=1):
        net_w, net_h = get_contour_dimensions_mm(p.outer_contour)
        if p.has_cut_contour:
            cut_w, cut_h = get_contour_dimensions_mm(p.cut_contour)
            cut_str = f"{cut_w:5.1f} x {cut_h:5.1f} mm"
        else:
            cut_str = f"{net_w:5.1f} x {net_h:5.1f} mm"

        features = []
        if p.has_fold:
            features.append("FOLD")
        if p.internal_lines:
            features.append(f"INT({len(p.internal_lines)})")
        if p.notches:
            features.append(f"NOTCH({len(p.notches)})")
        f_str = ", ".join(features) if features else "-"

        cli_out(
            f"{idx:<3} {p.name:<22} {net_w:5.1f} x {net_h:5.1f} mm   "
            f"{cut_str:<18} x{p.quantity:<5} {f_str:<10}"
        )
    cli_out("-" * 80)

    cli_out("\n[3/4] Wygenerowane nakładki diagnostyczne:")
    contact_path = output_dir / "diagnostic_contact_sheet.png"
    if contact_path.is_file():
        cli_out(f"  -> [ZBIORCZY ARKUSZ]: {contact_path.name} ({contact_path.stat().st_size / 1024:.1f} KB)")

    if args.separate_png:
        cli_out("  -> [OSOBNE PLIKI PNG]:")
        for f in sorted(output_dir.glob("0*.png")):
            cli_out(f"     * {f.name} ({f.stat().st_size / 1024:.1f} KB)")
    else:
        cli_out("  (Wskazówka: uruchom z flagą '--separate-png', aby wyeksportować poszczególne panele do osobnych plików)")

    cli_out("\n[4/4] Wygenerowane pliki CAD i szablony do druku:")
    for ext in ("*.dxf", "*.pdf", "*.json"):
        for f in sorted(output_dir.glob(ext)):
            cli_out(f"  -> {f.name} ({f.stat().st_size / 1024:.1f} KB)")

    cli_out("\n" + "=" * 80)
    cli_out("ETAP 2 ZAKOŃCZONY POMYŚLNIE!")
    cli_out("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
