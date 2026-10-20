"""
UI package for Image-to-Pattern CAD:
FastAPI backend server, REST API, and interactive single-page CAD Studio.
"""

__all__ = ["create_app"]

from src.ui.server import create_app
