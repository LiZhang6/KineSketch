# SPDX-License-Identifier: LGPL-2.1-or-later

import json
import threading
import sys
import traceback
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from freecad.KineSketch.agent.cancellation import RequestCancellation, RequestCancelled
from freecad.KineSketch.agent.client import AgentConfig, OpenAICompatibleClient, SSHTunneledAgentClient
from freecad.KineSketch.agent.ssh_tunnel import SSHConfig, SSHTunnel


class CancellationTests(unittest.TestCase):
    def test_binding_after_cancel_interrupts_and_raises(self):
        cancellation = RequestCancellation()
        cancellation.cancel()
        callback = MagicMock()
        with self.assertRaises(RequestCancelled), cancellation.bind(callback):
            self.fail("Cancelled binding must not run")
        callback.assert_called_once()

    def test_binding_is_removed_after_cleanup(self):
        cancellation = RequestCancellation()
        callback = MagicMock()
        with cancellation.bind(callback):
            pass
        cancellation.cancel()
        callback.assert_not_called()

    def test_cancel_before_request_does_not_connect(self):
        cancellation = RequestCancellation()
        cancellation.cancel()
        with patch("freecad.KineSketch.agent.client.HTTPConnection") as connection:
            with self.assertRaises(RequestCancelled):
                OpenAICompatibleClient(AgentConfig("http://localhost/v1", "test")).complete(
                    [], cancellation=cancellation)
        connection.assert_not_called()

    def test_cancel_interrupts_wait_for_headers_and_stalled_stream(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                self._assert_stalled_request_cancelled(stream)

    def test_cancellable_transport_preserves_payload_and_partial_sse_lines(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append((self.path, payload, self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                event = {"choices": [{"delta": {"content": "\u66f2\u67c4"}}]}
                line = ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
                self.wfile.write(line[:10])
                self.wfile.flush()
                # Exercise a read poll timeout in the middle of an SSE line.
                threading.Event().wait(0.3)
                self.wfile.write(line[10:] + b"data: [DONE]\n\n")
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        try:
            cancellation = RequestCancellation()
            config = AgentConfig(f"http://127.0.0.1:{server.server_port}/v1", "test",
                                 api_key="test-token", conversation_id="test-turn")
            chunks = []
            result = OpenAICompatibleClient(config).complete(
                [{"role": "user", "content": "create crank"}],
                [{"type": "function", "function": {"name": "create_crank"}}],
                on_content=chunks.append, tool_choice="required", cancellation=cancellation)
            self.assertEqual(chunks, ["\u66f2\u67c4"])
            self.assertEqual(result["content"], "\u66f2\u67c4")
            path, payload, authorization = captured[0]
            self.assertEqual(path, "/v1/chat/completions")
            self.assertEqual(payload["tool_choice"], "required")
            self.assertTrue(payload["stream"])
            self.assertEqual(payload["user"], "test-turn")
            self.assertEqual(authorization, "Bearer test-token")
            self.assertFalse(cancellation._callbacks)
        finally:
            server.shutdown()
            server.server_close()
            serving.join(2)

    def _assert_stalled_request_cancelled(self, stream):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        chunks = []
        errors = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                if stream:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    event = {"choices": [{"delta": {"content": "partial"}}]}
                    self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                    self.wfile.flush()
                entered.set()
                release.wait(5)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        cancellation = RequestCancellation()
        client = OpenAICompatibleClient(AgentConfig(
            f"http://127.0.0.1:{server.server_port}/v1", "test", timeout=10))

        def request():
            try:
                client.complete([], on_content=chunks.append if stream else None,
                                cancellation=cancellation)
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(2), "Server did not receive request")
            cancellation.cancel()
            stopped = finished.wait(2)
            stack = sys._current_frames().get(worker.ident)
            self.assertTrue(stopped, "Cancellation did not interrupt blocked read\n" +
                            ("".join(traceback.format_stack(stack)) if stack else ""))
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], RequestCancelled)
        finally:
            cancellation.cancel()
            release.set()
            server.shutdown()
            server.server_close()
            worker.join(2)
            serving.join(2)

    def test_cancellation_is_forwarded_through_tunnel(self):
        cancellation = RequestCancellation()
        ssh = SSHConfig("server", 22, "user")
        with patch("freecad.KineSketch.agent.client.SSHTunnel") as tunnel_type, \
                patch("freecad.KineSketch.agent.client.OpenAICompatibleClient") as direct:
            tunnel_type.return_value.__enter__.return_value.local_port = 43210
            SSHTunneledAgentClient(AgentConfig("http://localhost:11434/v1", "test", ssh=ssh)).complete(
                [], cancellation=cancellation)
        self.assertIs(tunnel_type.call_args.kwargs["cancellation"], cancellation)
        self.assertIs(direct.return_value.complete.call_args.kwargs["cancellation"], cancellation)

    def test_cancel_during_ssh_startup_terminates_process_and_cleans_up(self):
        cancellation = RequestCancellation()
        process = MagicMock()
        process.poll.return_value = None
        process.stderr = None
        tunnel = SSHTunnel(SSHConfig("server", 22, "user"), "localhost", 11434, cancellation)

        def waiting(*args):
            cancellation.cancel()
            cancellation.check()

        with patch("freecad.KineSketch.agent.ssh_tunnel.shutil.which", return_value="ssh"), \
                patch("freecad.KineSketch.agent.ssh_tunnel.subprocess.Popen", return_value=process), \
                patch("freecad.KineSketch.agent.ssh_tunnel._reserve_local_port", return_value=43210), \
                patch.object(tunnel, "_wait_until_ready", side_effect=waiting):
            with self.assertRaises(RequestCancelled):
                tunnel.open()
        process.terminate.assert_called()
        process.wait.assert_called_once()
        self.assertIsNone(tunnel._process)


if __name__ == "__main__":
    unittest.main()
