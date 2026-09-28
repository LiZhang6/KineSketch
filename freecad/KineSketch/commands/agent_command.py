# SPDX-License-Identifier: LGPL-2.1-or-later

"""Command that opens the KineSketch agent panel."""

from typing import ClassVar

import FreeCAD as App

from ..resources import Resources

translate = App.Qt.translate


class AgentCommand:
    """Open the integrated AI assistant."""

    Name: ClassVar[str] = "KineSketch_OpenAgent"

    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": Resources.icon("KineSketch.svg"),
            "MenuText": translate("KineSketch", "Open Agent"),
            "ToolTip": translate(
                "KineSketch", "Open the AI assistant for the active FreeCAD document"
            ),
        }

    def Activated(self) -> None:
        from ..agent.panel import show_agent_panel

        show_agent_panel()

    def IsActive(self) -> bool:
        return bool(App.GuiUp)

    @classmethod
    def Install(cls) -> None:
        if App.GuiUp:
            App.Gui.addCommand(cls.Name, cls())