# SPDX-License-Identifier: LGPL-2.1-or-later

"""Qt dock widget for the FreeCAD-integrated KineSketch agent."""

from __future__ import annotations

import json
import os
from queue import Empty, SimpleQueue
from threading import Thread
from typing import Any, ClassVar
from urllib.parse import urlsplit
from uuid import uuid4

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from .client import AgentConfig, create_agent_client
from .session import AgentSession
from .ssh_tunnel import SSHConfig
from .tools import TOOL_DEFINITIONS, document_summary, execute_tool_call, local_tool_summary


class _RequestWorker:
    def __init__(self, config: AgentConfig, messages: list[dict[str, Any]]) -> None:
        self._config = config
        self._messages = messages
        self.events: SimpleQueue[tuple[str, Any]] = SimpleQueue()

    def run(self) -> None:
        try:
            reply = create_agent_client(self._config).complete(
                self._messages,
                TOOL_DEFINITIONS,
                on_content=lambda content: self.events.put(("content", content)),
            )
        except Exception as error:
            self.events.put(("failed", str(error)))
            return
        self.events.put(("completed", reply))


class AgentDockWidget(QtWidgets.QDockWidget):
    """Interactive agent panel hosted by FreeCAD's main window."""

    ObjectName: ClassVar[str] = "KineSketchAgentDock"

    def __init__(self, parent=None) -> None:
        super().__init__(App.Qt.translate("KineSketch", "KineSketch Agent"), parent)
        self.setObjectName(self.ObjectName)
        self.setAllowedAreas(
            QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea
        )
        self._session = AgentSession()
        self._thread: Thread | None = None
        self._worker: _RequestWorker | None = None
        self._request_poll = QtCore.QTimer(self)
        self._request_poll.setInterval(10)
        self._request_poll.timeout.connect(self._poll_request)
        self._reply_pending = False
        self._continue_after_finish = False
        self._turn_tool_count = 0
        self._streaming_reply_started = False
        self._conversation_id = self._new_conversation_id()
        self._settings = QtCore.QSettings("KineSketch", "Agent")
        self._build_ui()

    def _build_ui(self) -> None:
        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)

        connection = QtWidgets.QGroupBox(
            App.Qt.translate("KineSketch", "Model connection"), container
        )
        self.model_connection = connection
        form = QtWidgets.QFormLayout(connection)
        self.endpoint_edit = QtWidgets.QLineEdit(
            self._settings.value(
                "endpoint",
                os.getenv(
                    "KINESKETCH_AGENT_ENDPOINT",
                    os.getenv("KINESKETCH_ENDPOINT", "http://127.0.0.1:18789/v1"),
                ),
            )
        )
        self.model_edit = QtWidgets.QLineEdit(
            self._settings.value(
                "model",
                os.getenv(
                    "KINESKETCH_AGENT_ID",
                    os.getenv("KINESKETCH_MODEL", "openclaw/default"),
                ),
            )
        )
        self.api_key_edit = QtWidgets.QLineEdit(
            os.getenv(
                "KINESKETCH_AGENT_TOKEN", os.getenv("KINESKETCH_API_KEY", "")
            )
        )
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(
            App.Qt.translate("KineSketch", "Optional; not saved")
        )
        form.addRow(App.Qt.translate("KineSketch", "Endpoint"), self.endpoint_edit)
        form.addRow(App.Qt.translate("KineSketch", "Agent"), self.model_edit)
        form.addRow(App.Qt.translate("KineSketch", "Access token"), self.api_key_edit)

        ssh_connection = QtWidgets.QGroupBox(
            App.Qt.translate("KineSketch", "SSH key tunnel"), container
        )
        ssh_connection.setCheckable(True)
        ssh_connection.setChecked(self._settings.value("ssh/enabled", True, type=bool))
        ssh_form = QtWidgets.QFormLayout(ssh_connection)
        self.ssh_connection = ssh_connection
        self.ssh_host_edit = QtWidgets.QLineEdit(
            self._settings.value(
                "ssh/host", os.getenv("KINESKETCH_SSH_HOST", "61.172.235.130")
            )
        )
        self.ssh_port_edit = QtWidgets.QSpinBox(ssh_connection)
        self.ssh_port_edit.setRange(1, 65535)
        self.ssh_port_edit.setValue(
            int(self._settings.value("ssh/port", os.getenv("KINESKETCH_SSH_PORT", "6010")))
        )
        self.ssh_user_edit = QtWidgets.QLineEdit(
            self._settings.value(
                "ssh/user", os.getenv("KINESKETCH_SSH_USER", "asus_gx10")
            )
        )
        ssh_form.addRow(App.Qt.translate("KineSketch", "Server"), self.ssh_host_edit)
        ssh_form.addRow(App.Qt.translate("KineSketch", "Port"), self.ssh_port_edit)
        ssh_form.addRow(App.Qt.translate("KineSketch", "Username"), self.ssh_user_edit)

        self.transcript = QtWidgets.QTextBrowser(container)
        self.transcript.setOpenExternalLinks(True)
        self.transcript.setPlaceholderText(
            App.Qt.translate(
                "KineSketch", "Ask about the active document or request a CAD change."
            )
        )

        self.prompt_edit = QtWidgets.QPlainTextEdit(container)
        self.prompt_edit.setPlaceholderText(
            App.Qt.translate("KineSketch", "Describe what you want to create or change...")
        )
        self.prompt_edit.setMaximumHeight(110)

        status_layout = QtWidgets.QHBoxLayout()
        status_layout.setContentsMargins(2, 0, 2, 0)
        self.status_indicator = QtWidgets.QLabel(container)
        self.status_indicator.setFixedSize(10, 10)
        self.status_indicator.setAccessibleName(
            App.Qt.translate("KineSketch", "Agent status")
        )
        self.status_label = QtWidgets.QLabel(container)
        status_layout.addWidget(self.status_indicator)
        status_layout.addWidget(self.status_label, 1)

        self.status_progress = QtWidgets.QProgressBar(container)
        self.status_progress.setTextVisible(False)
        self.status_progress.setFixedHeight(4)

        controls = QtWidgets.QHBoxLayout()
        self.clear_button = QtWidgets.QPushButton(
            App.Qt.translate("KineSketch", "Clear"), container
        )
        self.send_button = QtWidgets.QPushButton(
            App.Qt.translate("KineSketch", "Send"), container
        )
        self.send_button.setDefault(True)
        controls.addWidget(self.clear_button)
        controls.addStretch()
        controls.addWidget(self.send_button)

        layout.addWidget(connection)
        layout.addWidget(ssh_connection)
        layout.addWidget(self.transcript, 1)
        layout.addLayout(status_layout)
        layout.addWidget(self.status_progress)
        layout.addWidget(self.prompt_edit)
        layout.addLayout(controls)
        self.setWidget(container)

        self.send_button.clicked.connect(self._send)
        self.clear_button.clicked.connect(self._clear)
        self._set_status(App.Qt.translate("KineSketch", "Ready"), "ready")

    @QtCore.Slot()
    def _send(self) -> None:
        if self._thread is not None or self._reply_pending:
            return
        prompt = self.prompt_edit.toPlainText().strip()
        endpoint = self.endpoint_edit.text().strip()
        model = self.model_edit.text().strip()
        if not prompt:
            self._set_status(
                App.Qt.translate("KineSketch", "Enter a message"), "warning"
            )
            return
        if not endpoint or not model:
            self._append("System", "Endpoint and model are required.")
            self._set_status(
                App.Qt.translate("KineSketch", "Configuration required"), "error"
            )
            return
        if self.ssh_connection.isChecked() and (
            not self.ssh_host_edit.text().strip()
            or not self.ssh_user_edit.text().strip()
        ):
            self._append("System", "SSH server and username are required.")
            self._set_status(
                App.Qt.translate("KineSketch", "SSH configuration required"),
                "error",
            )
            return

        self._settings.setValue("endpoint", endpoint)
        self._settings.setValue("model", model)
        self._settings.setValue("ssh/enabled", self.ssh_connection.isChecked())
        self._settings.setValue("ssh/host", self.ssh_host_edit.text().strip())
        self._settings.setValue("ssh/port", self.ssh_port_edit.value())
        self._settings.setValue("ssh/user", self.ssh_user_edit.text().strip())
        self._session.begin(prompt, document_summary())
        self._turn_tool_count = 0
        self._append("You", prompt)
        self.prompt_edit.clear()
        self._request_model()

    def _request_model(self, status_message: str | None = None) -> None:
        ssh_config = None
        if self.ssh_connection.isChecked():
            ssh_config = SSHConfig(
                host=self.ssh_host_edit.text().strip(),
                port=self.ssh_port_edit.value(),
                username=self.ssh_user_edit.text().strip(),
            )
        is_ollama = urlsplit(self.endpoint_edit.text().strip()).port == 11434
        config = AgentConfig(
            endpoint=self.endpoint_edit.text().strip(),
            model=self.model_edit.text().strip(),
            api_key=self.api_key_edit.text(),
            conversation_id=self._conversation_id,
            ssh=ssh_config,
            max_tokens=8192 if is_ollama else None,
            think=False if is_ollama else None,
            tool_choice=self._session.tool_choice,
        )
        self._set_busy(True)
        self._set_status(
            status_message or App.Qt.translate("KineSketch", "Connecting to agent..."),
            "busy",
            busy=True,
        )
        self._streaming_reply_started = False
        self._reply_pending = True
        self._continue_after_finish = False

        worker = _RequestWorker(config, self._session.request_messages)
        thread = Thread(target=worker.run, name="KineSketchAgentRequest", daemon=True)
        self._thread = thread
        self._worker = worker
        self._request_poll.start()
        thread.start()

    @QtCore.Slot()
    def _poll_request(self) -> None:
        worker = self._worker
        if worker is None:
            self._request_poll.stop()
            return
        while True:
            try:
                kind, value = worker.events.get_nowait()
            except Empty:
                return
            if kind == "content":
                self._handle_content_delta(value)
                continue
            self._request_poll.stop()
            thread = self._thread
            if thread is not None:
                thread.join(timeout=2)
            self._thread = None
            self._worker = None
            if kind == "completed":
                self._handle_reply(value)
            else:
                self._handle_error(value)
            return

    @QtCore.Slot(object)
    def _handle_reply(self, message: dict[str, Any]) -> None:
        try:
            tool_calls = self._session.accept_assistant(message)
            if tool_calls:
                self._append_unstreamed_content(message)
                self._set_status(
                    App.Qt.translate("KineSketch", "Applying agent actions..."),
                    "warning",
                    busy=True,
                )
                local_summaries: list[str | None] = []
                for tool_call in tool_calls:
                    result = execute_tool_call(tool_call)
                    self._turn_tool_count += 1
                    function = tool_call.get("function")
                    name = (
                        function.get("name", "Invalid tool")
                        if isinstance(function, dict)
                        else "Invalid tool"
                    )
                    self._append("FreeCAD", f"{name}: {result}")
                    self._session.add_tool_result(str(tool_call.get("id", "")), result)
                    local_summaries.append(local_tool_summary(name, result))
                if len(local_summaries) == 1 and local_summaries[0] is not None:
                    self._append("KineSketch", local_summaries[0])
                    result_data = json.loads(result)
                    succeeded = bool(result_data.get("ok"))
                    if (not succeeded and name == "build_slider_crank_from_plan"
                            and result_data.get("stage") in ("plan", "geometry")):
                        self._session.continue_after_tools()
                        self._continue_after_finish = True
                        return
                    self._set_status(
                        App.Qt.translate(
                            "KineSketch", "Actions completed" if succeeded else "Agent action failed"
                        ),
                        "ready" if succeeded else "error",
                    )
                    return
                self._session.continue_after_tools()
                self._continue_after_finish = True
                return
            self._append_unstreamed_content(message)
            status = (
                App.Qt.translate("KineSketch", "Response received")
                if self._turn_tool_count
                else App.Qt.translate("KineSketch", "Text response received; no FreeCAD actions")
            )
            self._set_status(status, "ready")
        except Exception as error:
            self._append("System", str(error))
            self._set_status(
                App.Qt.translate("KineSketch", "Agent action failed"), "error"
            )
        finally:
            self._reply_pending = False
            self._advance_request()

    @QtCore.Slot(str)
    def _handle_content_delta(self, content: str) -> None:
        if not content:
            return
        if not self._streaming_reply_started:
            self._begin_stream("KineSketch")
            self._streaming_reply_started = True
        self._append_stream_text(content)
        self._set_status(
            App.Qt.translate("KineSketch", "Receiving response..."),
            "busy",
            busy=True,
        )

    @QtCore.Slot(str)
    def _handle_error(self, message: str) -> None:
        self._append("System", message)
        self._set_status(App.Qt.translate("KineSketch", "Request failed"), "error")
        self._reply_pending = False
        self._advance_request()

    def _advance_request(self) -> None:
        # CAD operations can re-enter the Qt event loop. Wait for BOTH the reply
        # handler and the worker thread before continuing or enabling Send/Clear.
        if self._thread is not None or self._reply_pending:
            return
        if self._continue_after_finish:
            self._continue_after_finish = False
            self._request_model(
                App.Qt.translate("KineSketch", "Sending action results...")
            )
            return
        self._set_busy(False)

    @QtCore.Slot()
    def _clear(self) -> None:
        if self._thread is not None or self._reply_pending:
            return
        self._session.clear()
        self._conversation_id = self._new_conversation_id()
        self.transcript.clear()
        self._set_status(App.Qt.translate("KineSketch", "Ready"), "ready")

    @staticmethod
    def _new_conversation_id() -> str:
        return f"kinesketch:{uuid4().hex}"

    def _set_busy(self, busy: bool) -> None:
        self.model_connection.setEnabled(not busy)
        self.ssh_connection.setEnabled(not busy)
        self.prompt_edit.setEnabled(not busy)
        self.clear_button.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.send_button.setText(
            App.Qt.translate("KineSketch", "Working...")
            if busy
            else App.Qt.translate("KineSketch", "Send")
        )

    def _set_status(self, message: str, state: str, *, busy: bool = False) -> None:
        colors = {
            "ready": "#2e7d32",
            "busy": "#1976d2",
            "warning": "#b26a00",
            "error": "#c62828",
        }
        color = colors.get(state, "#6b7280")
        self.status_indicator.setStyleSheet(
            f"background-color: {color}; border-radius: 5px;"
        )
        self.status_label.setText(message)
        if busy:
            self.status_progress.setStyleSheet("")
            self.status_progress.setRange(0, 0)
        else:
            self.status_progress.setRange(0, 1)
            self.status_progress.setValue(0)
            self.status_progress.setStyleSheet(
                "QProgressBar { background: transparent; border: none; }"
                "QProgressBar::chunk { background: transparent; }"
            )

    def _append(self, speaker: str, text: str) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        if not self.transcript.document().isEmpty():
            cursor.insertText("\n\n")
        cursor.insertText(f"{speaker}\n{text}")
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()

    def _append_unstreamed_content(self, message: dict[str, Any]) -> None:
        content = message.get("content") or ""
        if content and not self._streaming_reply_started:
            self._append("KineSketch", str(content))

    def _begin_stream(self, speaker: str) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        if not self.transcript.document().isEmpty():
            cursor.insertText("\n\n")
        cursor.insertText(f"{speaker}\n")
        self.transcript.setTextCursor(cursor)

    def _append_stream_text(self, text: str) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()


def show_agent_panel() -> AgentDockWidget:
    """Create or reveal the singleton agent dock widget."""
    main_window = Gui.getMainWindow()
    panel = main_window.findChild(QtWidgets.QDockWidget, AgentDockWidget.ObjectName)
    if panel is None:
        panel = AgentDockWidget(main_window)
        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
    panel.show()
    panel.raise_()
    return panel
