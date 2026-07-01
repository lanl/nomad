from __future__ import annotations

from pathlib import Path

import pytest

from nomad.gateway.config import GatewayConfig, GatewayDefaults
from nomad.gateway.server import CodeModeGateway


def _build_config(tmp_path: Path) -> GatewayConfig:
    defaults = GatewayDefaults(
        timeout_seconds=10,
        stdout_limit=64,
        stderr_limit=64,
        result_limit=256,
        workspace_root=tmp_path / "runs",
    )
    return GatewayConfig(servers={}, defaults=defaults, middleware=[])


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_stdio", [True, False])
async def test_run_code_non_zero_exit_returns_consistent_error(
    tmp_path: Path, capture_stdio: bool
) -> None:
    async with CodeModeGateway(_build_config(tmp_path)) as gateway:
        result = await gateway.run_code(
            "import sys\nsys.exit(23)\n",
            capture_stdio=capture_stdio,
        )

        assert result.returncode == 23
        assert result.duration_seconds >= 0
        assert result.result == {
            "error": "Sandbox execution returned non-zero exit code: 23"
        }


@pytest.mark.asyncio
async def test_run_code_large_result_returns_explicit_error(tmp_path: Path) -> None:
    async with CodeModeGateway(_build_config(tmp_path)) as gateway:
        result = await gateway.run_code(
            "RESULT = 'x' * 2048\n",
            capture_stdio=True,
        )

        assert result.returncode == 0
        assert result.duration_seconds >= 0
        assert result.result == {
            "error": (
                "RESULT is too large to return. "
                "Keep RESULT under about 64 B and write larger outputs to "
                "files instead."
            )
        }
