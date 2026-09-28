# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded local image attachments for multimodal chat requests."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

MAX_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class ImageAttachment:
    filename: str
    data_url: str


def load_image(path: str | Path) -> ImageAttachment:
    path = Path(path)
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image must be no larger than 10 MiB.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("Choose a PNG, JPEG or WebP image.")
    encoded = base64.b64encode(data).decode("ascii")
    return ImageAttachment(path.name, f"data:{mime};base64,{encoded}")
