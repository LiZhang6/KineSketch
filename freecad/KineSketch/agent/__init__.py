# SPDX-License-Identifier: LGPL-2.1-or-later

"""KineSketch's FreeCAD-integrated AI agent."""

from .client import AgentClientError, AgentConfig, OpenAICompatibleClient

__all__ = ["AgentClientError", "AgentConfig", "OpenAICompatibleClient"]