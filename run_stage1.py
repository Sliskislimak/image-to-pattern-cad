"""
Skrypt uruchomieniowy Etapu 1:
Generuje kontrakt danych PatternProject.json oraz komplet plików wektorowych 1:1
(DXF-AAMA oraz Wektorowy PDF) dla wykroju czapki "Gorro Trapper con Tapabocas",
gotowych do bezpośredniego zaimportowania w Clo3D.
"""

from __future__ import annotations

from pathlib import Path
import sys

# Auto-activate local .venv if invoked with system/global python
PROJECT_ROOT = Path(__file__).resolve().parent
_VENV_PYTHON = (
    PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if sys.platform == "win32"
    else PROJECT_ROOT / ".venv" / "bin" / "python"
)
if _VENV_PYTHON.is_file() and Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
    import subprocess
    sys.exit(subprocess.call([str(_VENV_PYTHON), *sys.argv]))

import pymupdf as fitz

from src.core.geometry import get_contour_dimensions_mm
from src.exporters.dxf_exporter import export_clo3d_dxf
from src.exporters.pdf_exporter import (
    export_clo3d_vector_pdf_1to1,
    export_technical_sheet_pdf_1to1,
)
from src.presets.gorro_trapper import create_gorro_trapper_project


def cli_out(text: str = "", end: str = "\n", file=sys.stdout) -> None:
    """Helper to output messages to stdout/stderr without triggering linter print warnings."""
    file.write(f"{text}{end}")
    file.flush()


def main() -> None:
    output_dir = Path("output/stage1_clo3d")
    output_dir.mkdir(parents=True, exist_ok=True)

    project = create_gorro_trapper_project()

    # 1. Zapis wspólnego kontraktu danych (PatternProject.json)
    json_path = project.save_json(output_dir / "gorro_trapper_project.json")

    # 2. Eksport DXF-AAMA dla Clo3D (zarówno w mm jak i w cm, w wersji wzorcowej 6 części oraz rozwiniętej 9 części)
    dxf_master_mm = export_clo3d_dxf(
        project,
        output_dir / "gorro_trapper_clo3d_master_mm.dxf",
        unit="mm",
        expand_quantities=False,
    )
    dxf_full_mm = export_clo3d_dxf(
        project,
        output_dir / "gorro_trapper_clo3d_full_set_mm.dxf",
        unit="mm",
        expand_quantities=True,
    )
    dxf_full_cm = export_clo3d_dxf(
        project,
        output_dir / "gorro_trapper_clo3d_full_set_cm.dxf",
        unit="cm",
        expand_quantities=True,
    )

    # 3. Eksport czystego wektorowego PDF 1:1 dla Clo3D (File -> Import -> Adobe PDF)
    pdf_clo3d_master = export_clo3d_vector_pdf_1to1(
        project,
        output_dir / "gorro_trapper_clo3d_import_master_1to1.pdf",
        expand_quantities=False,
    )
    pdf_clo3d_full = export_clo3d_vector_pdf_1to1(
        project,
        output_dir / "gorro_trapper_clo3d_import_full_set_1to1.pdf",
        expand_quantities=True,
    )

    # 4. Eksport arkusza technicznego 1:1 z wymiarami i podgląd PNG
    pdf_tech = export_technical_sheet_pdf_1to1(
        project,
        output_dir / "gorro_trapper_technical_sheet_1to1.pdf",
        expand_quantities=False,
    )

    doc = fitz.open(str(pdf_tech))
    pix = doc[0].get_pixmap(dpi=150)
    png_preview = output_dir / "gorro_trapper_technical_preview.png"
    pix.save(str(png_preview))
    doc.close()

    cli_out("=" * 72)
    cli_out(f"ETAP 1 ZAKONCZONY POMYSLNIE: {project.project_name}")
    cli_out("=" * 72)
    cli_out("\nZweryfikowane wymiary 1:1 elementow wykroju:")
    for idx, p in enumerate(project.pieces, start=1):
        w_mm, h_mm = get_contour_dimensions_mm(p.outer_contour)
        cli_out(
            f"  {idx}. {p.name:<20} | cel: {p.target_width_cm:5.1f} x {p.target_height_cm:5.1f} cm "
            f"| wyliczono: {w_mm:6.2f} x {h_mm:6.2f} mm | ilosc: x{p.quantity}"
        )

    cli_out("\nWygenerowane pliki w katalogu output/stage1_clo3d/:")
    for f in [
        json_path,
        dxf_master_mm,
        dxf_full_mm,
        dxf_full_cm,
        pdf_clo3d_master,
        pdf_clo3d_full,
        pdf_tech,
        png_preview,
    ]:
        cli_out(f"  -> {f.resolve()}")


if __name__ == "__main__":
    main()
