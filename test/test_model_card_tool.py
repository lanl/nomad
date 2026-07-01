from __future__ import annotations

import pytest
from fastmcp import FastMCP

from nomad.model_cards import (
    MODEL_CARD_TOOL_NAME,
    ModelCardLocator,
    register_model_card_tool,
)


@pytest.mark.asyncio
async def test_model_card_tool_returns_local_readme(tmp_path):
    models_dir = tmp_path / "models"
    tool_name = "mist-28M-ggd8iisr-tmQM"
    readme_text = "# Local Card\n"

    model_dir = models_dir / tool_name
    model_dir.mkdir(parents=True)
    (model_dir / "README.md").write_text(readme_text, encoding="utf-8")

    server = FastMCP("test")
    locator = ModelCardLocator()
    register_model_card_tool(server, locator)
    locator.register(tool_name, model_dir)

    result = await server.call_tool(MODEL_CARD_TOOL_NAME, {"tool_name": tool_name})

    assert len(result.content) == 1
    assert result.content[0].text == readme_text


@pytest.mark.asyncio
async def test_model_card_tool_downloads_remote_readme(monkeypatch, tmp_path):
    repo_id = "mist-models/mist-remote"
    tool_name = "mist-models---mist-remote"
    remote_card = tmp_path / "remote.md"
    remote_card.write_text("# Remote Card\n", encoding="utf-8")

    def fake_hf_download(*, repo_id: str, filename: str) -> str:
        assert repo_id == "mist-models/mist-remote"
        assert filename == "README.md"
        return str(remote_card)

    monkeypatch.setattr("nomad.model_cards.hf_hub_download", fake_hf_download)

    server = FastMCP("test")
    locator = ModelCardLocator()
    register_model_card_tool(server, locator)
    locator.register(tool_name, repo_id)

    result = await server.call_tool(MODEL_CARD_TOOL_NAME, {"tool_name": tool_name})

    assert len(result.content) == 1
    assert result.content[0].text == "# Remote Card\n"


def test_model_card_locator_suggests_close_registered_tool_names():
    locator = ModelCardLocator()
    locator.register("nomad_demo_server__get_model_card", "mist-models/mist-remote")
    locator.register(
        "nomad_demo_server__diffunet2_heat_pli_f2l", "mist-models/mist-heat"
    )

    with pytest.raises(KeyError) as exc_info:
        locator.read_model_card("nomad_demo_server__get_model_crd")

    message = str(exc_info.value)
    assert (
        "No model registered with the name 'nomad_demo_server__get_model_crd'."
        in message
    )
    assert "'nomad_demo_server__get_model_card'" in message


def test_model_card_locator_suggests_alias_style_partial_matches():
    locator = ModelCardLocator()
    locator.register(
        "mist_models---mist_28M_97vfcykk_clintox",
        "mist-models/mist-28M-97vfcykk-clintox",
    )
    locator.register(
        "mist_models---mist_28M_kw4ks27p_tox21", "mist-models/mist-28M-kw4ks27p-tox21"
    )

    with pytest.raises(KeyError) as exc_info:
        locator.read_model_card("clintox")

    message = str(exc_info.value)
    assert "'mist_models---mist_28M_97vfcykk_clintox'" in message


def test_model_card_locator_omits_suggestions_when_no_close_match():
    locator = ModelCardLocator()
    locator.register("nomad_demo_server__get_model_card", "mist-models/mist-remote")

    with pytest.raises(KeyError) as exc_info:
        locator.read_model_card("totally_different_tool")

    message = str(exc_info.value)
    assert "No model registered with the name 'totally_different_tool'." in message
    assert "Did you mean:" not in message
