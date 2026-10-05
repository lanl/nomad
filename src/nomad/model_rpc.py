from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model_env import ModelEnvironment, PythonEnvironment

logger = logging.getLogger(__name__)


class ModelRPCError(RuntimeError):
    """Raised when an isolated model worker returns a JSON-RPC error."""


@dataclass(frozen=True, slots=True)
class RemoteModel:
    """Host-side metadata and loading instructions for an isolated model."""

    environment: PythonEnvironment
    model_class: str
    source: str
    name: str
    description: str
    batch_size: int
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    def load_params(self, device: str) -> dict[str, Any]:
        return {
            "model_class": self.model_class,
            "source": self.source,
            "tool_name": self.name,
            "batch_size": self.batch_size,
            "device": device,
        }


class ModelProcess:
    """A synchronous JSON-RPC client for one cached model environment."""

    def __init__(self, environment: PythonEnvironment) -> None:
        self.environment = environment
        python = environment.ensure()
        child_env = dict(os.environ)
        if isinstance(environment, ModelEnvironment):
            child_env.pop("PYTHONPATH", None)
        self._process = subprocess.Popen(
            [str(python), "-m", "nomad.model_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=child_env,
            cwd=environment.base_dir
            if isinstance(environment, ModelEnvironment)
            else None,
            start_new_session=os.name != "nt",
        )
        self._request_id = 0
        self._lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=100)
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            daemon=True,
            name=f"nomad-model-stderr-{self._process.pid}",
        )
        self._stderr_thread.start()

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._lock:
            if not self.alive:
                raise ModelRPCError(self._exited_message())
            self._request_id += 1
            request_id = self._request_id
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
            assert self._process.stdin is not None
            assert self._process.stdout is not None
            try:
                self._process.stdin.write(json.dumps(payload) + "\n")
                self._process.stdin.flush()
                response_line = self._process.stdout.readline()
            except (BrokenPipeError, OSError) as exc:
                raise ModelRPCError(self._exited_message()) from exc

            if not response_line:
                raise ModelRPCError(self._exited_message())
            try:
                response = json.loads(response_line)
            except json.JSONDecodeError as exc:
                raise ModelRPCError(
                    f"Model worker returned invalid JSON: {response_line.rstrip()}"
                ) from exc
            if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
                raise ModelRPCError(
                    "Model worker returned a mismatched JSON-RPC response"
                )
            if error := response.get("error"):
                message = error.get("message", "model worker error")
                raise ModelRPCError(str(message))
            return response.get("result")

    def load(self, remote: RemoteModel, device: str) -> dict[str, Any]:
        result = self.request("load", remote.load_params(device))
        if not isinstance(result, dict):
            raise ModelRPCError("Model worker returned invalid model metadata")
        return result

    def run_batch(
        self,
        inputs: list[dict[str, Any]],
        *,
        batch_size: int,
    ) -> list[Any]:
        result = self.request(
            "run_batch",
            {"inputs": inputs, "batch_size": batch_size},
        )
        if not isinstance(result, list):
            raise ModelRPCError("Model worker returned a non-list batch result")
        return result

    def close(self) -> None:
        with self._lock:
            if self.alive:
                try:
                    assert self._process.stdin is not None
                    self._request_id += 1
                    self._process.stdin.write(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": self._request_id,
                                "method": "shutdown",
                                "params": {},
                            }
                        )
                        + "\n"
                    )
                    self._process.stdin.flush()
                    self._process.wait(timeout=2)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    self._process.terminate()
            if self.alive:
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()

            for stream in (
                self._process.stdin,
                self._process.stdout,
                self._process.stderr,
            ):
                if stream is not None:
                    stream.close()

    def _drain_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            line = line.rstrip()
            self._stderr.append(line)
            logger.debug("model worker %s: %s", self.pid, line)

    def _exited_message(self) -> str:
        message = f"Model worker exited with status {self._process.poll()}"
        if self._stderr:
            message += f": {self._stderr[-1]}"
        return message

    def __enter__(self) -> ModelProcess:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def inspect_remote_model(
    *,
    environment: PythonEnvironment,
    model_class: str,
    source: str | Path,
    tool_name: str | None,
    batch_size: int | None,
) -> RemoteModel:
    """Load an isolated model once to retrieve its public tool metadata."""
    with ModelProcess(environment) as process:
        result = process.request(
            "load",
            {
                "model_class": model_class,
                "source": str(source),
                "tool_name": tool_name,
                "batch_size": batch_size,
                "device": "cpu",
            },
        )
    if not isinstance(result, dict):
        raise ModelRPCError("Model worker returned invalid model metadata")
    return RemoteModel(
        environment=environment,
        model_class=model_class,
        source=str(source),
        name=str(result["name"]),
        description=str(result["description"]),
        batch_size=max(1, int(result["batch_size"])),
        input_schema=dict(result["input_schema"]),
        output_schema=dict(result["output_schema"]),
    )


__all__ = [
    "ModelProcess",
    "ModelRPCError",
    "RemoteModel",
    "inspect_remote_model",
]
