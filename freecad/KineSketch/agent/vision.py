# SPDX-License-Identifier: LGPL-2.1-or-later

"""Capture the active FreeCAD viewport for an OpenAI-compatible vision request."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui


CAPTURE_WIDTH = 1024
CAPTURE_HEIGHT = 768
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def capture_viewport(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded PNG data URI; callers must keep it out of text history."""
    if arguments:
        raise ValueError("capture_viewport does not accept arguments")
    if not App.GuiUp or App.ActiveDocument is None:
        raise ValueError("No active FreeCAD document and 3D view")
    gui_document = Gui.activeDocument()
    if gui_document is None or gui_document.activeView() is None:
        raise ValueError("No active 3D view")

    image_path = Path(tempfile.gettempdir()) / f"kinesketch-viewport-{uuid4().hex}.png"
    try:
        gui_document.activeView().saveImage(
            str(image_path), CAPTURE_WIDTH, CAPTURE_HEIGHT, "White"
        )
        result = load_png_image(image_path)
    finally:
        image_path.unlink(missing_ok=True)
    return result


def load_png_image(image_path: Path) -> dict[str, Any]:
    """Encode a nonblank FreeCAD PNG for a vision request."""
    if not image_path.is_file():
        raise ValueError(f"FreeCAD viewport image is missing: {image_path}")
    size = image_path.stat().st_size
    if size < 24 or size > MAX_IMAGE_BYTES:
        raise ValueError("Viewport screenshot is empty or exceeds the 8 MiB limit")
    image_bytes = image_path.read_bytes()

    if not image_bytes.startswith(_PNG_SIGNATURE):
        raise ValueError("FreeCAD returned an invalid PNG screenshot")
    width = int.from_bytes(image_bytes[16:20], "big")
    height = int.from_bytes(image_bytes[20:24], "big")
    if width <= 0 or height <= 0:
        raise ValueError("FreeCAD returned an invalid screenshot size")
    rendered = QtGui.QImage(str(image_path))
    if rendered.isNull() or rendered.width() != width or rendered.height() != height:
        raise ValueError("FreeCAD returned an unreadable screenshot")
    background = rendered.pixelColor(0, 0).rgba()
    step_x = max(1, width // 48)
    step_y = max(1, height // 36)
    if all(rendered.pixelColor(x, y).rgba() == background
           for y in range(0, height, step_y)
           for x in range(0, width, step_x)):
        raise ValueError("FreeCAD viewport screenshot is blank")
    return {
        "ok": True,
        "mime_type": "image/png",
        "width": width,
        "height": height,
        "bytes": len(image_bytes),
        "_image_data_url": "data:image/png;base64,"
        + base64.b64encode(image_bytes).decode("ascii"),
    }


def video_review_frames(video: dict[str, Any]) -> list[Path]:
    """Find two already verified FreeCAD viewport frames in an exported MP4 run."""
    path = video.get("mp4")
    count = video.get("verified_frames")
    if not isinstance(path, str) or not isinstance(count, int) or count < 2:
        return []
    video_path = Path(path)
    frames_dir = video_path.with_name(video_path.stem + "_frames")
    frames = [frames_dir / f"frame_{index:04d}.png" for index in (0, count // 2)]
    return frames if all(frame.is_file() for frame in frames) else []
