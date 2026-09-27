# SPDX-License-Identifier: LGPL-2.1-or-later

"""Example FreeCAD Workbench."""

import FreeCAD as App
import FreeCADGui as Gui

translate = App.Qt.translate

from .resources import Resources
from .commands import ExampleCommand

class KineSketchWorkbench(Gui.Workbench):

    MenuText: str = translate(
            "KineSketch",
            "Example Workbench",
        )

    ToolTip: str = translate(
            "KineSketch",
            "Example Workbench tooltip",
        )

    Icon: str = Resources.icon("KineSketch-wb.svg")


    def Initialize(self) -> None:
        App.Console.PrintMessage("Example Workbench initialized\n")
        # Adding menus and toolbars when the Workbench is active (example)
        commands = [ExampleCommand.Name]
        self.appendToolbar("KineSketch", commands)
        self.appendMenu("KineSketch", commands)

    def Activated(self) -> None:
        App.Console.PrintMessage("Example Workbench activated\n")

    def Deactivated(self) -> None:
        App.Console.PrintMessage("Example Workbench deactivated\n")

    def ContextMenu(self, recipient: str) -> None:
        App.Console.PrintMessage("Example Workbench context menu\n")
        # Adding context menus when the Workbench is active (example)
        self.appendContextMenu("", [ExampleCommand.Name])

    @classmethod
    def Install(cls) -> None:
        Gui.addWorkbench(cls)
