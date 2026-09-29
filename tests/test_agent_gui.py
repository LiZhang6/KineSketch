"""Exercise the real dock, streaming parser and CAD tools without a model service."""

from __future__ import annotations

import base64
import json
import math
import os
import shutil
import unittest
import uuid
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

try:
    import FreeCAD as App
    HAS_GUI = bool(App.GuiUp)
except ImportError:
    HAS_GUI = False


def tool_call(name, arguments, call_id):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def authored_parts_plan():
    """A non-fixture design to prove solids come from the supplied features."""
    def box(origin, size):
        return {"kind": "box", "origin_mm": origin, "size_mm": size}

    def cylinder(origin, radius, height):
        return {"kind": "cylinder", "origin_mm": origin,
                "radius_mm": radius, "height_mm": height}

    return {
        "ground": {"label": "Text-designed twin-rail base", "features": [
            box([-36, -24, -15], [250, 48, 6]),
            box([-16, 10.5, -9], [230, 4, 9]),
            box([-16, -14.5, -9], [230, 4, 9]),
            cylinder([0, 0, -9], 6.5, 9),
        ]},
        "crank": {"label": "Text-designed crank", "features": [
            box([0, -4.5, -2.5], [32, 9, 5]),
            cylinder([0, 0, -2.5], 6, 5),
            cylinder([32, 0, -2.5], 5.5, 5),
        ]},
        "connecting_rod": {"label": "Text-designed rod", "features": [
            box([0, -3.5, -2], [126, 7, 4]),
            cylinder([0, 0, -2], 5.5, 4),
            cylinder([126, 0, -2], 5.5, 4),
        ]},
        "slider": {"label": "Text-designed slider", "features": [
            box([-11, -9, -4.5], [22, 18, 9]),
        ]},
    }


class StreamingParserTests(unittest.TestCase):
    def test_vision_message_is_sent_as_image_url_without_tools(self):
        from freecad.KineSketch.agent import client as client_module
        from freecad.KineSketch.agent.session import AgentSession

        session = AgentSession()
        session.begin("Check the CAD view", "One box")
        session.add_viewport_images(["data:image/png;base64,AAAA"], post_action=True)
        requests = []

        def fake_urlopen(request, timeout):
            requests.append((json.loads(request.data), timeout))
            return BytesIO(json.dumps({"choices": [{"message": {
                "role": "assistant", "content": "The box is visible."
            }}]}).encode("utf-8"))

        config = client_module.AgentConfig(endpoint="http://localhost/v1", model="qwen")
        with patch.object(client_module, "urlopen", side_effect=fake_urlopen):
            reply = client_module.OpenAICompatibleClient(config).complete(
                session.request_messages, tools=None
            )
        self.assertEqual(reply["content"], "The box is visible.")
        payload, _timeout = requests[0]
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["messages"][-1]["content"][1], {
            "type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}
        })

    def test_reasoning_only_response_is_an_error(self):
        from freecad.KineSketch.agent.client import AgentClientError, _read_streaming_message

        body = ("data: " + json.dumps({"choices": [{"delta": {
            "content": "", "reasoning": "I should call a CAD tool."
        }}]}) + "\n\ndata: [DONE]\n\n")
        with self.assertRaisesRegex(AgentClientError, "no text or tool calls"):
            _read_streaming_message(BytesIO(body.encode()), lambda _chunk: None)

    def test_slider_crank_creation_selects_geometry_skill(self):
        from freecad.KineSketch.agent.session import AgentSession

        session = AgentSession()
        session.begin("请创建双导轨曲柄滑块并仿真", "Empty document")
        self.assertEqual(session.tool_choice["function"]["name"],
                         "build_slider_crank_from_plan")
        session.begin("请重播曲柄滑块演示", "Existing simulation")
        self.assertEqual(session.tool_choice, "auto")

    def test_split_tool_call_and_content(self):
        from freecad.KineSketch.agent.client import _read_streaming_message

        events = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "box",
                "type": "function", "function": {"name": "create_box",
                "arguments": '{"length": 20,'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0,
                "function": {"arguments": ' "width": 10}'}}]}}]},
            {"choices": [{"delta": {"content": "Ready"}}]},
        ]
        body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
        body += "data: [DONE]\n\n"
        chunks = []
        reply = _read_streaming_message(BytesIO(body.encode()), chunks.append)
        self.assertEqual(reply["content"], "Ready")
        self.assertEqual(chunks, ["Ready"])
        self.assertEqual(reply["tool_calls"], [
            tool_call("create_box", {"length": 20, "width": 10}, "box")
        ])


@unittest.skipUnless(HAS_GUI, "FreeCAD GUI Python runtime is required for the agent dock")
class AgentGuiTests(unittest.TestCase):
    def setUp(self):
        from PySide import QtCore, QtWidgets
        from freecad.KineSketch.agent.panel import AgentDockWidget

        self.QtCore, self.QtWidgets = QtCore, QtWidgets
        scratch_root = Path(os.environ.get("TMP") or os.environ.get("TEMP") or ".")
        self.scratch = scratch_root / ("kinesketch_agent_" + uuid.uuid4().hex)
        self.scratch.mkdir()
        self.addCleanup(shutil.rmtree, self.scratch)
        self.panel = AgentDockWidget()
        self.panel._settings = QtCore.QSettings(
            str(self.scratch / "settings.ini"), QtCore.QSettings.IniFormat
        )
        self.panel.ssh_connection.setChecked(False)
        self.panel.api_key_edit.clear()
        self.panel.model_edit.setText("local-protocol-fixture")
        self.document = App.newDocument("AgentGuiTest")
        self.document.UndoMode = 1
        self.addCleanup(self.cleanup_gui)

    def cleanup_gui(self):
        self.process_until(lambda: self.panel._thread is None, 5)
        self.assertIsNone(self.panel._thread, "Request thread did not terminate")
        self.panel.deleteLater()
        App.closeDocument(self.document.Name)
        self.QtWidgets.QApplication.processEvents()

    def begin_turn(self, prompt):
        from freecad.KineSketch.agent.tools import document_summary

        self.panel._session.begin(prompt, document_summary())
        self.panel._turn_tool_count = 0
        self.panel._reply_pending = True
        self.panel._set_busy(True)
        self.panel._append("You", prompt)

    def process_until(self, condition, timeout):
        if condition():
            return True
        loop = self.QtCore.QEventLoop()
        poll = self.QtCore.QTimer()
        poll.setInterval(5)
        poll.timeout.connect(lambda: loop.quit() if condition() else None)
        deadline = self.QtCore.QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(round(timeout * 1000))
        loop.exec()
        poll.stop()
        deadline.stop()
        return condition()

    def wait_until(self, condition, timeout=8):
        if self.process_until(condition, timeout):
            return
        self.fail(f"Agent timed out: {self.panel.status_label.text()}; "
                  f"thread_alive={self.panel._thread.is_alive() if self.panel._thread else None}; "
                  f"reply_pending={self.panel._reply_pending}; "
                  f"continue_after_finish={self.panel._continue_after_finish}; "
                  f"{self.panel.transcript.toPlainText()}")

    def test_reentrant_tools_continue_and_create_requested_geometry(self):
        from freecad.KineSketch.agent import panel as panel_module

        execute = panel_module.execute_tool_call
        reentry_states = []

        def execute_with_gui_events(call):
            # FreeCAD recompute/view updates can process GUI events while a tool
            # reply is being applied; Send must stay disabled until the turn ends.
            self.QtWidgets.QApplication.processEvents()
            reentry_states.append({"thread_finished": self.panel._thread is None,
                                   "send_enabled": self.panel.send_button.isEnabled()})
            return execute(call)

        self.begin_turn(
            "请在当前 FreeCAD 文档中依次操作：创建 Smoke Box，长20、宽10、高5 mm；"
            "创建 Smoke Cylinder，半径4、高12 mm，位置(30,0,0)；"
            "将长方体移到(2,3,4)，绕Z轴旋转30°；切换轴测视图并显示全部。"
        )
        continuations = []
        with patch.object(panel_module, "execute_tool_call", side_effect=execute_with_gui_events):
            with patch.object(self.panel, "_request_model", side_effect=continuations.append):
                self.panel._handle_reply({"role": "assistant", "tool_calls": [
                    tool_call("create_box", {"label": "Smoke Box", "length": 20,
                                            "width": 10, "height": 5}, "box"),
                    tool_call("create_cylinder", {"label": "Smoke Cylinder", "radius": 4,
                                                 "height": 12, "x": 30}, "cylinder"),
                ]})
                tool_results = {m["tool_call_id"]: json.loads(m["content"])
                                for m in self.panel._session.messages if m["role"] == "tool"}
                self.panel._reply_pending = True
                self.panel._handle_reply({"role": "assistant", "tool_calls": [
                    tool_call("set_placement", {"name": tool_results["box"]["name"],
                                                "x": 2, "y": 3, "z": 4, "yaw": 30},
                              "placement"),
                    tool_call("fit_view", {}, "view"),
                ]})
                self.panel._reply_pending = True
                self.panel._handle_reply({"role": "assistant", "content":
                    "Smoke completed: both shapes created, box moved, view fitted."})

        self.assertEqual(len(continuations), 2)
        self.assertTrue(self.panel.send_button.isEnabled())

        self.assertEqual(len(self.document.Objects), 2)
        box, = self.document.getObjectsByLabel("Smoke Box")
        cylinder, = self.document.getObjectsByLabel("Smoke Cylinder")
        self.assertEqual([float(box.Length), float(box.Width), float(box.Height)], [20, 10, 5])
        self.assertAlmostEqual(box.Shape.Volume, 1000)
        self.assertEqual([float(cylinder.Radius), float(cylinder.Height)], [4, 12])
        self.assertAlmostEqual(cylinder.Shape.Volume, math.pi * 4**2 * 12)
        self.assertLess((box.Placement.Base - App.Vector(2, 3, 4)).Length, 1e-9)
        self.assertLess((cylinder.Placement.Base - App.Vector(30, 0, 0)).Length, 1e-9)
        self.assertTrue(box.Placement.Rotation.isSame(App.Rotation(App.Vector(0, 0, 1), 30),
                                                      1e-9))
        self.assertTrue(all(s["thread_finished"] for s in reentry_states))
        self.assertTrue(all(not s["send_enabled"] for s in reentry_states))
        results = [json.loads(m["content"]) for m in self.panel._session.messages
                   if m["role"] == "tool"]
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r["ok"] for r in results))
        self.assertIn("Smoke completed", self.panel.transcript.toPlainText())

        artifact_dir = os.environ.get("KINESKETCH_AGENT_ARTIFACT_DIR")
        if artifact_dir:
            import FreeCADGui as Gui
            output = Path(artifact_dir)
            output.mkdir(parents=True, exist_ok=True)
            self.document.saveAs(str(output / "smoke.FCStd"))
            Gui.activeDocument().activeView().saveImage(str(output / "smoke.png"), 1000, 700,
                                                       "White")
            (output / "local_agent_test.json").write_text(json.dumps({
                "status": "success", "transport": "local deterministic reply fixture",
                "freecad_version": App.Version()[:3], "model_rounds": 3,
                "tool_results": results, "reentry_states": reentry_states,
                "geometry_assertions_passed": True,
            }, indent=2), encoding="utf-8")

        old_id = self.panel._conversation_id
        self.panel._clear()
        self.assertNotEqual(self.panel._conversation_id, old_id)
        self.assertEqual(self.panel.transcript.toPlainText(), "")

    def test_text_response_restores_controls_without_changing_document(self):
        self.begin_turn("Say hello without changing the document.")
        self.panel._handle_reply({"role": "assistant", "content": "Text-only response."})
        self.assertTrue(self.panel.send_button.isEnabled())
        self.assertEqual(len(self.document.Objects), 0)
        self.assertIn("Text-only response.", self.panel.transcript.toPlainText())
        self.assertIn("no FreeCAD actions", self.panel.status_label.text())

    def test_capture_tool_sends_real_png_without_base64_in_tool_result(self):
        self.begin_turn("Create a box and inspect the viewport.")
        with patch.object(self.panel, "_request_model") as request_model:
            self.panel._handle_reply({"role": "assistant", "tool_calls": [
                tool_call("create_box", {"length": 20, "width": 10, "height": 5}, "box"),
                tool_call("fit_view", {}, "fit"),
                tool_call("capture_viewport", {}, "capture"),
            ]})
        request_model.assert_called_once()
        tool_result = next(m for m in self.panel._session.messages
                           if m.get("tool_call_id") == "capture")
        self.assertNotIn("base64", tool_result["content"])
        self.assertNotIn("base64", self.panel.transcript.toPlainText())
        image_message = self.panel._session.request_messages[-1]
        image_url = image_message["content"][1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
        image_bytes = base64.b64decode(image_url.split(",", 1)[1])
        self.assertTrue(image_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertLessEqual(len(image_bytes), 8 * 1024 * 1024)

    def test_experimental_post_action_check_uses_one_image_only_round(self):
        self.panel.visual_check.setChecked(True)
        self.begin_turn("Create a box and visually check it.")
        with patch.object(self.panel, "_request_model") as request_model:
            self.panel._handle_reply({"role": "assistant", "tool_calls": [
                tool_call("create_box", {"length": 20, "width": 10, "height": 5}, "box")
            ]})
            self.panel._reply_pending = True
            self.panel._handle_reply({"role": "assistant", "content": "Box created."})
            self.panel._review_request = True
            self.panel._reply_pending = True
            self.panel._handle_reply({"role": "assistant", "content": "Box appears visible."})
        self.assertEqual(request_model.call_count, 2)
        self.assertEqual(request_model.call_args_list[1].kwargs, {"review_only": True})
        image_message = next(m for m in self.panel._session.request_messages
                             if isinstance(m.get("content"), list))
        self.assertIn("Experimental visual check", image_message["content"][0]["text"])
        self.assertEqual(image_message["content"][1]["type"], "image_url")
        self.assertNotIn("base64", self.panel.transcript.toPlainText())
        self.assertEqual(self.panel.status_label.text(), "Visual check completed")

    def test_streaming_error_restores_controls_without_pending_continuation(self):
        self.begin_turn("Create a box.")
        self.panel._handle_error("No required tool call")
        self.assertTrue(self.panel.send_button.isEnabled())
        self.assertFalse(self.panel._reply_pending)
        self.assertFalse(self.panel._continue_after_finish)
        self.assertEqual(len(self.document.Objects), 0)
        self.assertIn("No required tool call", self.panel.transcript.toPlainText())
        self.assertEqual(self.panel.status_label.text(), "Request failed")

    def test_placement_accepts_unique_labels_but_rejects_ambiguous_labels(self):
        from freecad.KineSketch.agent.tools import execute_tool_call

        def call(name, arguments):
            return json.loads(execute_tool_call(tool_call(name, arguments, "label-test")))

        dimensions = {"label": "Smoke Box", "length": 20, "width": 10, "height": 5}
        first = call("create_box", dimensions)
        placed = call("set_placement", {"name": "Smoke Box", "x": 2, "y": 3,
                                        "z": 4, "yaw": 30})
        self.assertTrue(placed["ok"], placed)
        self.assertEqual(placed["name"], first["name"])
        box = self.document.getObject(first["name"])
        self.assertLess((box.Placement.Base - App.Vector(2, 3, 4)).Length, 1e-9)
        preferences = App.ParamGet("User parameter:BaseApp/Preferences/Document")
        duplicate_labels = preferences.GetBool("DuplicateLabels", False)
        self.addCleanup(preferences.SetBool, "DuplicateLabels", duplicate_labels)
        preferences.SetBool("DuplicateLabels", True)
        second = call("create_box", dimensions)
        self.assertEqual(len(self.document.getObjectsByLabel("Smoke Box")), 2)
        ambiguous = call("set_placement", {"name": "Smoke Box", "x": 100})
        self.assertFalse(ambiguous["ok"], ambiguous)
        self.assertIn("Ambiguous object label", ambiguous["error"])
        self.assertLess((box.Placement.Base - App.Vector(2, 3, 4)).Length, 1e-9)
        self.assertTrue(call("set_placement", {"name": second["name"], "x": 100})["ok"])

    def test_slider_crank_demo_tool_builds_and_replays_native_motion(self):
        from freecad.KineSketch.agent import slider_crank_demo
        from freecad.KineSketch.agent.tools import execute_tool_call

        existing = set(App.listDocuments())

        def close_demo_documents():
            if slider_crank_demo._player is not None:
                slider_crank_demo._player.stop()
            for name in set(App.listDocuments()) - existing:
                App.closeDocument(name)

        self.addCleanup(close_demo_documents)
        report_path = os.environ.get("KINESKETCH_TEST_REPORT")
        output_root = (Path(report_path).parent / "agent-demo" if report_path
                       else self.scratch / "agent-demo")
        output_root.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, output_root)
        with patch.dict(os.environ, {"KINESKETCH_AGENT_OUTPUT_ROOT": str(output_root)}):
            result = json.loads(execute_tool_call(tool_call(
                "create_slider_crank_demo",
                {"crank_radius_mm": 30, "rod_length_mm": 120,
                 "initial_angle_deg": 30, "rpm": 60, "cycles": 2,
                 "export_video": False, "playback_seconds": 5},
                "slider-crank-demo",
            )))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stage"], "complete")
        self.assertEqual(len(result["geometry"]["parts"]), 4)
        self.assertTrue(Path(result["geometry"]["preview"]).is_file())
        self.assertTrue(Path(result["assembly"]["project"]).is_file())
        self.assertEqual(result["assembly"]["solver_return_code"], 0)
        self.assertTrue(Path(result["simulation"]["project"]).is_file())
        self.assertEqual(result["simulation"]["sampled_frames"], 401)
        self.wait_until(lambda: slider_crank_demo._player.timer.isActive(), timeout=3)
        slider = slider_crank_demo._player.document.getObject("Slider")
        initial_x = slider.Placement.Base.x
        self.wait_until(lambda: abs(slider.Placement.Base.x - initial_x) > 0.01, timeout=3)
        replay = json.loads(execute_tool_call(tool_call(
            "replay_slider_crank", {"playback_seconds": 5}, "replay",
        )))
        self.assertTrue(replay["ok"], replay)

    def test_authored_feature_plan_builds_new_solids_assembly_and_motion(self):
        from freecad.KineSketch.agent import slider_crank_demo
        from freecad.KineSketch.agent.tools import execute_tool_call

        existing = set(App.listDocuments())

        def close_generated_documents():
            if slider_crank_demo._player is not None:
                slider_crank_demo._player.stop()
            for name in set(App.listDocuments()) - existing:
                App.closeDocument(name)

        self.addCleanup(close_generated_documents)
        report_path = os.environ.get("KINESKETCH_TEST_REPORT")
        output_root = (Path(report_path).parent / "authored-design" if report_path
                       else self.scratch / "authored-design")
        output_root.mkdir()
        self.addCleanup(shutil.rmtree, output_root)
        with patch.dict(os.environ, {"KINESKETCH_AGENT_OUTPUT_ROOT": str(output_root)}):
            result = json.loads(execute_tool_call(tool_call(
                "build_slider_crank_from_plan",
                {"crank_radius_mm": 32, "rod_length_mm": 126,
                 "initial_angle_deg": 30, "rpm": 60, "cycles": 2,
                 "export_video": False, "playback_seconds": 5,
                 "parts": authored_parts_plan()},
                "authored-design",
            )))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["source"], "model_authored_features")
        self.assertEqual(result["assembly"]["solver_return_code"], 0)
        self.assertEqual(result["simulation"]["sampled_frames"], 401)
        plan = json.loads(Path(result["geometry"]["design_plan"]).read_text(encoding="utf-8"))
        self.assertEqual(plan["parameters"]["crank_radius_mm"], 32)
        self.assertEqual(plan["parts"]["slider"]["features"][0]["size_mm"], [22, 18, 9])
        slider_part = App.openDocument(result["geometry"]["parts"]["slider"])
        try:
            self.assertAlmostEqual(slider_part.getObject("Slider").Shape.BoundBox.YLength, 18)
        finally:
            App.closeDocument(slider_part.Name)
        self.wait_until(lambda: slider_crank_demo._player.timer.isActive(), timeout=3)
        slider = slider_crank_demo._player.document.getObject("Slider")
        initial_x = slider.Placement.Base.x
        self.wait_until(lambda: abs(slider.Placement.Base.x - initial_x) > 0.01, timeout=3)

    def test_completed_demo_reports_locally_without_second_ssh_request(self):
        from freecad.KineSketch.agent import panel as panel_module

        completed = {
            "ok": True,
            "geometry": {"preview": "parts_preview.FCStd"},
            "assembly": {"solver_return_code": 0, "project": "assembly.FCStd"},
            "simulation": {"sampled_frames": 401, "native_frames": 402,
                           "project": "simulation.FCStd", "csv": "motion.csv"},
            "video": {"verified_frames": 120, "mp4": "freecad_native_3d.mp4"},
            "playback_seconds": 60,
        }
        self.begin_turn("Create and simulate the slider-crank demo.")
        with patch.object(panel_module, "execute_tool_call", return_value=json.dumps(completed)):
            with patch.object(self.panel, "_request_model", side_effect=AssertionError(
                    "A verified demo should not require a second model request")):
                self.panel._handle_reply({"role": "assistant", "tool_calls": [tool_call(
                    "create_slider_crank_demo", {}, "complete-demo",
                )]})
        self.assertTrue(self.panel.send_button.isEnabled())
        self.assertEqual(self.panel.status_label.text(), "Actions completed")
        self.assertIn("401 个采样帧", self.panel.transcript.toPlainText())


if __name__ == "__main__":
    unittest.main()
