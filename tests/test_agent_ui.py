# SPDX-License-Identifier: LGPL-2.1-or-later

import importlib.util
import json
import sys
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from freecad.KineSketch.agent.client import AgentConfig
from freecad.KineSketch.agent.images import ImageAttachment
from freecad.KineSketch.agent.session import AgentSession


def load_panel():
    scheduled = []
    core = SimpleNamespace(QObject=object, Signal=lambda *args: MagicMock(),
                           Slot=lambda *args: lambda function: function,
                           QTimer=SimpleNamespace(singleShot=lambda delay, fn: scheduled.append(fn)))
    widgets = SimpleNamespace(QDockWidget=object, QStyle=SimpleNamespace(SP_MediaStop=1),
                              QFileDialog=MagicMock())
    qt = SimpleNamespace(QtCore=core, QtGui=SimpleNamespace(), QtWidgets=widgets)
    path = Path(__file__).resolve().parents[1] / "freecad/KineSketch/agent/panel.py"
    spec = importlib.util.spec_from_file_location("freecad.KineSketch.agent._ui_test_panel", path)
    panel = importlib.util.module_from_spec(spec)
    app = SimpleNamespace(Qt=SimpleNamespace(translate=lambda context, text: text))
    registry = SimpleNamespace(TOOL_DEFINITIONS=[], document_summary=lambda: "document",
                               execute_tool_call=MagicMock(return_value='{"ok":true}'))
    with patch.dict(sys.modules, {"FreeCAD": app, "FreeCADGui": SimpleNamespace(),
                                 "PySide": qt, "freecad.KineSketch.agent.tools": registry}):
        spec.loader.exec_module(panel)
    dock = panel.AgentDockWidget.__new__(panel.AgentDockWidget)
    dock._session = AgentSession()
    dock._session.begin("Inspect document", "document")
    dock._image = None
    dock._busy = True
    dock._stopped = False
    dock._request_id = 1
    dock._thread = object()
    dock._worker = MagicMock()
    dock._requests = {dock._thread: dock._worker}
    dock.sender = lambda: dock._thread
    dock._reply_handled = False
    dock._continue_after_finish = False
    dock._tools_running = False
    dock._tool_step_scheduled = False
    dock._pending_tools = deque()
    dock._streaming_reply_started = False
    for name in ("_append", "_append_stream_text", "_begin_stream", "_request_model", "style",
                 "stop_button", "model_connection", "ssh_connection", "prompt_edit", "clear_button",
                 "send_button", "image_button", "remove_image_button", "image_label", "transcript",
                 "status_indicator", "status_label", "status_progress", "_settings", "endpoint_edit",
                 "model_edit", "ssh_host_edit", "ssh_port_edit", "ssh_user_edit"):
        setattr(dock, name, MagicMock())
    return panel, dock, scheduled


def call(name, call_id):
    return {"id": call_id, "function": {"name": name, "arguments": "{}"}}


class AgentUIStopTests(unittest.TestCase):
    def test_stop_cancels_worker_unlocks_and_discards_late_signals(self):
        _, dock, _ = load_panel()
        worker = dock._worker
        old_id = dock._request_id
        dock._stop_conversation()
        worker.cancel.assert_called_once()
        self.assertFalse(dock._busy)
        self.assertTrue(dock._stopped)
        dock.stop_button.setEnabled.assert_called_with(False)
        dock.send_button.setEnabled.assert_called_with(True)
        dock.image_button.setEnabled.assert_called_with(True)
        dock._receive_content_delta(old_id, "late")
        dock._receive_reply(old_id, {"content": "late"})
        dock._receive_error(old_id, "late error")
        dock._append_stream_text.assert_not_called()
        dock._append.assert_called_once_with("System", "Conversation stopped")

    def test_stop_between_tools_preserves_completed_results(self):
        panel, dock, scheduled = load_panel()
        dock._handle_reply({"tool_calls": [call("create_box", "first"), call("fit_view", "second")]})
        scheduled.pop(0)()
        dock._stop_conversation()
        while scheduled:
            scheduled.pop(0)()
        panel.execute_tool_call.assert_called_once()
        results = [json.loads(item["content"]) for item in dock._session.messages if item["role"] == "tool"]
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        dock._request_model.assert_not_called()

    def test_old_tool_callback_and_cleanup_do_not_affect_new_turn(self):
        panel, dock, scheduled = load_panel()
        old_thread = dock._thread
        dock._handle_reply({"tool_calls": [call("create_box", "old")]})
        dock._stop_conversation()
        dock._stopped = False
        dock._busy = True
        dock._request_id += 1
        new_thread = dock._thread = object()
        new_worker = dock._worker = object()
        dock._session.begin("new turn", "document")
        dock._handle_reply({"tool_calls": [call("fit_view", "new")]})
        scheduled.pop(0)()
        panel.execute_tool_call.assert_not_called()
        self.assertTrue(dock._tool_step_scheduled)
        dock.sender = lambda: old_thread
        dock._request_finished()
        self.assertIs(dock._thread, new_thread)
        self.assertIs(dock._worker, new_worker)
        self.assertTrue(dock._busy)
        scheduled.pop(0)()
        self.assertEqual(panel.execute_tool_call.call_args.args[0]["id"], "new")

    def test_normal_tools_continue_only_after_network_cleanup(self):
        panel, dock, scheduled = load_panel()
        dock._handle_reply({"tool_calls": [call("create_box", "box")]})
        while scheduled:
            scheduled.pop(0)()
        panel.execute_tool_call.assert_called_once()
        dock._request_model.assert_not_called()
        self.assertTrue(dock._busy)
        dock._request_finished()
        dock._request_model.assert_called_once()

    def test_thread_finishing_before_reply_does_not_unlock_early(self):
        _, dock, _ = load_panel()
        dock._request_finished()
        self.assertTrue(dock._busy)
        dock._handle_reply({"content": "done"})
        self.assertFalse(dock._busy)

    def test_idle_stop_is_ignored(self):
        _, dock, _ = load_panel()
        dock._busy = False
        dock._stop_conversation()
        self.assertFalse(dock._stopped)
        dock._worker.cancel.assert_not_called()

    def test_cancelled_worker_always_signals_cleanup_not_completion(self):
        panel, _, _ = load_panel()
        worker = panel._RequestWorker(AgentConfig("http://localhost/v1", "test"), [], 7)
        client = MagicMock()

        def complete(*args, **kwargs):
            worker.cancel()
            return {"content": "late"}

        client.complete.side_effect = complete
        with patch.object(panel, "create_agent_client", return_value=client):
            worker.run()
        worker.completed.emit.assert_not_called()
        worker.failed.emit.assert_not_called()
        worker.finished.emit.assert_called_once()


class AgentUIImageTests(unittest.TestCase):
    def test_image_only_send_after_stop_uses_multimodal_message(self):
        _, dock, _ = load_panel()
        dock._stop_conversation()
        dock._image = ImageAttachment("drawing.png", "data:image/png;base64,example")
        dock.prompt_edit.toPlainText.return_value = ""
        dock.endpoint_edit.text.return_value = "http://localhost/v1"
        dock.model_edit.text.return_value = "vision"
        dock.ssh_connection.isChecked.return_value = False
        dock._send()
        dock._request_model.assert_called_once()
        self.assertFalse(dock._stopped)
        self.assertIsNone(dock._image)
        content = dock._session.request_messages[-1]["content"]
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,example")
        self.assertNotIn("create_crank", content[0]["text"])

    def test_attach_remove_and_busy_attachment_guard(self):
        panel, dock, _ = load_panel()
        image = ImageAttachment("drawing.png", "data:image/png;base64,example")
        panel.QtWidgets.QFileDialog.getOpenFileName.return_value = ("drawing.png", "")
        with patch.object(panel, "load_image", return_value=image) as load:
            dock._attach_image()
            load.assert_not_called()
            dock._busy = False
            dock._attach_image()
            self.assertIs(dock._image, image)
            dock.image_label.setText.assert_called_with("drawing.png")
            dock._remove_image()
            self.assertIsNone(dock._image)
            dock.remove_image_button.setVisible.assert_called_with(False)

    def test_clear_discards_attachment(self):
        _, dock, _ = load_panel()
        dock._busy = False
        dock._image = ImageAttachment("drawing.png", "data:image/png;base64,example")
        dock._clear()
        self.assertIsNone(dock._image)
        self.assertEqual(len(dock._session.messages), 1)


if __name__ == "__main__":
    unittest.main()
