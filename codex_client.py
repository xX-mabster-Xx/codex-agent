import asyncio
from collections.abc import Hashable
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import signal
from typing import Any
import tomllib


log = logging.getLogger(__name__)

# MCP tools can legitimately return a sizeable JSON document. `StreamReader.readline`
# uses a 64 KiB default limit, which made one large tool result silently terminate the
# stdout reader and left the Telegram UI waiting forever. We read JSONL ourselves
# below and still keep a hard upper bound to avoid accepting an unbounded stream.
APP_SERVER_READ_CHUNK_BYTES = 64 * 1024
APP_SERVER_MAX_MESSAGE_BYTES = 16 * 1024 * 1024
APP_SERVER_STREAM_BUFFER_BYTES = 1024 * 1024


class CodexRPCError(RuntimeError):
    pass


class CodexTransportError(CodexRPCError):
    pass


class CodexThreadCorruptError(CodexRPCError):
    pass


@dataclass(slots=True)
class ServerRequest:
    id: str | int
    method: str
    params: dict[str, Any]


class CodexClient:
    """Small JSONL/JSON-RPC client for `codex app-server --stdio`."""

    def __init__(
        self,
        project_dir: Path,
        proxy_url: str | None = None,
        extra_env: dict[str, str] | None = None,
        profile: str | None = None,
        provider_env: dict[str, str] | None = None,
        runtime_config_overrides: list[str] | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.proxy_url = proxy_url
        self.extra_env = extra_env or {}
        self.profile = profile
        self.provider_env = provider_env or {}
        # Runtime limits belong to the Telegram integration and intentionally
        # win over a profile's local defaults.
        self.config_overrides = [
            *self._profile_overrides(profile),
            *(runtime_config_overrides or []),
        ]
        self.events: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self.server_requests: asyncio.Queue[ServerRequest] = asyncio.Queue()
        self._item_snapshots: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[Hashable, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []
        self._recovery_task: asyncio.Task[None] | None = None
        self.generation = 0
        self._integrity_errors = 0

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._process and self._process.returncode is None:
                return
            await self._start_unlocked()

    async def _start_unlocked(self) -> None:
        log.info(
            "Starting codex app-server cwd=%s proxy=%s profile=%s",
            self.project_dir,
            bool(self.proxy_url),
            self.profile or "default",
        )
        environment = os.environ.copy()
        # These credentials belong exclusively to the Telegram frontend.  Keep
        # them out of every tool and MCP process spawned by Codex.
        environment.pop("BOT_TOKEN", None)
        environment.pop("OPENROUTER_API_KEY", None)
        environment.pop("GOOGLE_OAUTH_CLIENT_ID", None)
        environment.pop("GOOGLE_OAUTH_CLIENT_SECRET", None)
        environment.update(self.extra_env)
        # `provider_env` is assembled from the active profile's declared
        # `env_key`, never from the complete bot environment.
        environment.update(self.provider_env)
        if self.proxy_url:
            for name in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            ):
                environment[name] = self.proxy_url
            # The shared Telegram MCP is intentionally bound to loopback only.
            # Do not send those local MCP requests through the SOCKS proxy:
            # apart from being unnecessary, the proxy cannot reach the caller's
            # own loopback namespace and responds with a transport failure.
            for name in ("NO_PROXY", "no_proxy"):
                current = environment.get(name, "").strip().strip(",")
                local_hosts = "127.0.0.1,localhost,::1"
                environment[name] = f"{current},{local_hosts}" if current else local_hosts
        command = ["codex"]
        # Current Codex builds deliberately reject `--profile` for
        # `app-server`, although they accept it for the interactive runtime.
        # Layer the profile's relevant TOML values through supported `-c`
        # overrides instead.  The normal user config is still loaded first.
        for override in self.config_overrides:
            command.extend(("--config", override))
        command.extend(("app-server", "--stdio"))
        self._process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.project_dir,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=APP_SERVER_STREAM_BUFFER_BYTES,
        )
        process = self._process
        self._tasks = [
            asyncio.create_task(self._read_stdout(process), name="codex-stdout"),
            asyncio.create_task(self._read_stderr(process), name="codex-stderr"),
        ]
        log.info("codex app-server started pid=%s", process.pid)
        try:
            await self._call_raw(
                "initialize",
                {
                    "clientInfo": {
                        "name": "telegram-codex-mvp",
                        "title": "Telegram Codex MVP",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
                timeout=15,
            )
            await self.notify("initialized")
        except Exception:
            await self._stop_unlocked("initialization failed")
            raise
        self.generation += 1
        log.info("codex app-server initialized")

    @classmethod
    def _profile_overrides(cls, profile: str | None) -> list[str]:
        if not profile:
            return []
        path = Path.home() / ".codex" / f"{profile}.config.toml"
        try:
            with path.open("rb") as source:
                values = tomllib.load(source)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise CodexRPCError(f"Could not load Codex profile {profile!r}: {error}") from error
        overrides: list[str] = []
        for name, value in values.items():
            # These persist UI/project preferences only; forwarding them is
            # unnecessary for a headless app-server and can leak old model UI
            # state into the profile.
            if name in {"tui", "projects"}:
                continue
            cls._append_toml_overrides(overrides, name, value)
        return overrides

    @classmethod
    def _append_toml_overrides(
        cls, target: list[str], key: str, value: Any
    ) -> None:
        if isinstance(value, dict):
            for child, child_value in value.items():
                cls._append_toml_overrides(target, f"{key}.{cls._toml_key(str(child))}", child_value)
            return
        target.append(f"{key}={cls._toml_value(value)}")

    @staticmethod
    def _toml_key(value: str) -> str:
        return value if value.replace("_", "").isalnum() else json.dumps(value)

    @classmethod
    def _toml_value(cls, value: Any) -> str:
        if isinstance(value, str):
            return json.dumps(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return "[" + ", ".join(cls._toml_value(item) for item in value) + "]"
        raise CodexRPCError(f"Unsupported Codex profile value: {value!r}")

    async def call(
        self, method: str, params: dict[str, Any], timeout: float = 30
    ) -> dict[str, Any]:
        process = self._process
        if not process or process.returncode is not None:
            await self.restart("process is not running", expected_process=process)
            raise CodexRPCError(
                f"codex app-server was not running and has been restarted; retry {method}"
            )

        integrity_before = self._integrity_errors
        try:
            result = await self._call_raw(method, params, timeout)
            if method == "thread/resume":
                await asyncio.sleep(0.05)
                if self._integrity_errors > integrity_before:
                    raise CodexThreadCorruptError(
                        "Codex thread contains an unfinished tool call"
                    )
            return result
        except TimeoutError as error:
            log.error("RPC timeout method=%s after=%ss", method, timeout)
            restarted = await self.restart(
                f"RPC timeout in {method}", expected_process=process
            )
            suffix = "app-server restarted" if restarted else "restart already completed"
            raise CodexRPCError(
                f"codex app-server did not answer {method} in {timeout:g}s; {suffix}"
            ) from error
        except CodexThreadCorruptError:
            raise
        except CodexTransportError:
            await self.restart(
                f"transport failed during {method}", expected_process=process
            )
            raise
        except CodexRPCError:
            if self._process is process and process.returncode is not None:
                await self.restart(
                    f"process stopped during {method}", expected_process=process
                )
            raise

    async def _call_raw(
        self, method: str, params: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        log.debug("RPC request id=%s method=%s", request_id, method)
        try:
            await self._send(
                {"id": request_id, "method": method, "params": params}
            )
            result = await asyncio.wait_for(future, timeout)
            log.debug("RPC response id=%s method=%s ok", request_id, method)
            return result
        except (CodexRPCError, TimeoutError):
            log.exception("RPC request failed id=%s method=%s", request_id, method)
            raise
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        log.debug("RPC notification method=%s", method)
        await self._send(message)

    def item_snapshot(
        self, thread_id: Any, turn_id: Any, item_id: Any
    ) -> dict[str, Any] | None:
        """Return the latest item payload correlated with an approval request."""
        return self._item_snapshots.get(
            (str(thread_id), str(turn_id), str(item_id))
        )

    async def respond(self, request_id: str | int, result: dict[str, Any]) -> None:
        log.debug("RPC server response id=%s", request_id)
        await self._send({"id": request_id, "result": result})

    async def respond_error(
        self, request_id: str | int, code: int, message: str
    ) -> None:
        await self._send(
            {"id": request_id, "error": {"code": code, "message": message}}
        )

    async def close(self) -> None:
        recovery_task = self._recovery_task
        if recovery_task and recovery_task is not asyncio.current_task():
            recovery_task.cancel()
            await asyncio.gather(recovery_task, return_exceptions=True)
        async with self._lifecycle_lock:
            await self._stop_unlocked("client shutdown")

    async def restart(
        self,
        reason: str,
        *,
        expected_process: asyncio.subprocess.Process | None = None,
    ) -> bool:
        async with self._lifecycle_lock:
            if expected_process is not None and self._process is not expected_process:
                return False
            log.warning("Restarting codex app-server: %s", reason)
            await self._stop_unlocked(reason)
            await self._start_unlocked()
            return True

    async def _stop_unlocked(self, reason: str) -> None:
        log.info("Stopping codex app-server reason=%s", reason)
        process = self._process
        tasks = self._tasks
        self._process = None
        self._tasks = []
        error = CodexTransportError(f"codex app-server stopped: {reason}")
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        if process and process.returncode is None:
            self._signal_process_group(process, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                log.warning("Force-killing codex app-server process group pid=%s", process.pid)
                self._signal_process_group(process, signal.SIGKILL)
                await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._drain_queue(self.events)
        self._drain_queue(self.server_requests)
        self._item_snapshots.clear()
        log.info("codex app-server stopped")

    @staticmethod
    def _drain_queue(queue: asyncio.Queue[Any]) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    @staticmethod
    def _signal_process_group(
        process: asyncio.subprocess.Process, sig: signal.Signals
    ) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    async def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if not process or not process.stdin or process.returncode is not None:
            raise CodexRPCError("codex app-server is not running")
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        try:
            async with self._write_lock:
                process.stdin.write(data.encode() + b"\n")
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as error:
            raise CodexTransportError(
                "codex app-server stdio transport failed"
            ) from error

    async def _read_stdout(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout
        failure_reason = "app-server stdout closed unexpectedly"
        buffer = bytearray()
        try:
            while chunk := await process.stdout.read(APP_SERVER_READ_CHUNK_BYTES):
                buffer.extend(chunk)
                while True:
                    line_end = buffer.find(b"\n")
                    if line_end < 0:
                        break
                    line = bytes(buffer[:line_end])
                    del buffer[: line_end + 1]
                    if len(line) > APP_SERVER_MAX_MESSAGE_BYTES:
                        failure_reason = (
                            "app-server emitted a JSON-RPC message larger than "
                            f"{APP_SERVER_MAX_MESSAGE_BYTES // (1024 * 1024)} MiB"
                        )
                        log.error("%s; restarting app-server", failure_reason)
                        return
                    try:
                        message = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        log.warning("Invalid app-server output: %r", line[:500])
                        continue
                    self._dispatch(message)
                if len(buffer) > APP_SERVER_MAX_MESSAGE_BYTES:
                    failure_reason = (
                        "app-server emitted an unterminated JSON-RPC message larger than "
                        f"{APP_SERVER_MAX_MESSAGE_BYTES // (1024 * 1024)} MiB"
                    )
                    log.error("%s; restarting app-server", failure_reason)
                    return
            if buffer:
                log.warning("Discarding incomplete app-server JSON-RPC output: %r", buffer[:500])
        except asyncio.CancelledError:
            raise
        except Exception:
            failure_reason = "app-server stdout reader crashed"
            log.exception("%s", failure_reason)
        finally:
            if self._process is process:
                error = CodexTransportError(
                    f"codex app-server transport failed: {failure_reason}"
                )
                for future in tuple(self._pending.values()):
                    if not future.done():
                        future.set_exception(error)
                # An EOF is never a normal state for the long-lived app-server.
                # `_stop_unlocked` clears `self._process` before cancelling this
                # task, so reaching this branch means a real transport failure
                # whether the child is still alive or has already exited.
                self._schedule_recovery(process, failure_reason)

    def _schedule_recovery(
        self, process: asyncio.subprocess.Process, reason: str
    ) -> None:
        if self._recovery_task and not self._recovery_task.done():
            return
        self._recovery_task = asyncio.create_task(
            self._recover_stdout_failure(process, reason),
            name="codex-stdout-recovery",
        )

    async def _recover_stdout_failure(
        self, process: asyncio.subprocess.Process, reason: str
    ) -> None:
        try:
            await asyncio.sleep(0)
            restarted = await self.restart(reason, expected_process=process)
            if restarted:
                log.warning("Recovered codex app-server after stdout failure: %s", reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Could not recover codex app-server after stdout failure")
        finally:
            if self._recovery_task is asyncio.current_task():
                self._recovery_task = None

    def _dispatch(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if request_id is not None and ("result" in message or "error" in message):
            future = self._pending.get(request_id)
            if not future or future.done():
                return
            if "error" in message:
                error = message["error"]
                future.set_exception(
                    CodexRPCError(
                        f"{error.get('code', 'RPC')}: {error.get('message', error)}"
                    )
                )
            else:
                future.set_result(message.get("result") or {})
            return

        method = message.get("method")
        params = message.get("params") or {}
        if method in {"item/started", "item/completed"}:
            item = params.get("item") or {}
            item_id = item.get("id")
            thread_id = params.get("threadId")
            turn_id = params.get("turnId")
            if item_id is not None and thread_id is not None and turn_id is not None:
                self._item_snapshots[
                    (str(thread_id), str(turn_id), str(item_id))
                ] = item
        elif method == "turn/completed":
            thread_id = str(params.get("threadId"))
            turn_id = str(params.get("turn", {}).get("id") or params.get("turnId"))
            self._item_snapshots = {
                key: value
                for key, value in self._item_snapshots.items()
                if key[:2] != (thread_id, turn_id)
            }
        if not method:
            log.debug("Ignoring app-server message: %r", message)
        elif request_id is not None:
            log.debug("RPC server request id=%s method=%s", request_id, method)
            self.server_requests.put_nowait(ServerRequest(request_id, method, params))
        else:
            log.debug("RPC event method=%s", method)
            self.events.put_nowait((method, params))

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr
        while line := await process.stderr.readline():
            text = line.decode(errors="replace").rstrip()
            if "Custom tool call output is missing" in text:
                self._integrity_errors += 1
            log.warning("codex: %s", text)
