# SPDX-License-Identifier: LGPL-2.1-or-later

"""OpenAI-compatible HTTP client with optional SSH port forwarding."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import SplitResult, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .ssh_tunnel import SSHConfig, SSHTunnel


class AgentClientError(RuntimeError):
    """Raised when the configured model endpoint cannot return a valid reply."""


@dataclass(frozen=True)
class AgentConfig:
    """Connection settings for an OpenAI-compatible chat completion endpoint."""

    endpoint: str
    model: str
    api_key: str = ""
    conversation_id: str = ""
    timeout: float = 90.0
    ssh: SSHConfig | None = None

    @property
    def chat_completions_url(self) -> str:
        endpoint = self.endpoint.rstrip("/")
        if endpoint.endswith("/chat/completions"):
            return endpoint
        return f"{endpoint}/chat/completions"


class AgentClient(Protocol):
    """Transport boundary implemented by remote agent backends."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


class OpenAICompatibleClient:
    """Send chat completion requests without adding a runtime dependency."""

    def __init__(self, config: AgentConfig) -> None:
        if not config.endpoint.strip():
            raise ValueError("Agent endpoint is required")
        if not config.model.strip():
            raise ValueError("Agent model is required")
        self.config = config

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if self.config.conversation_id:
            payload["user"] = self.config.conversation_id
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "KineSketch-FreeCAD-Agent/1.0",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        request = Request(
            self.config.chat_completions_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise AgentClientError(
                f"Model endpoint returned HTTP {error.code}: {detail}"
            ) from error
        except (URLError, TimeoutError) as error:
            raise AgentClientError(f"Cannot reach model endpoint: {error}") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AgentClientError("Model endpoint returned invalid JSON") from error

        try:
            message = result["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise AgentClientError("Model response contains no assistant message") from error
        if not isinstance(message, dict):
            raise AgentClientError("Model response contains an invalid assistant message")
        return message


class SSHTunneledAgentClient:
    """Reach an HTTP agent endpoint through a key-authenticated SSH tunnel."""

    def __init__(self, config: AgentConfig) -> None:
        if config.ssh is None:
            raise ValueError("SSH settings are required")
        self.config = config

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        parsed = _parse_tunnel_endpoint(self.config.endpoint)
        remote_port = parsed.port or 80
        with SSHTunnel(self.config.ssh, parsed.hostname or "", remote_port) as tunnel:
            local_endpoint = urlunsplit(
                parsed._replace(netloc=f"127.0.0.1:{tunnel.local_port}")
            )
            direct_config = replace(self.config, endpoint=local_endpoint, ssh=None)
            return OpenAICompatibleClient(direct_config).complete(messages, tools)


def _parse_tunnel_endpoint(endpoint: str) -> SplitResult:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("SSH tunnel endpoint must be an absolute HTTP URL")
    return parsed


def create_agent_client(config: AgentConfig) -> AgentClient:
    """Create the configured remote agent transport."""
    if config.ssh is not None:
        return SSHTunneledAgentClient(config)
    return OpenAICompatibleClient(config)
