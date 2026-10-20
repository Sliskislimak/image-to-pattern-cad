"""
Interactive Region-of-Interest (ROI) zones for the Pattern Semantic Digitizer.

The user can outline, directly on the uploaded raster image, areas where the digitizer
should search for pattern pieces (``include`` zones) and areas it must ignore
(``exclude`` zones, e.g. a mannequin photo, an assembly diagram or a size table).

Zones are stored as polygons in *normalized* image coordinates (x, y in [0, 1] relative to the
image width / height). This keeps them independent of the resolution at which the browser
previews the image.

Semantics (Photoshop-like selection arithmetic):
    allowed = (union of include zones, or the whole image when none exist)
              minus (union of exclude zones)
"""

from __future__ import annotations

import math
from typing import Any, List, Literal, Sequence, Tuple

import cv2
import numpy as np
from pydantic import BaseModel, Field, field_validator

__all__ = [
    "ROIZone",
    "parse_zones",
    "rasterize_zones",
    "apply_roi_to_image",
    "draw_zones_overlay",
]

ZoneMode = Literal["include", "exclude"]

_INCLUDE_BGR = (80, 200, 80)
_EXCLUDE_BGR = (60, 60, 230)


class ROIZone(BaseModel):
    """One user-drawn polygon zone in normalized image coordinates."""

    mode: ZoneMode = Field(description="'include' = search for pieces here; 'exclude' = ignore this area.")
    points: List[Tuple[float, float]] = Field(
        min_length=3,
        description="Closed polygon vertices as normalized (x, y) pairs in [0, 1].",
    )

    @field_validator("points")
    @classmethod
    def _validate_points(cls, pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        clean: List[Tuple[float, float]] = []
        for x, y in pts:
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError("ROI zone points must be finite numbers.")
            clean.append((min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y)))))
        return clean


def parse_zones(raw: Any) -> List[ROIZone]:
    """Builds a validated zone list from a JSON-like payload (list of dicts / ROIZone)."""
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("ROI zones must be a list of {mode, points} objects.")
    return [z if isinstance(z, ROIZone) else ROIZone.model_validate(z) for z in raw]


def _polygon_mask(zone: ROIZone, h: int, w: int) -> np.ndarray:
    pts = np.array(
        [[x * (w - 1), y * (h - 1)] for x, y in zone.points],
        dtype=np.float64,
    )
    m = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m, [np.round(pts).astype(np.int32).reshape(-1, 1, 2)], 255)
    return m


def rasterize_zones(zones: Sequence[ROIZone], shape: Tuple[int, ...]) -> np.ndarray:
    """
    Returns a boolean (H, W) mask: True where the digitizer may look for pattern pieces.

    Raises ValueError when the zones leave nothing to search in.
    """
    h, w = int(shape[0]), int(shape[1])
    includes = [z for z in zones if z.mode == "include"]
    excludes = [z for z in zones if z.mode == "exclude"]

    if includes:
        allowed = np.zeros((h, w), np.uint8)
        for z in includes:
            allowed = cv2.bitwise_or(allowed, _polygon_mask(z, h, w))
    else:
        allowed = np.full((h, w), 255, np.uint8)

    for z in excludes:
        allowed = cv2.bitwise_and(allowed, cv2.bitwise_not(_polygon_mask(z, h, w)))

    result = allowed > 0
    if not result.any():
        raise ValueError("ROI zones leave no area to search for pattern pieces.")
    return result


def apply_roi_to_image(image: np.ndarray, allowed: np.ndarray, fill_value: int = 255) -> np.ndarray:
    """Returns a copy of ``image`` where everything outside ``allowed`` is painted with background."""
    if allowed.shape[:2] != image.shape[:2]:
        raise ValueError(f"ROI mask shape {allowed.shape[:2]} does not match image {image.shape[:2]}.")
    out = image.copy()
    out[~allowed] = fill_value
    return out


def draw_zones_overlay(
    image: np.ndarray,
    zones: Sequence[ROIZone],
    alpha: float = 0.28,
    line_px: int = 2,
) -> np.ndarray:
    """Draws include (green) / exclude (red) zones on a copy of ``image`` for diagnostics."""
    h, w = image.shape[:2]
    out = image.copy()
    tint = out.copy()
    for z in zones:
        color = _INCLUDE_BGR if z.mode == "include" else _EXCLUDE_BGR
        cv2.fillPoly(tint, [_zone_pixels(z, h, w)], color)
    out = cv2.addWeighted(tint, alpha, out, 1.0 - alpha, 0.0)
    for z in zones:
        color = _INCLUDE_BGR if z.mode == "include" else _EXCLUDE_BGR
        cv2.polylines(out, [_zone_pixels(z, h, w)], True, color, line_px, cv2.LINE_AA)
    return out


def _zone_pixels(zone: ROIZone, h: int, w: int) -> np.ndarray:
    pts = np.array([[x * (w - 1), y * (h - 1)] for x, y in zone.points], dtype=np.float64)
    return np.round(pts).astype(np.int32).reshape(-1, 1, 2)
