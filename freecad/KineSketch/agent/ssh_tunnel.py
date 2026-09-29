# SPDX-License-Identifier: LGPL-2.1-or-later

"""System OpenSSH port forwarding for the remote agent."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from contextlib import nullcontext
from dataclasses import dataclass
from .cancellation import RequestCancellation


class SSHTunnelError(RuntimeError):
    """Raised when an SSH tunnel cannot be opened."""


@dataclass(frozen=True)
class SSHConfig:
    """Connection settings for an SSH server with preconfigured key authentication."""

    host: str
    port: int
    username: str
    timeout: float = 15.0


class SSHTunnel:
    """Forward a random local TCP port through the system OpenSSH client."""

    def __init__(self, config: SSHConfig, remote_host: str, remote_port: int,
                 cancellation: RequestCancellation | None = None) -> None:
        self.config = config
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.local_port: int | None = None
        self._process: subprocess.Popen[str] | None = None
        self._cancellation = cancellation

    def __enter__(self) -> SSHTunnel:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def open(self) -> None:
        if self._cancellation is not None:
            self._cancellation.check()
        if self._process is not None:
            return
        self._validate()

        executable = shutil.which("ssh")
        if executable is None:
            raise SSHTunnelError(
                "OpenSSH client was not found. Install or enable the system 'ssh' command."
            )

        local_port = _reserve_local_port()
        forward_target = _forward_target(local_port, self.remote_host, self.remote_port)
        timeout = max(1, int(self.config.timeout))
        command = [
            executable,
            "-N",
            "-o",
            "BatchMode=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            f"ConnectTimeout={timeout}",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-L",
            forward_target,
            "-p",
            str(self.config.port),
            f"{self.config.username}@{self.config.host}",
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )
        except OSError as error:
            raise SSHTunnelError(f"Cannot start OpenSSH: {error}") from error

        try:
            def interrupt():
                if process.poll() is None:
                    process.terminate()

            binding = self._cancellation.bind(interrupt) if self._cancellation is not None else nullcontext()
            with binding:
                self._wait_until_ready(process, local_port)
                if self._cancellation is not None:
                    self._cancellation.check()
        except Exception:
            _stop_process(process)
            raise

        self._process = process
        self.local_port = local_port

    def close(self) -> None:
        if self._process is not None:
            _stop_process(self._process)
            self._process = None
        self.local_port = None

    def _wait_until_ready(self, process: subprocess.Popen[str], port: int) -> None:
        deadline = time.monotonic() + self.config.timeout
        while time.monotonic() < deadline:
            if self._cancellation is not None:
                self._cancellation.check()
            if process.poll() is not None:
                detail = process.stderr.read().strip() if process.stderr else ""
                message = detail or f"OpenSSH exited with code {process.returncode}"
                raise SSHTunnelError(
                    "Cannot open SSH tunnel: "
                    f"{message}. Verify passwordless key login and known_hosts first."
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        raise SSHTunnelError(
            f"SSH tunnel did not become ready within {self.config.timeout:g} seconds"
        )

    def _validate(self) -> None:
        if not self.config.host.strip() or self.config.host.startswith("-"):
            raise SSHTunnelError("SSH host is required and must not start with '-'")
        if not 1 <= self.config.port <= 65535:
            raise SSHTunnelError("SSH port must be between 1 and 65535")
        if not self.config.username.strip() or self.config.username.startswith("-"):
            raise SSHTunnelError("SSH username is required and must not start with '-'")
        if not self.remote_host.strip():
            raise SSHTunnelError("Remote agent host is required")
        if not 1 <= self.remote_port <= 65535:
            raise SSHTunnelError("Remote agent port must be between 1 and 65535")


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _forward_target(local_port: int, remote_host: str, remote_port: int) -> str:
    host = f"[{remote_host}]" if ":" in remote_host else remote_host
    return f"127.0.0.1:{local_port}:{host}:{remote_port}"


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
    if process.stderr is not None:
        process.stderr.close()
