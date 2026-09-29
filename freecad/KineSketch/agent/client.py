# SPDX-License-Identifier: LGPL-2.1-or-later

"""OpenAI-compatible HTTP client with optional SSH port forwarding."""

from __future__ import annotations

import json
import socket
import io
import time
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPSConnection, HTTPException, HTTPResponse
from dataclasses import dataclass, replace
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import SplitResult, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .ssh_tunnel import SSHConfig, SSHTunnel
from .cancellation import RequestCancellation


class AgentClientError(RuntimeError):
    """Raised when the configured model endpoint cannot return a valid reply."""


ContentCallback = Callable[[str], None]


def validate_access_token(token: str) -> str:
    """Normalize pasted tokens without exposing their contents in diagnostics."""
    token = token.strip()
    if not token.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in token):
        raise AgentClientError(
            "Access token must contain only printable ASCII characters without whitespace. "
            "Remove pasted notes; leave this field empty if the service requires no token."
        )
    return token


@dataclass(frozen=True)
class AgentConfig:
    """Connection settings for an OpenAI-compatible chat completion endpoint."""

    endpoint: str
    model: str
    api_key: str = ""
    conversation_id: str = ""
    timeout: float = 90.0
    ssh: SSHConfig | None = None
    max_tokens: int | None = None
    think: bool | None = None
    tool_choice: str | dict[str, Any] = "auto"

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
        on_content: ContentCallback | None = None,
        tool_choice: str = "auto",
        cancellation: RequestCancellation | None = None,
    ) -> dict[str, Any]: ...


class OpenAICompatibleClient:
    """Send chat completion requests without adding a runtime dependency."""

    def __init__(self, config: AgentConfig) -> None:
        if not config.endpoint.strip():
            raise ValueError("Agent endpoint is required")
        if not config.model.strip():
            raise ValueError("Agent model is required")
        self._api_key = validate_access_token(config.api_key)
        self.config = config

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_content: ContentCallback | None = None,
        tool_choice: str = "auto",
        cancellation: RequestCancellation | None = None,
    ) -> dict[str, Any]:
        if cancellation is not None:
            cancellation.check()
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if self.config.conversation_id:
            payload["user"] = self.config.conversation_id
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens
        if self.config.think is not None:
            payload["think"] = self.config.think
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if on_content is not None:
            payload["stream"] = True

        headers = {
            "Accept": "text/event-stream" if on_content is not None else "application/json",
            "Content-Type": "application/json",
            "User-Agent": "KineSketch-FreeCAD-Agent/1.0",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        request = Request(
            self.config.chat_completions_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with _open_response(request, self.config.timeout, cancellation) as response:
                if on_content is not None:
                    return _read_streaming_message(response, on_content, cancellation)
                result = json.loads(response.read().decode("utf-8"))
                if cancellation is not None:
                    cancellation.check()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise AgentClientError(
                f"Model endpoint returned HTTP {error.code}: {detail}"
            ) from error
        except (URLError, OSError, HTTPException) as error:
            if cancellation is not None:
                cancellation.check()
            raise AgentClientError(f"Cannot reach model endpoint: {error}") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AgentClientError("Model endpoint returned invalid JSON") from error

        try:
            message = result["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise AgentClientError("Model response contains no assistant message") from error
        if not isinstance(message, dict):
            raise AgentClientError("Model response contains an invalid assistant message")
        if not message.get("content") and not message.get("tool_calls"):
            raise AgentClientError(
                "Model returned no text or tool calls; increase the endpoint's output token limit"
            )
        return message


class SSHTunneledAgentClient:
    """Reach an HTTP agent endpoint through a key-authenticated SSH tunnel."""

    def __init__(self, config: AgentConfig) -> None:
        if config.ssh is None:
            raise ValueError("SSH settings are required")
        validate_access_token(config.api_key)
        self.config = config

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_content: ContentCallback | None = None,
        tool_choice: str = "auto",
        cancellation: RequestCancellation | None = None,
    ) -> dict[str, Any]:
        parsed = _parse_tunnel_endpoint(self.config.endpoint)
        remote_port = parsed.port or 80
        options = {"cancellation": cancellation} if cancellation is not None else {}
        with SSHTunnel(self.config.ssh, parsed.hostname or "", remote_port, **options) as tunnel:
            local_endpoint = urlunsplit(
                parsed._replace(netloc=f"127.0.0.1:{tunnel.local_port}")
            )
            direct_config = replace(self.config, endpoint=local_endpoint, ssh=None)
            return OpenAICompatibleClient(direct_config).complete(
                messages,
                tools,
                on_content=on_content,
                tool_choice=tool_choice,
                **options,
            )


@contextmanager
def _open_response(request: Request, timeout: float, cancellation: RequestCancellation | None):
    if cancellation is None:
        with urlopen(request, timeout=timeout) as response:
            yield response
        return
    parsed = urlsplit(request.full_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Agent endpoint must be an absolute HTTP or HTTPS URL")
    connection_type = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    connection.response_class = lambda sock, **kwargs: HTTPResponse(
        _ResponseSocket(sock, cancellation, timeout), **kwargs)
    try:
        cancellation.check()
        connection.connect()
        transport = connection.sock
        # Interrupt the socket immediately; response cleanup stays on the worker.
        def interrupt():
            try:
                transport.shutdown(socket.SHUT_RDWR)
            finally:
                transport.close()

        with cancellation.bind(interrupt):
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection.request(request.get_method(), path, body=request.data,
                               headers=dict(request.header_items()))
            with connection.getresponse() as response:
                cancellation.check()
                if response.status >= 400:
                    detail = response.read().decode("utf-8", errors="replace")
                    cancellation.check()
                    raise AgentClientError(f"Model endpoint returned HTTP {response.status}: {detail}")
                yield response
    finally:
        connection.close()


class _ResponseSocket:
    def __init__(self, transport, cancellation, timeout):
        self.transport = transport
        self.cancellation = cancellation
        self.timeout = timeout

    def makefile(self, mode):
        return io.BufferedReader(_ResponseReader(self.transport, self.cancellation, self.timeout))


class _ResponseReader(io.RawIOBase):
    """Poll cancellation without losing partially buffered HTTP/SSE lines."""

    def __init__(self, transport, cancellation, timeout):
        super().__init__()
        self.transport = transport
        self.cancellation = cancellation
        self.timeout = timeout
        self._owner = None
        cancellation.check()
        transport.settimeout(min(0.2, timeout))
        # Retain the socket while HTTPConnection releases a Connection: close response.
        self._owner = transport.makefile("rb", buffering=0)

    def readable(self):
        return True

    def close(self):
        try:
            if self._owner is not None:
                self._owner.close()
        finally:
            super().close()

    def readinto(self, buffer):
        deadline = time.monotonic() + self.timeout
        while True:
            self.cancellation.check()
            try:
                count = self.transport.recv_into(buffer)
                self.cancellation.check()
                return count
            except TimeoutError:
                self.cancellation.check()
                if time.monotonic() >= deadline:
                    raise


def _read_streaming_message(response: Any, on_content: ContentCallback,
                            cancellation: RequestCancellation | None = None) -> dict[str, Any]:
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    received_done = False

    try:
        for raw_line in response:
            if cancellation is not None:
                cancellation.check()
            line = raw_line.decode("utf-8").strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data = line[5:].lstrip()
            if data == "[DONE]":
                received_done = True
                break
            event = json.loads(data)
            if not isinstance(event, dict):
                raise AgentClientError("Model endpoint returned an invalid stream event")
            if "error" in event:
                raise AgentClientError(_stream_error_message(event["error"]))

            choices = event.get("choices")
            if not choices:
                continue
            if not isinstance(choices, list) or not isinstance(choices[0], dict):
                raise AgentClientError("Model endpoint returned invalid stream choices")
            delta = choices[0].get("delta", {})
            if not isinstance(delta, dict):
                raise AgentClientError("Model endpoint returned an invalid stream delta")

            content = delta.get("content")
            if isinstance(content, str) and content:
                content_parts.append(content)
                on_content(content)
            call_deltas = delta.get("tool_calls") or []
            if not isinstance(call_deltas, list):
                raise AgentClientError("Model endpoint returned invalid tool-call deltas")
            for call_delta in call_deltas:
                _merge_tool_call_delta(tool_calls, call_delta)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AgentClientError("Model endpoint returned an invalid SSE stream") from error

    if cancellation is not None:
        cancellation.check()
    if not received_done:
        raise AgentClientError("Model endpoint ended the stream before [DONE]")

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts) or None,
    }
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    if not message["content"] and not tool_calls:
        raise AgentClientError(
            "Model returned no text or tool calls; increase the endpoint's output token limit"
        )
    return message


def _merge_tool_call_delta(
    tool_calls: dict[int, dict[str, Any]], call_delta: Any
) -> None:
    if not isinstance(call_delta, dict):
        raise AgentClientError("Model endpoint returned an invalid tool-call delta")
    index = call_delta.get("index")
    if not isinstance(index, int) or index < 0:
        raise AgentClientError("Model endpoint returned a tool call without an index")

    call = tool_calls.setdefault(
        index,
        {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        },
    )
    if isinstance(call_delta.get("id"), str):
        call["id"] = call_delta["id"]
    if isinstance(call_delta.get("type"), str):
        call["type"] = call_delta["type"]

    function_delta = call_delta.get("function")
    if function_delta is None:
        return
    if not isinstance(function_delta, dict):
        raise AgentClientError("Model endpoint returned an invalid tool function")
    for field in ("name", "arguments"):
        value = function_delta.get(field)
        if isinstance(value, str):
            call["function"][field] += value


def _stream_error_message(error: Any) -> str:
    if isinstance(error, dict):
        detail = error.get("message") or error.get("code") or str(error)
    else:
        detail = str(error)
    return f"Model endpoint returned a streaming error: {detail}"


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
