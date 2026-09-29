# SPDX-License-Identifier: LGPL-2.1-or-later

import json
import unittest
from unittest.mock import MagicMock, patch

from freecad.KineSketch.agent.cancellation import RequestCancellation, RequestCancelled
from freecad.KineSketch.agent.images import ImageAttachment
from freecad.KineSketch.agent.session import AgentSession
from freecad.KineSketch.agent.ssh_tunnel import SSHConfig, SSHTunnel, SSHTunnelError


class MergedSSHTests(unittest.TestCase):
    def setUp(self):
        self.cancellation = RequestCancellation()
        self.tunnel = SSHTunnel(SSHConfig("server", 6010, "user"), "localhost", 11434,
                                self.cancellation)
        self.processes = [MagicMock() for _ in range(3)]
        for process in self.processes:
            process.poll.return_value = None
        for target, value in (("shutil.which", "ssh"), ("_reserve_local_port", 43210)):
            patcher = patch(f"freecad.KineSketch.agent.ssh_tunnel.{target}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("freecad.KineSketch.agent.ssh_tunnel.subprocess.Popen", side_effect=self.processes)
        self.spawn = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(self.tunnel, "_wait_until_ready")
        self.ready = patcher.start()
        self.addCleanup(patcher.stop)

    def test_success_starts_one_process_and_waits_once(self):
        self.tunnel.open()
        self.spawn.assert_called_once()
        self.ready.assert_called_once_with(self.processes[0], 43210)
        self.assertIs(self.tunnel._process, self.processes[0])
        self.assertEqual(self.tunnel.local_port, 43210)
        self.tunnel.close()
        self.processes[0].wait.assert_called_once()
        self.assertIsNone(self.tunnel._process)

    def test_transient_retry_cleans_old_process_and_keeps_successful_one(self):
        self.ready.side_effect = [SSHTunnelError("Connection closed by remote host"), None]
        with patch.object(self.tunnel, "_wait_retry") as backoff:
            self.tunnel.open()
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(self.ready.call_count, 2)
        backoff.assert_called_once_with(0.5)
        self.processes[0].wait.assert_called_once()
        self.processes[0].stderr.close.assert_called_once()
        self.processes[1].terminate.assert_not_called()
        self.assertIs(self.tunnel._process, self.processes[1])
        self.tunnel.close()

    def test_authentication_error_does_not_retry(self):
        self.ready.side_effect = SSHTunnelError("Permission denied (publickey)")
        with self.assertRaisesRegex(SSHTunnelError, "Permission denied"):
            self.tunnel.open()
        self.spawn.assert_called_once()
        self.processes[0].wait.assert_called_once()
        self.assertIsNone(self.tunnel._process)
        self.assertIsNone(self.tunnel.local_port)

    def test_retries_are_bounded_and_all_failed_processes_are_cleaned(self):
        self.ready.side_effect = SSHTunnelError("Connection closed")
        with patch.object(self.tunnel, "_wait_retry"), self.assertRaises(SSHTunnelError):
            self.tunnel.open()
        self.assertEqual(self.spawn.call_count, 3)
        for process in self.processes:
            process.wait.assert_called_once()
            process.stderr.close.assert_called_once()
        self.assertIsNone(self.tunnel._process)

    def test_stop_during_backoff_prevents_another_process(self):
        self.ready.side_effect = SSHTunnelError("Connection closed")
        with patch("freecad.KineSketch.agent.ssh_tunnel.time.sleep",
                   side_effect=lambda delay: self.cancellation.cancel()):
            with self.assertRaises(RequestCancelled):
                self.tunnel.open()
        self.spawn.assert_called_once()
        self.processes[0].wait.assert_called_once()
        self.assertIsNone(self.tunnel._process)

    def test_cancelled_ssh_error_is_not_retried(self):
        def waiting(*args):
            self.cancellation.cancel()
            raise SSHTunnelError("Connection closed")

        self.ready.side_effect = waiting
        with self.assertRaises(RequestCancelled):
            self.tunnel.open()
        self.spawn.assert_called_once()
        self.processes[0].wait.assert_called_once()

    def test_stop_on_retry_cancels_the_new_process(self):
        def waiting(process, port):
            if process is self.processes[0]:
                raise SSHTunnelError("Connection closed")
            self.cancellation.cancel()
            self.cancellation.check()

        self.ready.side_effect = waiting
        with patch.object(self.tunnel, "_wait_retry"), self.assertRaises(RequestCancelled):
            self.tunnel.open()
        self.assertEqual(self.spawn.call_count, 2)
        for process in self.processes[:2]:
            process.terminate.assert_called()
            process.wait.assert_called_once()
        self.assertIsNone(self.tunnel._process)


class MergedSessionTests(unittest.TestCase):
    def test_cancel_closes_only_pending_calls_and_resets_tool_selection(self):
        session = AgentSession()
        session.begin("Create a slider-crank", "document")
        self.assertIsInstance(session.tool_choice, dict)
        session.accept_assistant({"tool_calls": [
            {"id": "done", "function": {"name": "build_slider_crank_from_plan"}},
            {"id": "pending", "function": {"name": "fit_view"}},
        ]})
        session.add_tool_result("done", '{"ok":true}')
        session.cancel_turn()
        results = [item for item in session.messages if item["role"] == "tool"]
        self.assertEqual([item["tool_call_id"] for item in results], ["done", "pending"])
        self.assertTrue(json.loads(results[0]["content"])["ok"])
        self.assertFalse(json.loads(results[1]["content"])["ok"])
        self.assertEqual(session.tool_choice, "auto")
        session.begin("Inspect this image", "document", ImageAttachment("drawing.png", "data:image/png;base64,AA"))
        self.assertEqual(session.request_messages[-1]["content"][1]["image_url"]["url"], "data:image/png;base64,AA")

    def test_followup_does_not_force_a_second_creation(self):
        session = AgentSession()
        session.begin("Create a slider-crank", "document")
        self.assertIsInstance(session.tool_choice, dict)
        session.continue_after_tools()
        self.assertEqual(session.tool_choice, "auto")

    def test_previous_images_are_released_without_losing_current_images(self):
        session = AgentSession()
        session.begin("First", "document", ImageAttachment("old.png", "data:image/png;base64,old"))
        session.add_viewport_images(["data:image/png;base64,old-view"], post_action=False)
        session.begin("Second", "document", ImageAttachment("new.png", "data:image/png;base64,new"))
        self.assertEqual(len(session.request_messages), 2)
        self.assertEqual(session.request_messages[-1]["content"][1]["image_url"]["url"], "data:image/png;base64,new")
        self.assertNotIn("base64,old", json.dumps(session.messages))


if __name__ == "__main__":
    unittest.main()
