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

from .client import AgentClientError, AgentEmptyResponseError, AgentConfig, create_agent_client, validate_access_token
from .cancellation import RequestCancellation, RequestCancelled
from .images import ImageAttachment, load_image
from .session import AgentSession, CREATION_TOOLS
from .ssh_tunnel import SSHConfig
from .tools import TOOL_DEFINITIONS, document_summary, execute_tool_call


REASONING_PREVIEW_LIMIT = 2000


class _RequestWorker(QtCore.QObject):
    content_delta = QtCore.Signal(int, str)
    reasoning_delta = QtCore.Signal(int, str)
    status_changed = QtCore.Signal(int, str)
    completed = QtCore.Signal(int, object)
    failed = QtCore.Signal(int, str)
    finished = QtCore.Signal()

    def __init__(self, config: AgentConfig, messages: list[dict[str, Any]],
                 request_id: int = 0, tools: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self._config = config
        self._messages = messages
        self._tools = TOOL_DEFINITIONS if tools is None else tools
        self.request_id = request_id
        self.cancellation = RequestCancellation()
        self._stream_parts: list[str] = []
        self._stream_size = 0
        self._last_stream_emit = 0.0
        self._reasoning_parts: list[str] = []

    def _emit_status(self, status: str) -> None:
        self.cancellation.check()
        self.status_changed.emit(self.request_id, status)

    def _emit_reasoning(self, content: str) -> None:
        self.cancellation.check()
        if content:
            self._reasoning_parts[:] = [
                ("".join(self._reasoning_parts) + content)[-REASONING_PREVIEW_LIMIT:]
            ]
        if time.monotonic() - self._last_stream_emit >= 0.075:
            self._flush_content()

    def _emit_content(self, content: str) -> None:
        self.cancellation.check()
        self._stream_parts.append(content)
        self._stream_size += len(content)
        if self._stream_size >= 4096 or time.monotonic() - self._last_stream_emit >= 0.075:
            self._flush_content()

    def _flush_content(self) -> None:
        if self._reasoning_parts:
            self.reasoning_delta.emit(self.request_id, "".join(self._reasoning_parts))
            self._reasoning_parts.clear()
        if self._stream_parts:
            self.content_delta.emit(self.request_id, "".join(self._stream_parts))
            self._stream_parts.clear()
            self._stream_size = 0
        self._last_stream_emit = time.monotonic()

    def cancel(self) -> None:
        self.cancellation.cancel()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            self.cancellation.check()
            reply = create_agent_client(self._config).complete(
                self._messages,
                self._tools,
                tool_choice=self._config.tool_choice,
                on_content=self._emit_content,
                on_reasoning=self._emit_reasoning,
                on_status=self._emit_status,
                cancellation=self.cancellation,
            )
            self.cancellation.check()
        except RequestCancelled:
            return
        except AgentEmptyResponseError as error:
            if not self.cancellation.cancelled:
                self._flush_content()
                self.completed.emit(self.request_id, {"content": None, "_empty_response_error": str(error)})
            return
        except Exception as error:
            if not self.cancellation.cancelled:
                self._flush_content()
                self.failed.emit(self.request_id, str(error))
            return
        else:
            self._flush_content()
            self.completed.emit(self.request_id, reply)
        finally:
            self.finished.emit()


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
        self._stopped = False
        self._request_id = 0
        self._requests = {}
        self._reply_handled = False
        self._tools_running = False
        self._pending_tools: deque[dict[str, Any]] = deque()
        self._pending_viewport_images: list[str] = []
        self._tool_step_scheduled = False
        self._streaming_reply_started = False
        self._stream_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._reasoning_text = ""
        self._last_connection = None
        self._profile_tokens: dict[str, str] = {}
        self._provider = "compatible"
        self._status_signature = None
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
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        container.setMinimumWidth(280)

        header = QtWidgets.QHBoxLayout()
        self.connection_summary = QtWidgets.QLabel(container)
        self.connection_summary.setTextFormat(QtCore.Qt.PlainText)
        self.connection_summary.setWordWrap(True)
        self.connection_summary.setMinimumWidth(0)
        self.connection_summary.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.settings_button = QtWidgets.QToolButton(container)
        self.settings_button.setCheckable(True)
        self.settings_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView))
        self.settings_button.setToolTip(App.Qt.translate("KineSketch", "Connection settings"))
        self.settings_button.setAccessibleName(App.Qt.translate("KineSketch", "Connection settings"))
        self.settings_button.setFixedSize(32, 32)
        header.addWidget(self.connection_summary, 1)
        header.addWidget(self.settings_button)

        self.settings_area = QtWidgets.QScrollArea(container)
        self.settings_area.setWidgetResizable(True)
        self.settings_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.settings_area.setMaximumHeight(260)
        settings = QtWidgets.QWidget(self.settings_area)
        settings_layout = QtWidgets.QVBoxLayout(settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_area.setWidget(settings)

        connection = QtWidgets.QGroupBox(
            App.Qt.translate("KineSketch", "Model connection"), container
        )
        self.model_connection = connection
        form = QtWidgets.QFormLayout(connection)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.endpoint_edit = QtWidgets.QLineEdit(
            self._settings.value(
                "endpoint",
                os.getenv(
                    "KINESKETCH_AGENT_ENDPOINT",
                    os.getenv("KINESKETCH_ENDPOINT", "http://127.0.0.1:18789/v1"),
                ),
            )
        )
        self.provider_combo = QtWidgets.QComboBox(connection)
        self.provider_combo.addItem("OpenAI-compatible / Gateway", "compatible")
        self.provider_combo.addItem("StepFun", "stepfun")
        self.provider_combo.addItem("StepFun International", "stepfun-international")
        self.model_combo = QtWidgets.QComboBox(connection)
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.model_combo.addItems(["openclaw/default", "step-3.5-flash", "step-3.5-flash-2603",
                                  "step-3.7-flash", "step-5-preview"])
        self.model_combo.setEditText(str(self._settings.value(
                "model",
                os.getenv(
                    "KINESKETCH_AGENT_ID",
                    os.getenv("KINESKETCH_MODEL", "openclaw/default"),
                ),
            )))
        self.model_edit = self.model_combo.lineEdit()
        self.api_key_edit = QtWidgets.QLineEdit(
            os.getenv(
                "KINESKETCH_AGENT_TOKEN", os.getenv("KINESKETCH_API_KEY", "")
            )
        )
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(
            App.Qt.translate("KineSketch", "Optional; not saved")
        )
        self.api_key_edit.setToolTip(App.Qt.translate("KineSketch", "Access token (ASCII only); leave empty if not required"))
        form.addRow(App.Qt.translate("KineSketch", "Provider"), self.provider_combo)
        form.addRow(App.Qt.translate("KineSketch", "Endpoint"), self.endpoint_edit)
        form.addRow(App.Qt.translate("KineSketch", "Model"), self.model_combo)
        form.addRow(App.Qt.translate("KineSketch", "Access token"), self.api_key_edit)
        self.max_tokens_edit = QtWidgets.QSpinBox(connection)
        self.max_tokens_edit.setRange(128, 32768)
        self.max_tokens_edit.setValue(int(self._settings.value("max_tokens", 8192)))
        form.addRow(App.Qt.translate("KineSketch", "Output tokens"), self.max_tokens_edit)
        self.timeout_edit = QtWidgets.QSpinBox(connection)
        self.timeout_edit.setRange(15, 600)
        self.timeout_edit.setSuffix(" s")
        self.timeout_edit.setValue(int(self._settings.value("timeout", 300)))
        form.addRow(App.Qt.translate("KineSketch", "Response timeout"), self.timeout_edit)
        self.thinking_combo = QtWidgets.QComboBox(connection)
        for label, value in (("Default", None), ("Off", "none"), ("Low", "low"), ("High", "high")):
            self.thinking_combo.addItem(App.Qt.translate("KineSketch", label), value)
        self.thinking_combo.setCurrentIndex(max(0, self.thinking_combo.findData(self._settings.value("reasoning_effort", None))))
        form.addRow(App.Qt.translate("KineSketch", "Thinking"), self.thinking_combo)

        ssh_connection = QtWidgets.QGroupBox(
            App.Qt.translate("KineSketch", "SSH key tunnel"), container
        )
        ssh_connection.setCheckable(True)
        ssh_connection.setChecked(self._settings.value(
            "profiles/compatible/ssh", self._settings.value("ssh/enabled", True, type=bool), type=bool))
        ssh_form = QtWidgets.QFormLayout(ssh_connection)
        ssh_form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        ssh_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
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
        self.endpoint_edit.setText(str(self._settings.value(
            "profiles/compatible/endpoint", self.endpoint_edit.text())))
        self.model_combo.setEditText(str(self._settings.value(
            "profiles/compatible/model", self.model_edit.text())))

        self.transcript = QtWidgets.QTextBrowser(container)
        self.transcript.setMinimumHeight(140)
        self.transcript.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.transcript.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        self.transcript.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.transcript.document().setDocumentMargin(8)
        self.transcript.setOpenExternalLinks(True)
        self.transcript.setPlaceholderText(
            App.Qt.translate(
                "KineSketch", "KineSketch"
            )
        )
        self.reasoning_group = QtWidgets.QGroupBox(App.Qt.translate("KineSketch", "Thinking excerpt"), container)
        reasoning_layout = QtWidgets.QVBoxLayout(self.reasoning_group)
        reasoning_layout.setContentsMargins(6, 4, 6, 4)
        self.reasoning_view = QtWidgets.QPlainTextEdit(self.reasoning_group)
        self.reasoning_view.setReadOnly(True)
        self.reasoning_view.setMaximumHeight(100)
        reasoning_layout.addWidget(self.reasoning_view)
        self.reasoning_group.hide()
        self.reply_group = QtWidgets.QGroupBox(App.Qt.translate("KineSketch", "Reply (streaming)"), container)
        reply_layout = QtWidgets.QVBoxLayout(self.reply_group)
        reply_layout.setContentsMargins(6, 4, 6, 4)
        self.reply_view = QtWidgets.QPlainTextEdit(self.reply_group)
        self.reply_view.setReadOnly(True)
        self.reply_view.setMaximumHeight(160)
        reply_layout.addWidget(self.reply_view)
        self.reply_group.hide()

        self.prompt_edit = QtWidgets.QPlainTextEdit(container)
        self.prompt_edit.setPlaceholderText(
            App.Qt.translate("KineSketch", "Message...")
        )
        self.prompt_edit.setMinimumHeight(70)
        self.prompt_edit.setMaximumHeight(110)
        self.prompt_edit.installEventFilter(self)

        status_layout = QtWidgets.QHBoxLayout()
        status_layout.setContentsMargins(2, 0, 2, 0)
        self.status_indicator = QtWidgets.QLabel(container)
        self.status_indicator.setFixedSize(10, 10)
        self.status_indicator.setAccessibleName(
            App.Qt.translate("KineSketch", "Agent status")
        )
        self.status_label = QtWidgets.QLabel(container)
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(QtCore.Qt.PlainText)
        self.status_label.setMinimumWidth(0)
        status_layout.addWidget(self.status_indicator)
        status_layout.addWidget(self.status_label, 1)

        self.status_progress = QtWidgets.QProgressBar(container)
        self.status_progress.setTextVisible(False)
        self.status_progress.setFixedHeight(4)

        controls = QtWidgets.QHBoxLayout()
        self.image_button = QtWidgets.QToolButton(container)
        self.image_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileIcon))
        self.image_button.setToolTip(App.Qt.translate("KineSketch", "Attach image"))
        self.image_button.setAccessibleName(App.Qt.translate("KineSketch", "Attach image"))
        self.image_button.setFixedSize(32, 32)
        self.image_label = QtWidgets.QLabel(container)
        self.image_label.setTextFormat(QtCore.Qt.PlainText)
        self.image_label.setWordWrap(True)
        self.image_label.setMinimumWidth(0)
        self.image_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.remove_image_button = QtWidgets.QToolButton(container)
        self.remove_image_button.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.SP_DialogCancelButton)
        )
        self.remove_image_button.setToolTip(App.Qt.translate("KineSketch", "Remove image"))
        self.remove_image_button.setAccessibleName(App.Qt.translate("KineSketch", "Remove image"))
        self.remove_image_button.setFixedSize(28, 28)
        self.attachment_row = QtWidgets.QWidget(container)
        attachments = QtWidgets.QHBoxLayout(self.attachment_row)
        attachments.setContentsMargins(2, 0, 0, 0)
        attachments.addWidget(self.image_label, 1)
        attachments.addWidget(self.remove_image_button)
        self.attachment_row.hide()
        self.clear_button = QtWidgets.QToolButton(container)
        self.clear_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogResetButton))
        self.clear_button.setToolTip(App.Qt.translate("KineSketch", "Clear conversation"))
        self.clear_button.setAccessibleName(App.Qt.translate("KineSketch", "Clear conversation"))
        self.clear_button.setFixedSize(32, 32)
        self.send_button = QtWidgets.QPushButton(
            App.Qt.translate("KineSketch", "Send"), container
        )
        self.send_button.setDefault(True)
        self.send_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ArrowForward))
        self.send_button.setToolTip(App.Qt.translate("KineSketch", "Send (Ctrl+Enter)"))
        self.send_button.setMinimumWidth(84)
        self.send_button.setFixedHeight(32)
        self.stop_button = QtWidgets.QPushButton(container)
        self.stop_button.setFixedHeight(32)
        self._update_stop_button()
        controls.addWidget(self.image_button)
        controls.addWidget(self.clear_button)
        controls.addStretch()
        controls.addWidget(self.stop_button)
        controls.addWidget(self.send_button)

        settings_layout.addWidget(connection)
        settings_layout.addWidget(ssh_connection)
        layout.addLayout(header)
        layout.addWidget(self.settings_area)
        layout.addWidget(self.transcript, 1)
        layout.addWidget(self.reasoning_group)
        layout.addWidget(self.reply_group)
        layout.addLayout(status_layout)
        layout.addWidget(self.status_progress)
        layout.addWidget(self.prompt_edit)
        layout.addWidget(self.attachment_row)
        layout.addLayout(controls)
        self.setWidget(container)

        self.send_button.clicked.connect(self._send)
        self.clear_button.clicked.connect(self._clear)
        self.stop_button.clicked.connect(self._stop_conversation)
        self.image_button.clicked.connect(self._attach_image)
        self.remove_image_button.clicked.connect(self._remove_image)
        self.settings_button.toggled.connect(self._toggle_settings)
        self.model_edit.textChanged.connect(self._update_connection_summary)
        self.endpoint_edit.textChanged.connect(self._update_connection_summary)
        self.ssh_connection.toggled.connect(self._update_connection_summary)
        self.provider_combo.currentIndexChanged.connect(self._change_provider)
        saved_provider = str(self._settings.value("provider", "compatible"))
        index = self.provider_combo.findData(saved_provider)
        if index > 0:
            self.provider_combo.setCurrentIndex(index)
        self._toggle_settings(self._settings.value("ui/settings_expanded", False, type=bool))
        self._update_connection_summary()
        self._set_status(App.Qt.translate("KineSketch", "Ready"), "ready")

    def _save_profile(self) -> None:
        prefix = f"profiles/{self._provider}/"
        self._settings.setValue(prefix + "endpoint", self.endpoint_edit.text().strip())
        self._settings.setValue(prefix + "model", self.model_edit.text().strip())
        self._settings.setValue(prefix + "ssh", self.ssh_connection.isChecked())
        self._profile_tokens[self._provider] = self.api_key_edit.text()

    def _change_provider(self, _index: int) -> None:
        if self._busy:
            return
        self._save_profile()
        self._settings.setValue("max_tokens", self.max_tokens_edit.value())
        self._provider = str(self.provider_combo.currentData())
        cloud = self._provider.startswith("stepfun")
        self.thinking_combo.model().item(1).setEnabled(not cloud)
        if cloud and self.thinking_combo.currentData() == "none":
            self.thinking_combo.setCurrentIndex(0)
        default_endpoint = ("https://api.stepfun.ai/v1" if self._provider == "stepfun-international"
                            else "https://api.stepfun.com/v1") if cloud else "http://127.0.0.1:18789/v1"
        prefix = f"profiles/{self._provider}/"
        self.endpoint_edit.setText(str(self._settings.value(prefix + "endpoint", default_endpoint)))
        self.model_combo.setEditText(str(self._settings.value(prefix + "model", "step-3.5-flash" if cloud else "openclaw/default")))
        self.ssh_connection.setChecked(self._settings.value(prefix + "ssh", not cloud, type=bool))
        self.api_key_edit.setText(self._profile_tokens.get(self._provider, ""))
        self.api_key_edit.setPlaceholderText(App.Qt.translate("KineSketch", "API key; not saved" if cloud else "Optional; not saved"))
        self._settings.setValue("provider", self._provider)
        self._update_connection_summary()

    def _toggle_settings(self, expanded: bool) -> None:
        self.settings_button.setChecked(expanded)
        self.settings_area.setVisible(expanded)
        self._settings.setValue("ui/settings_expanded", expanded)

    def _update_connection_summary(self, *_args) -> None:
        model = self.model_edit.text().strip()
        self.connection_summary.setText(model.rsplit("/", 1)[-1] or App.Qt.translate("KineSketch", "Agent"))
        transport = "SSH" if self.ssh_connection.isChecked() else "HTTP"
        self.connection_summary.setToolTip(f"{model}\n{transport}: {self.endpoint_edit.text().strip()}")

    def eventFilter(self, watched, event):
        if (watched is self.prompt_edit and event.type() == QtCore.QEvent.KeyPress
                and event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
                and event.modifiers() & QtCore.Qt.ControlModifier):
            if not event.isAutoRepeat():
                self._send()
            return True
        return super().eventFilter(watched, event)

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
        self.image_label.setToolTip(image.filename)
        self.attachment_row.show()
        self.remove_image_button.setVisible(True)

    @QtCore.Slot()
    def _remove_image(self) -> None:
        if self._busy:
            return
        self._image = None
        self.image_label.clear()
        self.image_label.setToolTip("")
        self.attachment_row.hide()
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
                "dimensions and units. Ask for missing dimensions before modifying "
                "geometry; do not infer exact sizes from an unscaled photo."
            )
        if not endpoint or not model:
            self._toggle_settings(True)
            self._append("System", "Endpoint and model are required.")
            self._set_status(
                App.Qt.translate("KineSketch", "Configuration required"), "error"
            )
            return
        if self.ssh_connection.isChecked() and (
            not self.ssh_host_edit.text().strip()
            or not self.ssh_user_edit.text().strip()
        ):
            self._toggle_settings(True)
            self._append("System", "SSH server and username are required.")
            self._set_status(
                App.Qt.translate("KineSketch", "SSH configuration required"),
                "error",
            )
            return

        try:
            validate_access_token(self.api_key_edit.text())
        except AgentClientError as error:
            self._toggle_settings(True)
            self.api_key_edit.setFocus()
            self._append("System", str(error))
            self._set_status(App.Qt.translate("KineSketch", "Invalid access token"), "error")
            return

        if self._provider.startswith("stepfun"):
            if not self.api_key_edit.text().strip():
                self._toggle_settings(True)
                self.api_key_edit.setFocus()
                self._append("System", "StepFun requires an API key.")
                self._set_status("API key required", "error")
                return
            if self._image is not None and model in ("step-3.5-flash", "step-3.5-flash-2603"):
                self._toggle_settings(True)
                self._append("System", "This StepFun model is text-only. Select step-3.7-flash or step-5-preview for images.")
                self._set_status("Vision model required", "warning")
                return

        self._save_profile()
        if self._provider == "compatible":
            self._settings.setValue("endpoint", endpoint)
            self._settings.setValue("model", model)
        self._settings.setValue("max_tokens", self.max_tokens_edit.value())
        self._settings.setValue("timeout", self.timeout_edit.value())
        self._settings.setValue("reasoning_effort", self.thinking_combo.currentData())
        if self._provider == "compatible":
            self._settings.setValue("ssh/enabled", self.ssh_connection.isChecked())
        self._settings.setValue("ssh/host", self.ssh_host_edit.text().strip())
        self._settings.setValue("ssh/port", self.ssh_port_edit.value())
        self._settings.setValue("ssh/user", self.ssh_user_edit.text().strip())
        connection = (self._provider, endpoint, model, self.ssh_connection.isChecked(),
                      self.ssh_host_edit.text().strip(), self.ssh_port_edit.value(),
                      self.ssh_user_edit.text().strip())
        if self._last_connection is not None and connection != self._last_connection:
            self._session.clear()
            self._conversation_id = self._new_conversation_id()
        self._last_connection = connection
        self._reasoning_text = ""
        self._reasoning_parts.clear()
        self._pending_viewport_images.clear()
        self.reasoning_view.clear()
        self.reasoning_group.hide()
        self.reply_view.clear()
        self.reply_group.hide()
        self._session.begin(prompt, document_summary(), self._image)
        self._append("You", prompt)
        if self._image is not None:
            self._append("Image", self._image.filename)
        self._remove_image()
        self.prompt_edit.clear()
        self._stopped = False
        self._request_model()

    def _request_model(self, status_message: str | None = None) -> None:
        if self._stopped:
            return
        try:
            tools = self._session.request_tools(TOOL_DEFINITIONS)
        except ValueError as error:
            self._handle_error(str(error))
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
            provider=self._provider,
            tool_choice=self._session.tool_choice,
            max_tokens=self.max_tokens_edit.value(),
            timeout=self.timeout_edit.value(),
            reasoning_effort=self.thinking_combo.currentData(),
        )
        self._set_busy(True)
        self._set_status(
            status_message or App.Qt.translate("KineSketch", "Connecting to agent..."),
            "busy",
            busy=True,
        )
        self._streaming_reply_started = False
        self._stream_parts.clear()
        self.reply_view.clear()
        self.reply_group.hide()
        self.reply_group.setTitle(App.Qt.translate("KineSketch", "Reply (streaming)"))
        self.reasoning_group.setTitle(App.Qt.translate("KineSketch", "Thinking excerpt (live)"))
        self._reply_handled = False
        self._request_id += 1

        thread = QtCore.QThread(self)
        worker = _RequestWorker(config, self._session.request_messages, self._request_id, tools=tools)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.content_delta.connect(self._receive_content_delta)
        worker.reasoning_delta.connect(self._receive_reasoning_delta)
        worker.status_changed.connect(self._receive_status)
        worker.completed.connect(self._receive_reply)
        worker.failed.connect(self._receive_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._request_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        self._requests[thread] = worker
        thread.start()

    @QtCore.Slot(int, str)
    def _receive_content_delta(self, request_id: int, content: str) -> None:
        if request_id == self._request_id and not self._stopped:
            self._handle_content_delta(content)

    @QtCore.Slot(int, str)
    def _receive_reasoning_delta(self, request_id: int, content: str) -> None:
        if request_id != self._request_id or self._stopped:
            return
        if content:
            self._reasoning_parts[:] = [
                ("".join(self._reasoning_parts) + content)[-REASONING_PREVIEW_LIMIT:]
            ]
            if not self._stream_timer.isActive():
                self._stream_timer.start()
        if not self._streaming_reply_started and not self._stream_parts:
            self._set_status("Thinking...", "busy", busy=True)

    @QtCore.Slot(int, str)
    def _receive_status(self, request_id: int, status: str) -> None:
        if request_id == self._request_id and not self._stopped:
            self._set_status(App.Qt.translate("KineSketch", status), "busy", busy=True)

    @QtCore.Slot(int, object)
    def _receive_reply(self, request_id: int, message: dict[str, Any]) -> None:
        if request_id == self._request_id and not self._stopped:
            self._handle_reply(message)

    @QtCore.Slot(int, str)
    def _receive_error(self, request_id: int, message: str) -> None:
        if request_id == self._request_id and not self._stopped:
            self._handle_error(message)

    @QtCore.Slot(object)
    def _handle_reply(self, message: dict[str, Any]) -> None:
        if self._stopped:
            return
        self._flush_stream()
        self._reply_handled = True
        try:
            empty_error = message.get("_empty_response_error")
            tool_calls = [] if empty_error else self._session.accept_assistant(message)
            self.reply_group.hide()
            if tool_calls:
                self._append_unstreamed_content(message)
                self._set_status(
                    App.Qt.translate("KineSketch", "Applying agent actions..."),
                    "warning",
                    busy=True,
                )
                self._pending_tools.extend(tool_calls)
                self._tools_running = True
                self._schedule_tool_step()
                return
            if self._session.retry_missing_tool():
                self._continue_after_finish = True
                self._set_status("Requesting modeling tool call...", "busy", busy=True)
                return
            if self._session.creation_requested and not self._session.creation_succeeded:
                if self._session.creation_attempted:
                    detail = self._session.creation_error or "No modeling tool completed successfully."
                    self._append("System", f"Model creation failed: {detail}")
                else:
                    self._append("System", "The model did not call a modeling tool after one retry. No CAD model was created.")
                    if message.get("content"):
                        self._append("Agent response (not executed)", str(message["content"]))
                self._set_status("Model not created", "error")
                return
            if empty_error:
                if self._session.creation_succeeded:
                    self._append("KineSketch", self._session.creation_summary)
                else:
                    self._handle_error(str(empty_error))
                    return
            else:
                self._append_unstreamed_content(message)
            self.reasoning_group.setTitle(App.Qt.translate("KineSketch", "Thinking excerpt (complete)"))
            self._set_status(
                App.Qt.translate("KineSketch", "Answer complete"), "ready"
            )
        except Exception as error:
            self._append("System", str(error))
            self._set_status(
                App.Qt.translate("KineSketch", "Agent action failed"), "error"
            )
        finally:
            self._advance_request()

    def _schedule_tool_step(self) -> None:
        if not self._stopped and not self._tool_step_scheduled:
            self._tool_step_scheduled = True
            request_id = self._request_id
            QtCore.QTimer.singleShot(0, lambda: self._run_next_tool(request_id))

    def _run_next_tool(self, request_id: int) -> None:
        # Yield between actions so Stop can discard the remaining queue.
        if request_id != self._request_id:
            return
        self._tool_step_scheduled = False
        if self._stopped or not self._tools_running:
            return
        try:
            if self._pending_tools:
                call = self._pending_tools.popleft()
                result = execute_tool_call(call)
                name = str(call.get("function", {}).get("name", ""))
                try:
                    data = json.loads(result)
                except (TypeError, json.JSONDecodeError):
                    data = None
                if isinstance(data, dict):
                    image = data.pop("_image_data_url", None)
                    if name == "capture_viewport" and data.get("ok") is True and isinstance(image, str):
                        self._pending_viewport_images.append(image)
                        self._pending_viewport_images[:] = self._pending_viewport_images[-2:]
                    result = json.dumps(data, ensure_ascii=False)
                    if name in CREATION_TOOLS:
                        target = data.get("label") or data.get("name") or data.get("run_directory")
                        self._append("Tool", f"{name}: " + (
                            f"created {target or 'model'}" if data.get("ok") is True
                            else str(data.get("error", "Operation failed"))))
                self._session.add_tool_result(str(call.get("id", "")), result)
                self._schedule_tool_step()
                return
            if self._pending_viewport_images:
                self._session.add_viewport_images(self._pending_viewport_images, post_action=False)
                self._pending_viewport_images.clear()
            self._session.continue_after_tools()
            self._continue_after_finish = True
        except Exception as error:
            self._pending_tools.clear()
            self._pending_viewport_images.clear()
            self._append("System", str(error))
            self._set_status(App.Qt.translate("KineSketch", "Agent action failed"), "error")
        self._tools_running = False
        self._advance_request()

    def _update_stop_button(self) -> None:
        self.stop_button.setText(App.Qt.translate("KineSketch", "Stop"))
        self.stop_button.setToolTip(App.Qt.translate("KineSketch", "Stop conversation"))
        self.stop_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaStop))
        self.stop_button.setEnabled(self._busy)

    @QtCore.Slot()
    def _stop_conversation(self) -> None:
        if not self._busy:
            return
        self._stopped = True
        self._stream_timer.stop()
        self._stream_parts.clear()
        self._reasoning_parts.clear()
        self.reasoning_group.setTitle(App.Qt.translate("KineSketch", "Thinking excerpt (stopped)"))
        self.reply_group.setTitle(App.Qt.translate("KineSketch", "Reply (stopped)"))
        self._request_id += 1
        self._pending_tools.clear()
        self._pending_viewport_images.clear()
        self._tools_running = False
        self._tool_step_scheduled = False
        self._continue_after_finish = False
        self._reply_handled = True
        if self._worker is not None:
            self._worker.cancel()
        # Retain old workers until cleanup; let the user start another turn now.
        self._thread = None
        self._worker = None
        self._session.cancel_turn()
        self._set_busy(False)
        self._append("System", App.Qt.translate("KineSketch", "Conversation stopped"))
        self._set_status(App.Qt.translate("KineSketch", "Conversation stopped"), "ready")

    @QtCore.Slot(str)
    def _handle_content_delta(self, content: str) -> None:
        if not content or self._stopped:
            return
        self._stream_parts.append(content)
        if not self._stream_timer.isActive():
            self._stream_timer.start()
        self._set_status(
            App.Qt.translate("KineSketch", "Receiving response..."), "busy", busy=True,
        )

    @QtCore.Slot()
    def _flush_stream(self) -> None:
        self._stream_timer.stop()
        if self._stopped:
            return
        if self._reasoning_parts:
            self.reasoning_group.show()
            self._reasoning_text = (
                self._reasoning_text + "".join(self._reasoning_parts)
            )[-REASONING_PREVIEW_LIMIT:]
            self._replace_preview(self.reasoning_view, self._reasoning_text)
            self._reasoning_parts.clear()
        if not self._stream_parts:
            return
        content = "".join(self._stream_parts)
        self._stream_parts.clear()
        if not self._streaming_reply_started:
            self.reply_group.show()
            self._streaming_reply_started = True
        self._append_preview(self.reply_view, content)

    @staticmethod
    def _replace_preview(view, text: str) -> None:
        scrollbar = view.verticalScrollBar()
        previous = scrollbar.value()
        follow = previous >= scrollbar.maximum() - 24
        view.setPlainText(text)
        scrollbar.setValue(scrollbar.maximum() if follow else previous)

    @staticmethod
    def _append_preview(view, text: str) -> None:
        scrollbar = view.verticalScrollBar()
        previous = scrollbar.value()
        follow = previous >= scrollbar.maximum() - 24
        cursor = QtGui.QTextCursor(view.document())
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        scrollbar.setValue(scrollbar.maximum() if follow else previous)

    @QtCore.Slot(str)
    def _handle_error(self, message: str) -> None:
        if self._stopped:
            return
        self._flush_stream()
        self.reply_group.setTitle(App.Qt.translate("KineSketch", "Reply (incomplete)"))
        self.reasoning_group.setTitle(App.Qt.translate("KineSketch", "Thinking excerpt (incomplete)"))
        self._reply_handled = True
        self._append("System", message)
        self._set_status(App.Qt.translate("KineSketch", "Request failed"), "error")
        self._advance_request()

    @QtCore.Slot()
    def _request_finished(self) -> None:
        thread = self.sender()
        self._requests.pop(thread, None)
        if thread is not self._thread:
            return
        self._thread = None
        self._worker = None
        self._advance_request()

    def _advance_request(self) -> None:
        if self._stopped or self._thread is not None or self._tools_running or not self._reply_handled:
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
        self._pending_viewport_images.clear()
        self._stream_parts.clear()
        self._reasoning_parts.clear()
        self._reasoning_text = ""
        self.reasoning_view.clear()
        self.reasoning_group.hide()
        self.reply_view.clear()
        self.reply_group.hide()
        self._remove_image()
        self._conversation_id = self._new_conversation_id()
        self.transcript.clear()
        self._set_status(App.Qt.translate("KineSketch", "Ready"), "ready")

    @staticmethod
    def _new_conversation_id() -> str:
        return f"kinesketch:{uuid4().hex}"

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_stop_button()
        self.model_connection.setEnabled(not busy)
        self.ssh_connection.setEnabled(not busy)
        self.prompt_edit.setEnabled(not busy)
        self.image_button.setEnabled(not busy)
        self.remove_image_button.setEnabled(not busy)
        self.clear_button.setEnabled(not busy)
        self.send_button.setEnabled(not busy)

    def _set_status(self, message: str, state: str, *, busy: bool = False) -> None:
        signature = (message, state, busy)
        if signature == self._status_signature:
            return
        self._status_signature = signature
        self.status_progress.setVisible(busy)
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
        self._begin_stream(speaker)
        self._append_stream_text(text)

    def _append_unstreamed_content(self, message: dict[str, Any]) -> None:
        content = message.get("content") or ""
        if content:
            self._append("Agent actions" if message.get("tool_calls") else "KineSketch", str(content))

    def _begin_stream(self, speaker: str) -> None:
        scrollbar = self.transcript.verticalScrollBar()
        previous = scrollbar.value()
        follow = previous >= scrollbar.maximum() - 24
        cursor = QtGui.QTextCursor(self.transcript.document())
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        block = QtGui.QTextBlockFormat()
        block.setTopMargin(12)
        block.setBottomMargin(4)
        heading = QtGui.QTextCharFormat()
        heading.setFontWeight(QtGui.QFont.Bold)
        heading.setForeground(self.palette().color(QtGui.QPalette.Highlight if speaker == "You" else QtGui.QPalette.Text))
        if not self.transcript.document().isEmpty():
            cursor.insertBlock(block, heading)
        else:
            cursor.setBlockFormat(block)
        cursor.insertText(App.Qt.translate("KineSketch", speaker), heading)
        body = QtGui.QTextCharFormat()
        body.setFontWeight(QtGui.QFont.Normal)
        body.setForeground(self.palette().color(QtGui.QPalette.Text))
        cursor.insertBlock(QtGui.QTextBlockFormat(), body)
        scrollbar.setValue(scrollbar.maximum() if follow else previous)

    def _append_stream_text(self, text: str) -> None:
        scrollbar = self.transcript.verticalScrollBar()
        previous = scrollbar.value()
        follow = previous >= scrollbar.maximum() - 24
        cursor = QtGui.QTextCursor(self.transcript.document())
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        scrollbar.setValue(scrollbar.maximum() if follow else previous)


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
