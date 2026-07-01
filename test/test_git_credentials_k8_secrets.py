import os
import shutil
import subprocess
from pathlib import Path

import pytest

HELPER = Path("container/git-credentials-k8-secrets.sh")


def run_helper(
    *,
    protocol: str = "https",
    host: str = "github.com",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    helper_env = os.environ.copy()
    if env is not None:
        helper_env.update(env)
    return subprocess.run(
        [str(HELPER), "get"],
        input=f"protocol={protocol}\nhost={host}\n\n",
        capture_output=True,
        check=False,
        env=helper_env,
        text=True,
    )


def assert_credentials(
    result: subprocess.CompletedProcess[str], *, username: str, password: str
) -> None:
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == f"username={username}\npassword={password}\n"


def test_reads_kubernetes_basic_auth_directory(tmp_path: Path):
    host_dir = tmp_path / "github.com"
    host_dir.mkdir()
    (host_dir / "username").write_text("oauth2\n", encoding="utf-8")
    (host_dir / "password").write_text("directory-token\n", encoding="utf-8")

    result = run_helper(env={"GIT_CREDENTIAL_PATH": str(tmp_path)})

    assert_credentials(result, username="oauth2", password="directory-token")


def test_reads_single_file_credential_store(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("git is required to exercise credential-store lookup")

    credentials = tmp_path / "git-credentials"
    subprocess.run(
        ["git", "credential-store", f"--file={credentials}", "store"],
        input=(
            "protocol=https\n"
            "host=github.com\n"
            "username=store-user\n"
            "password=store-token\n\n"
        ),
        check=True,
        text=True,
    )

    result = run_helper(env={"GIT_CREDENTIAL_PATH": str(credentials)})

    assert_credentials(result, username="store-user", password="store-token")


def test_falls_back_to_host_environment_variables(tmp_path: Path):
    result = run_helper(
        env={
            "GIT_CREDENTIAL_PATH": str(tmp_path / "missing"),
            "GIT_CREDENTIAL_GITHUB_COM_USERNAME": "env-user",
            "GIT_CREDENTIAL_GITHUB_COM_PASSWORD": "env-token",
        }
    )

    assert_credentials(result, username="env-user", password="env-token")


def test_environment_variable_host_normalization_supports_ports(tmp_path: Path):
    result = run_helper(
        host="git.example.com:8443",
        env={
            "GIT_CREDENTIAL_PATH": str(tmp_path / "missing"),
            "GIT_CREDENTIAL_GIT_EXAMPLE_COM_8443_USERNAME": "port-user",
            "GIT_CREDENTIAL_GIT_EXAMPLE_COM_8443_PASSWORD": "port-token",
        },
    )

    assert_credentials(result, username="port-user", password="port-token")


def test_missing_credentials_return_no_output(tmp_path: Path):
    result = run_helper(env={"GIT_CREDENTIAL_PATH": str(tmp_path / "missing")})

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_non_https_requests_return_no_output(tmp_path: Path):
    result = run_helper(
        protocol="ssh",
        env={
            "GIT_CREDENTIAL_PATH": str(tmp_path / "missing"),
            "GIT_CREDENTIAL_GITHUB_COM_USERNAME": "env-user",
            "GIT_CREDENTIAL_GITHUB_COM_PASSWORD": "env-token",
        },
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
