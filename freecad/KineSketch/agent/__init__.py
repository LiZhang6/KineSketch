# SPDX-License-Identifier: LGPL-2.1-or-later

"""KineSketch's FreeCAD-integrated AI agent."""

from .client import (
	AgentClient,
	AgentClientError,
	AgentConfig,
	OpenAICompatibleClient,
	create_agent_client,
)

__all__ = [
	"AgentClient",
	"AgentClientError",
	"AgentConfig",
	"OpenAICompatibleClient",
	"create_agent_client",
]
