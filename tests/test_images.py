# SPDX-License-Identifier: LGPL-2.1-or-later

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from freecad.KineSketch.agent import images
from freecad.KineSketch.agent.client import AgentConfig, OpenAICompatibleClient
from freecad.KineSketch.agent.images import ImageAttachment, load_image
from freecad.KineSketch.agent.session import AgentSession


class ImageTests(unittest.TestCase):
    def test_formats_detected_from_content_not_extension(self):
        samples = [
            (b"\x89PNG\r\n\x1a\nexample", "image/png"),
            (b"\xff\xd8\xffexample", "image/jpeg"),
            (b"RIFF\x00\x00\x00\x00WEBPexample", "image/webp"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drawing.bin"
            for data, mime in samples:
                with self.subTest(mime=mime):
                    path.write_bytes(data)
                    attachment = load_image(path)
                    self.assertEqual(attachment.filename, "drawing.bin")
                    self.assertEqual(attachment.data_url,
                                     f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}")

    def test_rejects_unsupported_and_empty_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drawing.png"
            for data in (b"", b"GIF89a", b"<svg></svg>", b"RIFFnot-a-webp"):
                path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "PNG, JPEG or WebP"):
                    load_image(path)

    def test_size_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drawing.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\nX")
            with patch.object(images, "MAX_IMAGE_BYTES", 8):
                with self.assertRaisesRegex(ValueError, "10 MiB"):
                    load_image(path)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                load_image(Path(folder) / "missing.png")


class MultimodalSessionTests(unittest.TestCase):
    def setUp(self):
        self.session = AgentSession()
        self.image = ImageAttachment("crank.png", "data:image/png;base64,example")

    def test_text_only_remains_string(self):
        self.session.begin("Create a box", "document")
        self.assertIsInstance(self.session.request_messages[-1]["content"], str)

    def test_image_retained_during_tool_rounds(self):
        self.session.begin("Use this drawing", "document", self.image)
        content = self.session.request_messages[-1]["content"]
        self.assertIn("Use this drawing", content[0]["text"])
        self.assertEqual(content[1], {"type": "image_url", "image_url": {
            "url": self.image.data_url, "detail": "high",
        }})
        self.session.accept_assistant({"content": None, "tool_calls": [
            {"id": "inspect", "type": "function", "function": {
                "name": "inspect_document", "arguments": "{}",
            }},
        ]})
        self.session.add_tool_result("inspect", '{"ok":true}')
        self.session.continue_after_tools()
        self.assertEqual(self.session.request_messages[1]["content"], content)
        self.session.begin("Confirm dimensions", "document")
        self.assertEqual(len(self.session.request_messages), 2)
        self.assertIsInstance(self.session.request_messages[1]["content"], str)

    def test_clear_removes_image_history(self):
        self.session.begin("drawing", "document", self.image)
        self.session.clear()
        self.assertEqual(len(self.session.messages), 1)

    def test_http_payload_preserves_image(self):
        self.session.begin("drawing", "document", self.image)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }).encode()
        config = AgentConfig("http://localhost/v1", "vision", conversation_id="test")
        with patch("freecad.KineSketch.agent.client.urlopen", return_value=response) as send:
            reply = OpenAICompatibleClient(config).complete(self.session.request_messages)
        payload = json.loads(send.call_args.args[0].data)
        self.assertEqual(payload["messages"], self.session.request_messages)
        self.assertEqual(payload["user"], "test")
        self.assertEqual(reply["content"], "ok")


if __name__ == "__main__":
    unittest.main()
