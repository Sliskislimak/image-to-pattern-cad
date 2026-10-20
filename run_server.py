#!/usr/bin/env python3
"""
CLI Runner for Image-to-Pattern CAD Studio (Stage 4 UI Server).

Launches the integrated FastAPI server and interactive Web CAD Studio:
- Interactive 2D Vector CAD Canvas (1:1 mm grid, Pan/Zoom, Bézier handles, Seam preview)
- 1-Click Tailoring Actions (Unfold, Mirror, Edge Role change, Variable & Global Seam Allowances)
- Raster Pattern Digitizer (Drag & Drop PNG/JPG with live Schneider Bézier vectorization)
- Multi-Page A4 Home Tiler & 2D Nesting Visualizer (Hybrid Smart Pack & Poster Grid)
- Production 1:1 Exports: Clo3D DXF-AAMA, Clo3D 1:1 Vector PDF, Technical Sheet, A4 Print PDF, JSON

Usage:
    python run_server.py
    python run_server.py --port 8080 --no-browser
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn
from src.ui.server import create_app


def cli_out(text: str = "", end: str = "\n", file=sys.stdout) -> None:
    """Helper to output messages to stdout/stderr without triggering linter print warnings."""
    file.write(f"{text}{end}")
    file.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Image-to-Pattern CAD Studio: FastAPI Server & Interactive Web CAD Canvas.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port to listen on.")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload for development.")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch web browser on startup.")
    return parser.parse_args()


def open_browser_delayed(url: str, delay_seconds: float = 1.0) -> None:
    def _open() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    timer = threading.Timer(delay_seconds, _open)
    timer.daemon = True
    timer.start()


def main() -> int:
    args = parse_args()
    url = f"http://{args.host}:{args.port}"

    cli_out("=" * 80)
    cli_out("IMAGE-TO-PATTERN CAD — ETAP 4: INTERAKTYWNY CYFROWY WARSZTAT KRAWIECKI")
    cli_out("FastAPI REST Server & 1:1 Vector CAD Canvas Studio")
    cli_out("=" * 80)
    cli_out(f"  Adres serwera:        {url}")
    cli_out(f"  Interfejs sieciowy:   {args.host}")
    cli_out(f"  Port:                 {args.port}")
    cli_out(f"  Auto-otwarcie www:    {not args.no_browser}")
    cli_out(f"  Auto-reload:          {args.reload}")
    cli_out("=" * 80)
    cli_out(f"\n[1/2] Inicjalizacja silnika CAD Studio...")
    cli_out(f"[2/2] Serwer uruchomiony! Otwórz w przeglądarce: {url}")
    cli_out("      Naciśnij Ctrl+C, aby zatrzymać serwer.\n")

    if not args.no_browser:
        open_browser_delayed(url)

    app = create_app()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
