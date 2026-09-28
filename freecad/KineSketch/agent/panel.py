# SPDX-License-Identifier: LGPL-2.1-or-later

"""Qt dock widget for the FreeCAD-integrated KineSketch agent."""

from __future__ import annotations

import os
import json
import time
from collections import deque
from typing import Any, ClassVar
from uuid import uuid4

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from .client import AgentConfig, create_agent_client
from .defaults import connection_defaults
from .images import ImageAttachment, load_image
from .session import AgentSession
from .ssh_tunnel import SSHConfig
from .tools import TOOL_DEFINITIONS, document_summary, execute_tool_call


class _RequestWorker(QtCore.QObject):
    content_delta = QtCore.Signal(str)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(
        self, config: AgentConfig, messages: list[dict[str, Any]],
        tools: list[dict[str, Any]], tool_choice: str,
    ) -> None:
        super().__init__()
        self._config = config
        self._messages = messages
        self._tools = tools
        self._tool_choice = tool_choice
        self._stream_parts: list[str] = []
        self._stream_size = 0
        self._last_stream_emit = time.monotonic()

    def _buffer_content(self, content: str) -> None:
        self._stream_parts.append(content)
        self._stream_size += len(content)
        if self._stream_size >= 4096 or time.monotonic() - self._last_stream_emit >= 0.075:
            self._flush_content()

    def _flush_content(self) -> None:
        if self._stream_parts:
            self.content_delta.emit("".join(self._stream_parts))
            self._stream_parts.clear()
            self._stream_size = 0
            self._last_stream_emit = time.monotonic()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            reply = create_agent_client(self._config).complete(
                self._messages,
                self._tools,
                on_content=self._buffer_content,
                tool_choice=self._tool_choice,
            )
        except Exception as error:
            self._flush_content()
            self.failed.emit(str(error))
            return
        self._flush_content()
        self.completed.emit(reply)


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
        self._image: ImageAttachment | None = None
        self._thread: QtCore.QThread | None = None
        self._worker: _RequestWorker | None = None
        self._continue_after_finish = False
        self._busy = False
        self._paused = False
        self._paused_reply: dict[str, Any] | None = None
        self._paused_error: str | None = None
        self._reply_handled = False
        self._tools_running = False
        self._pending_tools: deque[dict[str, Any]] = deque()
        self._tool_step_scheduled = False
        self._view_pending = False
        self._tool_document = None
        self._stream_parts: list[str] = []
        self._status_signature = None
        self._streaming_reply_started = False
        self._conversation_id = self._new_conversation_id()
        self._settings = QtCore.QSettings("KineSketch", "Agent")
        self._build_ui()
        self._stream_timer = QtCore.QTimer(self)
        self._stream_timer.setSingleShot(True)
        self._stream_timer.setInterval(75)
        self._stream_timer.timeout.connect(self._flush_stream)

    def _build_ui(self) -> None:
        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)

        connection = QtWidgets.QGroupBox(
            App.Qt.translate("KineSketch", "Model connection"), container
        )
        self.model_connection = connection
        form = QtWidgets.QFormLayout(connection)
        endpoint, model = connection_defaults(
            self._settings.value("endpoint"), self._settings.value("model"), os.environ,
        )
        self.endpoint_edit = QtWidgets.QLineEdit(endpoint)
        self.model_edit = QtWidgets.QLineEdit(model)
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
        self.image_button = QtWidgets.QToolButton(container)
        self.image_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileIcon))
        self.image_button.setToolTip(App.Qt.translate("KineSketch", "Attach image"))
        self.image_label = QtWidgets.QLabel(container)
        self.image_label.setTextFormat(QtCore.Qt.PlainText)
        self.image_label.setWordWrap(True)
        self.remove_image_button = QtWidgets.QToolButton(container)
        self.remove_image_button.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.SP_DialogCancelButton)
        )
        self.remove_image_button.setToolTip(App.Qt.translate("KineSketch", "Remove image"))
        self.remove_image_button.setVisible(False)
        attachments = QtWidgets.QHBoxLayout()
        attachments.addWidget(self.image_button)
        attachments.addWidget(self.image_label, 1)
        attachments.addWidget(self.remove_image_button)
        self.clear_button = QtWidgets.QPushButton(
            App.Qt.translate("KineSketch", "Clear"), container
        )
        self.send_button = QtWidgets.QPushButton(
            App.Qt.translate("KineSketch", "Send"), container
        )
        self.send_button.setDefault(True)
        self.pause_button = QtWidgets.QPushButton(container)
        self._update_pause_button()
        controls.addWidget(self.clear_button)
        controls.addStretch()
        controls.addWidget(self.pause_button)
        controls.addWidget(self.send_button)

        layout.addWidget(connection)
        layout.addWidget(ssh_connection)
        layout.addWidget(self.transcript, 1)
        layout.addLayout(status_layout)
        layout.addWidget(self.status_progress)
        layout.addWidget(self.prompt_edit)
        layout.addLayout(attachments)
        layout.addLayout(controls)
        self.setWidget(container)

        self.send_button.clicked.connect(self._send)
        self.clear_button.clicked.connect(self._clear)
        self.pause_button.clicked.connect(self._toggle_pause)
        self._set_status(App.Qt.translate("KineSketch", "Ready"), "ready")
        self.image_button.clicked.connect(self._attach_image)
        self.remove_image_button.clicked.connect(self._remove_image)

    @QtCore.Slot()
    def _attach_image(self) -> None:
        if self._busy:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, App.Qt.translate("KineSketch", "Attach image"), "",
            "Images (*.png *.jpg *.jpeg *.webp)",
        )
        if not path:
            return
        try:
            image = load_image(path)
        except (OSError, ValueError) as error:
            self._append("System", str(error))
            return
        self._image = image
        self.image_label.setText(image.filename)
        self.remove_image_button.setVisible(True)

    @QtCore.Slot()
    def _remove_image(self) -> None:
        if self._busy:
            return
        self._image = None
        self.image_label.clear()
        self.remove_image_button.setVisible(False)

    @QtCore.Slot()
    def _send(self) -> None:
        if self._busy:
            return
        prompt = self.prompt_edit.toPlainText().strip()
        endpoint = self.endpoint_edit.text().strip()
        model = self.model_edit.text().strip()
        if not prompt and self._image is None:
            self._set_status(
                App.Qt.translate("KineSketch", "Enter a message"), "warning"
            )
            return
        if not prompt:
            prompt = (
                "Inspect this image for a FreeCAD model. Identify the part and readable "
                "dimensions. For a crank, create a feasible prototype using create_crank "
                "defaults for missing parameters and report the returned assumptions. "
                "Do not present defaults as measurements or verified material data."
            )
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
        self._session.begin(prompt, document_summary(), self._image)
        self._append("You", prompt)
        if self._image is not None:
            self._append("Image", self._image.filename)
        self._remove_image()
        self.prompt_edit.clear()
        self._request_model()

    def _request_model(self, status_message: str | None = None) -> None:
        if self._paused:
            return
        ssh_config = None
        if self.ssh_connection.isChecked():
            ssh_config = SSHConfig(
                host=self.ssh_host_edit.text().strip(),
                port=self.ssh_port_edit.value(),
                username=self.ssh_user_edit.text().strip(),
            )
        config = AgentConfig(
            endpoint=self.endpoint_edit.text().strip(),
            model=self.model_edit.text().strip(),
            api_key=self.api_key_edit.text(),
            conversation_id=self._conversation_id,
            ssh=ssh_config,
        )
        self._set_busy(True)
        self._set_status(
            status_message or App.Qt.translate("KineSketch", "Connecting to agent..."),
            "busy",
            busy=True,
        )
        self._streaming_reply_started = False
        self._reply_handled = False

        thread = QtCore.QThread(self)
        tools = self._session.request_tools(TOOL_DEFINITIONS)
        worker = _RequestWorker(
            config, self._session.request_messages, tools, self._session.tool_choice,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.content_delta.connect(self._handle_content_delta)
        worker.completed.connect(self._handle_reply)
        worker.failed.connect(self._handle_error)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._request_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @QtCore.Slot(object)
    def _handle_reply(self, message: dict[str, Any]) -> None:
        if self._paused:
            self._paused_reply = message
            return
        self._flush_stream()
        self._reply_handled = True
        try:
            tool_calls = self._session.accept_assistant(message)
            if tool_calls:
                self._append_unstreamed_content(message)
                self._set_status(
                    App.Qt.translate("KineSketch", "Applying agent actions..."),
                    "warning",
                    busy=True,
                )
                self._pending_tools.extend(tool_calls)
                self._tools_running = True
                self._tool_document = App.ActiveDocument
                self._schedule_tool_step()
                return
            if self._session.retry_missing_tool():
                self._continue_after_finish = True
                self._set_status(
                    App.Qt.translate("KineSketch", "Requesting model action..."), "busy", busy=True,
                )
                return
            self._append_unstreamed_content(message)
            self._set_status(
                App.Qt.translate("KineSketch", "Response received"), "ready"
            )
        except Exception as error:
            self._append("System", str(error))
            self._set_status(
                App.Qt.translate("KineSketch", "Agent action failed"), "error"
            )
        finally:
            self._advance_request()

    @QtCore.Slot()
    def _run_next_tool(self) -> None:
        """Run one document action, then return to Qt before the next action."""
        self._tool_step_scheduled = False
        if self._paused or not self._tools_running:
            return
        try:
            if self._view_pending:
                self._view_pending = False
                if App.ActiveDocument == self._tool_document:
                    self._fit_created_view()
                self._schedule_tool_step()
                return
            if self._pending_tools:
                call = self._pending_tools.popleft()
                name = call.get("function", {}).get("name", "unknown")
                self._set_status(f"Applying {name}...", "warning", busy=True)
                started = time.perf_counter()
                if App.ActiveDocument != self._tool_document:
                    result = json.dumps({"ok": False, "error": "Active document changed; action skipped."})
                else:
                    result = execute_tool_call(call)
                    self._tool_document = App.ActiveDocument
                self._session.add_tool_result(str(call.get("id", "")), result)
                self._show_tool_result(name, result, time.perf_counter() - started)
                self._schedule_tool_step()
                return
            self._session.continue_after_tools()
            self._continue_after_finish = True
        except Exception as error:
            self._pending_tools.clear()
            self._view_pending = False
            self._append("System", str(error))
            self._set_status(App.Qt.translate("KineSketch", "Agent action failed"), "error")
        self._tools_running = False
        self._advance_request()

    def _schedule_tool_step(self) -> None:
        if not self._paused and not self._tool_step_scheduled:
            self._tool_step_scheduled = True
            QtCore.QTimer.singleShot(0, self._run_next_tool)

    def _update_pause_button(self) -> None:
        label = "Resume" if self._paused else "Pause"
        icon = QtWidgets.QStyle.SP_MediaPlay if self._paused else QtWidgets.QStyle.SP_MediaPause
        self.pause_button.setText(App.Qt.translate("KineSketch", label))
        self.pause_button.setToolTip(App.Qt.translate("KineSketch", label + " conversation"))
        self.pause_button.setIcon(self.style().standardIcon(icon))
        self.pause_button.setEnabled(self._busy)

    @QtCore.Slot()
    def _toggle_pause(self) -> None:
        if not self._busy:
            return
        self._paused = not self._paused
        self._update_pause_button()
        if self._paused:
            self._stream_timer.stop()
            self._set_status(App.Qt.translate("KineSketch", "Conversation paused"), "warning")
            return
        self._set_status(App.Qt.translate("KineSketch", "Resuming conversation..."), "busy", busy=True)
        self._flush_stream()
        if self._paused_reply is not None:
            reply, self._paused_reply = self._paused_reply, None
            self._handle_reply(reply)
        elif self._paused_error is not None:
            error, self._paused_error = self._paused_error, None
            self._handle_error(error)
        elif self._tools_running:
            self._schedule_tool_step()
        else:
            self._advance_request()

    def _show_tool_result(self, name: str, result: str, elapsed: float) -> None:
        data = json.loads(result)
        if not data.get("ok"):
            self._append("Tool", f"{name}: {data.get('error', 'Action failed')} ({elapsed:.2f} s)")
            return
        if name in {"create_crank", "create_box", "create_cylinder", "generate_model"}:
            details = {key: data[key] for key in ("name", "results", "parameters", "density_source", "modeling_assumptions") if key in data}
            self._append("Tool", f"{name} completed ({elapsed:.2f} s)\n" + json.dumps(details, ensure_ascii=False, indent=2))
            self._view_pending = bool(App.GuiUp and App.ActiveDocument is not None)

    def _fit_created_view(self) -> None:
        try:
            result = json.loads(execute_tool_call({"function": {"name": "fit_view", "arguments": "{}"}}))
            if not result.get("ok"):
                raise ValueError(result.get("error", "View fitting failed"))
        except Exception as error:
            self._append("System", f"Model created, but view fitting failed: {error}")

    @QtCore.Slot(str)
    def _handle_content_delta(self, content: str) -> None:
        if not content:
            return
        self._stream_parts.append(content)
        if self._paused:
            return
        if not self._stream_timer.isActive():
            self._stream_timer.start()
        self._set_status(
            App.Qt.translate("KineSketch", "Receiving response..."), "busy", busy=True,
        )

    @QtCore.Slot()
    def _flush_stream(self) -> None:
        self._stream_timer.stop()
        if self._paused:
            return
        if not self._stream_parts:
            return
        content = "".join(self._stream_parts)
        self._stream_parts.clear()
        if not self._streaming_reply_started:
            self._begin_stream("KineSketch")
            self._streaming_reply_started = True
        self._append_stream_text(content)

    @QtCore.Slot(str)
    def _handle_error(self, message: str) -> None:
        if self._paused:
            self._paused_error = message
            return
        self._flush_stream()
        self._reply_handled = True
        self._append("System", message)
        self._set_status(App.Qt.translate("KineSketch", "Request failed"), "error")
        self._advance_request()

    @QtCore.Slot()
    def _request_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._advance_request()

    def _advance_request(self) -> None:
        if self._paused:
            return
        if self._thread is not None or self._tools_running or not self._reply_handled:
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
        if self._busy:
            return
        self._session.clear()
        self._stream_timer.stop()
        self._stream_parts.clear()
        self._remove_image()
        self._conversation_id = self._new_conversation_id()
        self.transcript.clear()
        self._set_status(App.Qt.translate("KineSketch", "Ready"), "ready")

    @staticmethod
    def _new_conversation_id() -> str:
        return f"kinesketch:{uuid4().hex}"

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if not busy:
            self._paused = False
        self._update_pause_button()
        self.model_connection.setEnabled(not busy)
        self.ssh_connection.setEnabled(not busy)
        self.prompt_edit.setEnabled(not busy)
        self.image_button.setEnabled(not busy)
        self.remove_image_button.setEnabled(not busy)
        self.clear_button.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.send_button.setText(
            App.Qt.translate("KineSketch", "Working...")
            if busy
            else App.Qt.translate("KineSketch", "Send")
        )

    def _set_status(self, message: str, state: str, *, busy: bool = False) -> None:
        signature = (message, state, busy)
        if signature == self._status_signature:
            return
        self._status_signature = signature
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
