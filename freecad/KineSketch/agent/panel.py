# SPDX-License-Identifier: LGPL-2.1-or-later

"""Qt dock widget for the FreeCAD-integrated KineSketch agent."""

from __future__ import annotations

import os
from typing import Any, ClassVar

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from .client import AgentConfig, OpenAICompatibleClient
from .session import AgentSession
from .tools import TOOL_DEFINITIONS, document_summary, execute_tool_call


class _RequestWorker(QtCore.QObject):
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, config: AgentConfig, messages: list[dict[str, Any]]) -> None:
        super().__init__()
        self._config = config
        self._messages = messages

    @QtCore.Slot()
    def run(self) -> None:
        try:
            reply = OpenAICompatibleClient(self._config).complete(
                self._messages, TOOL_DEFINITIONS
            )
        except Exception as error:
            self.failed.emit(str(error))
            return
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
        self._thread: QtCore.QThread | None = None
        self._worker: _RequestWorker | None = None
        self._continue_after_finish = False
        self._settings = QtCore.QSettings("KineSketch", "Agent")
        self._build_ui()

    def _build_ui(self) -> None:
        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)

        connection = QtWidgets.QGroupBox(
            App.Qt.translate("KineSketch", "Model connection"), container
        )
        form = QtWidgets.QFormLayout(connection)
        self.endpoint_edit = QtWidgets.QLineEdit(
            self._settings.value(
                "endpoint",
                os.getenv("KINESKETCH_ENDPOINT", "http://127.0.0.1:11434/v1"),
            )
        )
        self.model_edit = QtWidgets.QLineEdit(
            self._settings.value(
                "model", os.getenv("KINESKETCH_MODEL", "qwen2.5:7b")
            )
        )
        self.api_key_edit = QtWidgets.QLineEdit(os.getenv("KINESKETCH_API_KEY", ""))
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(
            App.Qt.translate("KineSketch", "Optional; not saved")
        )
        form.addRow(App.Qt.translate("KineSketch", "Endpoint"), self.endpoint_edit)
        form.addRow(App.Qt.translate("KineSketch", "Model"), self.model_edit)
        form.addRow(App.Qt.translate("KineSketch", "API key"), self.api_key_edit)

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
        layout.addWidget(self.transcript, 1)
        layout.addWidget(self.prompt_edit)
        layout.addLayout(controls)
        self.setWidget(container)

        self.send_button.clicked.connect(self._send)
        self.clear_button.clicked.connect(self._clear)

    @QtCore.Slot()
    def _send(self) -> None:
        if self._thread is not None:
            return
        prompt = self.prompt_edit.toPlainText().strip()
        endpoint = self.endpoint_edit.text().strip()
        model = self.model_edit.text().strip()
        if not prompt:
            return
        if not endpoint or not model:
            self._append("System", "Endpoint and model are required.")
            return

        self._settings.setValue("endpoint", endpoint)
        self._settings.setValue("model", model)
        self._session.begin(prompt, document_summary())
        self._append("You", prompt)
        self.prompt_edit.clear()
        self._request_model()

    def _request_model(self) -> None:
        config = AgentConfig(
            endpoint=self.endpoint_edit.text().strip(),
            model=self.model_edit.text().strip(),
            api_key=self.api_key_edit.text(),
        )
        self.send_button.setEnabled(False)
        self.send_button.setText(App.Qt.translate("KineSketch", "Working..."))

        thread = QtCore.QThread(self)
        worker = _RequestWorker(config, self._session.messages)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
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
        try:
            tool_calls = self._session.accept_assistant(message)
            if tool_calls:
                for tool_call in tool_calls:
                    result = execute_tool_call(tool_call)
                    self._session.add_tool_result(str(tool_call.get("id", "")), result)
                self._session.continue_after_tools()
                self._continue_after_finish = True
                return
            content = message.get("content") or ""
            self._append("KineSketch", str(content))
        except Exception as error:
            self._append("System", str(error))

    @QtCore.Slot(str)
    def _handle_error(self, message: str) -> None:
        self._append("System", message)

    @QtCore.Slot()
    def _request_finished(self) -> None:
        self._thread = None
        self._worker = None
        self.send_button.setEnabled(True)
        self.send_button.setText(App.Qt.translate("KineSketch", "Send"))
        if self._continue_after_finish:
            self._continue_after_finish = False
            self._request_model()

    @QtCore.Slot()
    def _clear(self) -> None:
        if self._thread is not None:
            return
        self._session.clear()
        self.transcript.clear()

    def _append(self, speaker: str, text: str) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(cursor.End)
        if not self.transcript.document().isEmpty():
            cursor.insertText("\n\n")
        cursor.insertText(f"{speaker}\n{text}")
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