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
        if not image_path.is_file():
            raise ValueError("FreeCAD did not save the viewport screenshot")
        size = image_path.stat().st_size
        if size < 24 or size > MAX_IMAGE_BYTES:
            raise ValueError("Viewport screenshot is empty or exceeds the 8 MiB limit")
        image_bytes = image_path.read_bytes()
    finally:
        image_path.unlink(missing_ok=True)

    if not image_bytes.startswith(_PNG_SIGNATURE):
        raise ValueError("FreeCAD returned an invalid PNG screenshot")
    width = int.from_bytes(image_bytes[16:20], "big")
    height = int.from_bytes(image_bytes[20:24], "big")
    if width <= 0 or height <= 0:
        raise ValueError("FreeCAD returned an invalid screenshot size")
    return {
        "ok": True,
        "mime_type": "image/png",
        "width": width,
        "height": height,
        "bytes": len(image_bytes),
        "_image_data_url": "data:image/png;base64,"
        + base64.b64encode(image_bytes).decode("ascii"),
    }
