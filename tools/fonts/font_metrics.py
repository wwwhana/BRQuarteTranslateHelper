"""Placement shared by the bitmap and SDF font builders."""

import math


def sdf_glyph_offsets(plane_bounds: dict, metrics: dict, render_size: int) -> tuple[int, int]:
    """Convert msdf-atlas-gen plane bounds to this game's left/top fields."""
    return (
        math.floor(plane_bounds["left"] * render_size),
        math.floor((plane_bounds["top"] - metrics["ascender"]) * render_size),
    )


def center_middle_dot(record: dict, image, center_y: float) -> None:
    if record["character"] not in ("·", "・"):
        return
    x, y = record["x"], record["y"]
    crop = image.crop((x, y, x + record["width"], y + record["height"]))
    bounds = crop.point(lambda value: 255 if value > 127 else 0).getbbox()
    if bounds is None or record["advance"] <= 0:
        raise ValueError("Could not determine middle-dot pixels or glyph advance")
    # The texture includes padding; center the visible ink, not the texture box.
    record["left"] = round((record["advance"] - bounds[0] - bounds[2]) / 2)
    record["top"] = round(center_y - (bounds[1] + bounds[3]) / 2)
