import pytest

from nomad.common.env import deep_interp_env, interpolate_env


def test_interpolate_env_uses_existing_value(monkeypatch):
    monkeypatch.setenv("NOMAD_TEST_VALUE", "configured")

    assert (
        interpolate_env("prefix-${NOMAD_TEST_VALUE}-suffix")
        == "prefix-configured-suffix"
    )


def test_interpolate_env_uses_default_when_missing(monkeypatch):
    monkeypatch.delenv("NOMAD_TEST_MISSING", raising=False)

    assert interpolate_env("${NOMAD_TEST_MISSING:fallback}") == "fallback"


def test_interpolate_env_prefers_empty_env_to_default(monkeypatch):
    monkeypatch.setenv("NOMAD_TEST_EMPTY", "")

    assert interpolate_env("${NOMAD_TEST_EMPTY:fallback}") == ""


def test_interpolate_env_raises_when_missing_and_no_default(monkeypatch):
    monkeypatch.delenv("NOMAD_TEST_MISSING", raising=False)

    with pytest.raises(KeyError, match="NOMAD_TEST_MISSING"):
        interpolate_env("${NOMAD_TEST_MISSING}")


def test_deep_interp_env_recurses_through_nested_dicts(monkeypatch):
    monkeypatch.setenv("NOMAD_TEST_PRESENT", "ready")
    monkeypatch.delenv("NOMAD_TEST_DEFAULTED", raising=False)

    assert deep_interp_env(
        {
            "outer": {
                "present": "${NOMAD_TEST_PRESENT}",
                "defaulted": "${NOMAD_TEST_DEFAULTED:fallback}",
            },
            "literal": 7,
        }
    ) == {
        "outer": {"present": "ready", "defaulted": "fallback"},
        "literal": 7,
    }


def test_deep_interp_env_raises_for_missing_nested_value(monkeypatch):
    monkeypatch.delenv("NOMAD_TEST_MISSING", raising=False)

    with pytest.raises(KeyError, match="NOMAD_TEST_MISSING"):
        deep_interp_env({"outer": {"missing": "${NOMAD_TEST_MISSING}"}})


def test_deep_interp_env_recurses_through_lists(monkeypatch):
    monkeypatch.setenv("NOMAD_TEST_PRESENT", "ready")
    monkeypatch.delenv("NOMAD_TEST_DEFAULTED", raising=False)

    assert deep_interp_env(
        {
            "models": [
                {"name_or_path": "${NOMAD_TEST_PRESENT}"},
                {"name_or_path": "${NOMAD_TEST_DEFAULTED:fallback}"},
            ]
        }
    ) == {
        "models": [
            {"name_or_path": "ready"},
            {"name_or_path": "fallback"},
        ]
    }
