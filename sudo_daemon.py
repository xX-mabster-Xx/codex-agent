#!/usr/bin/env python3
"""Local root executor with an out-of-band Telegram approval gate.

The daemon exposes only a Unix socket and accepts requests only from one Linux
UID.  A submitted command is *not* executed immediately: it stays pending
until the separate root-approval Telegram bot explicitly approves or rejects
it through the same socket.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import logging
import os
import secrets
import signal
import socket
import stat
import struct
import time
from pathlib import Path
from typing import Any


MAX_REQUEST_BYTES = 32_768
MAX_RESPONSE_BYTES = 65_536
MAX_COMMAND_CHARS = 3_500
MAX_CONTEXT_CHARS = 1_000
MAX_PENDING_REQUESTS = 32
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 900
SAFE_ENV = {
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}

log = logging.getLogger("codex_sudo_daemon")


class RequestError(ValueError):
    """A malformed or unsafe protocol request."""


@dataclass(slots=True)
class PendingApproval:
    request_id: str
    command: str
    cwd: Path
    timeout_seconds: int
    reason: str
    expected_result: str
    risks: str
    created_at: float
    decision: asyncio.Future[bool]

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "command": self.command,
            "cwd": str(self.cwd),
            "timeout_seconds": self.timeout_seconds,
            "reason": self.reason,
            "expected_result": self.expected_result,
            "risks": self.risks,
            "created_at": self.created_at,
        }


class ApprovalBroker:
    def __init__(self, approval_timeout_seconds: int) -> None:
        self.approval_timeout_seconds = approval_timeout_seconds
        self._pending: dict[str, PendingApproval] = {}
        self._lock = asyncio.Lock()

    async def submit(
        self,
        *,
        command: str,
        cwd: Path,
        timeout_seconds: int,
        reason: str,
        expected_result: str,
        risks: str,
    ) -> dict[str, Any]:
        async with self._lock:
            if len(self._pending) >= MAX_PENDING_REQUESTS:
                raise RequestError("Too many root commands are awaiting approval")
            request_id = secrets.token_urlsafe(18)
            pending = PendingApproval(
                request_id=request_id,
                command=command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                reason=reason,
                expected_result=expected_result,
                risks=risks,
                created_at=time.time(),
                decision=asyncio.get_running_loop().create_future(),
            )
            self._pending[request_id] = pending

        log.warning(
            "Root command awaiting Telegram approval id=%s cwd=%s command=%r",
            request_id,
            cwd,
            command,
        )
        try:
            allowed = await asyncio.wait_for(
                asyncio.shield(pending.decision), timeout=self.approval_timeout_seconds
            )
        except TimeoutError:
            log.warning("Root command approval expired id=%s", request_id)
            return {
                "ok": False,
                "approval": "expired",
                "error": "Root command was not approved before the approval deadline",
            }
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)

        if not allowed:
            log.warning("Root command rejected in Telegram id=%s", request_id)
            return {
                "ok": False,
                "approval": "rejected",
                "error": "Root command was rejected in the root-approval Telegram bot",
            }
        result = await run_command(command, cwd, timeout_seconds)
        result["approval"] = "approved"
        result["request_id"] = request_id
        return result

    async def list_pending(self) -> dict[str, Any]:
        async with self._lock:
            requests = [item.as_public_dict() for item in self._pending.values()]
        return {"ok": True, "requests": requests}

    async def decide(self, request_id: str, allowed: bool) -> dict[str, Any]:
        async with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending.decision.done():
                return {"ok": False, "error": "Approval request is no longer pending"}
            pending.decision.set_result(allowed)
        log.warning(
            "Root command Telegram decision id=%s decision=%s",
            request_id,
            "approved" if allowed else "rejected",
        )
        return {"ok": True, "decision": "approved" if allowed else "rejected"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Root executor for the Codex sudo MCP")
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--allowed-uid", type=int, required=True)
    parser.add_argument("--approval-timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    if not 30 <= args.approval_timeout_seconds <= 900:
        parser.error("--approval-timeout-seconds must be between 30 and 900")
    return args


def peer_uid(writer: asyncio.StreamWriter) -> int:
    transport_socket = writer.get_extra_info("socket")
    if transport_socket is None:
        raise PermissionError("Unix socket peer credentials are unavailable")
    credentials = transport_socket.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
    )
    return struct.unpack("3i", credentials)[1]


def prepare_socket_path(socket_path: Path) -> None:
    parent = socket_path.parent
    parent.mkdir(mode=0o711, parents=True, exist_ok=True)
    parent_stat = parent.stat()
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != 0:
        raise RuntimeError(f"Unsafe socket directory: {parent}")
    os.chmod(parent, 0o711)

    try:
        socket_stat = socket_path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(socket_stat.st_mode):
        raise RuntimeError(f"Refusing to replace a non-socket path: {socket_path}")
    socket_path.unlink()


def _required_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_CONTEXT_CHARS:
        raise RequestError(
            f"{name} must be a non-empty string of at most {MAX_CONTEXT_CHARS} characters"
        )
    return value.strip()


def validate_submit_request(payload: Any) -> tuple[str, Path, int, str, str, str]:
    if not isinstance(payload, dict):
        raise RequestError("Request must be a JSON object")

    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise RequestError("command must be a non-empty string")
    if "\x00" in command or len(command) > MAX_COMMAND_CHARS:
        raise RequestError(
            f"command contains invalid characters or is longer than {MAX_COMMAND_CHARS} characters"
        )

    cwd_value = payload.get("cwd", "/")
    if not isinstance(cwd_value, str) or "\x00" in cwd_value:
        raise RequestError("cwd must be a path string")
    cwd = Path(cwd_value)
    if not cwd.is_absolute() or not cwd.is_dir():
        raise RequestError("cwd must be an existing absolute directory")

    timeout = payload.get("timeout_seconds", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise RequestError("timeout_seconds must be an integer")
    if not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
        raise RequestError(
            f"timeout_seconds must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS}"
        )
    return (
        command,
        cwd,
        timeout,
        _required_text(payload, "reason"),
        _required_text(payload, "expected_result"),
        _required_text(payload, "risks"),
    )


def validate_decision(payload: Any) -> tuple[str, bool]:
    if not isinstance(payload, dict):
        raise RequestError("Request must be a JSON object")
    request_id = payload.get("request_id")
    decision = payload.get("decision")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise RequestError("request_id is invalid")
    if decision not in {"approve", "reject"}:
        raise RequestError("decision must be approve or reject")
    return request_id, decision == "approve"


async def read_limited(stream: asyncio.StreamReader) -> tuple[str, bool]:
    content = bytearray()
    truncated = False
    while chunk := await stream.read(4096):
        remaining = MAX_RESPONSE_BYTES - len(content)
        if remaining > 0:
            content.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return content.decode("utf-8", errors="replace"), truncated


async def terminate_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            await process.wait()


async def run_command(command: str, cwd: Path, timeout: int) -> dict[str, Any]:
    log.warning("Root command approved cwd=%s command=%r", cwd, command)
    process = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-lc",
        command,
        cwd=str(cwd),
        env=SAFE_ENV,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(read_limited(process.stdout))
    stderr_task = asyncio.create_task(read_limited(process.stderr))
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        timed_out = True
        await terminate_process_group(process)
    readers = asyncio.gather(stdout_task, stderr_task)
    try:
        (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.wait_for(
            asyncio.shield(readers), timeout=5
        )
    except TimeoutError:
        timed_out = True
        await terminate_process_group(process)
        (stdout, stdout_truncated), (stderr, stderr_truncated) = await readers
    result = {
        "ok": not timed_out and process.returncode == 0,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "output_truncated": stdout_truncated or stderr_truncated,
    }
    log.warning(
        "Root command finished exit_code=%s timeout=%s output_truncated=%s command=%r",
        process.returncode,
        timed_out,
        result["output_truncated"],
        command,
    )
    return result


async def send_response(writer: asyncio.StreamWriter, response: dict[str, Any]) -> None:
    writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    await writer.drain()


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    allowed_uid: int,
    broker: ApprovalBroker,
) -> None:
    try:
        if peer_uid(writer) != allowed_uid:
            log.warning("Rejected sudo socket connection from unauthorized UID")
            return
        raw = await asyncio.wait_for(reader.readline(), timeout=15)
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            raise RequestError("Request is empty or too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise RequestError("Request must be a JSON object")
        action = payload.get("action", "submit")
        if action == "submit":
            command, cwd, timeout, reason, expected_result, risks = validate_submit_request(payload)
            response = await broker.submit(
                command=command,
                cwd=cwd,
                timeout_seconds=timeout,
                reason=reason,
                expected_result=expected_result,
                risks=risks,
            )
        elif action == "list_pending":
            response = await broker.list_pending()
        elif action == "decide":
            request_id, allowed = validate_decision(payload)
            response = await broker.decide(request_id, allowed)
        else:
            raise RequestError("action must be submit, list_pending, or decide")
    except (RequestError, json.JSONDecodeError) as error:
        log.warning("Rejected malformed root command request: %s", error)
        response = {"ok": False, "error": str(error)}
    except Exception as error:
        log.exception("Root command request failed")
        response = {"ok": False, "error": f"Executor error: {error}"}
    try:
        await send_response(writer, response)
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


async def main() -> None:
    args = parse_args()
    if os.geteuid() != 0:
        raise SystemExit("codex-sudo-daemon must run as root")
    prepare_socket_path(args.socket)
    broker = ApprovalBroker(args.approval_timeout_seconds)
    server = await asyncio.start_unix_server(
        lambda reader, writer: handle_client(reader, writer, args.allowed_uid, broker),
        path=str(args.socket),
    )
    os.chown(args.socket, args.allowed_uid, -1)
    os.chmod(args.socket, 0o600)
    log.warning(
        "Root executor listening on Unix socket %s for uid=%s approval_timeout=%ss",
        args.socket,
        args.allowed_uid,
        args.approval_timeout_seconds,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
