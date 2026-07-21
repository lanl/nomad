import os
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path

import truststore
from dotenv import dotenv_values, load_dotenv
from pygments.lexers import get_lexer_by_name
from sphinx.errors import SphinxError
from sphinx.highlighting import lexers

# Use system certificates
truststore.inject_into_ssl()

project = "nomad"
copyright = "2026, Nomad Contributors"
author = "Nomad Contributors"
release = f"v{importlib_metadata.version(project)}"
PROJECT_METADATA = importlib_metadata.metadata(project)


def _resolve_source_repository() -> str:
    home_page = PROJECT_METADATA.get("Home-page")
    if home_page:
        return home_page.rstrip("/")

    for entry in PROJECT_METADATA.get_all("Project-URL") or ():
        if "," not in entry:
            continue
        label, _, value = entry.partition(",")
        if label.strip().lower() in {"source", "repository", "homepage"}:
            return value.strip().rstrip("/")

    return "https://github.com/lanl/nomad"


source_repository = _resolve_source_repository().rstrip("/")
source_branch = "main"
source_directory = "docs"
docs_source_prefix = f"{source_branch}/{source_directory}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(DOCS_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=False)

extensions = [
    "myst_nb",
    "sphinx_click.ext",
    "sphinx_design",
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.extlinks",
    "sphinx_copybutton",
    "sphinx_llm.txt",
]

exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "**/.venv/**",
    "reference/generated/**",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "myst-nb",
    ".ipynb": "myst-nb",
}
root_doc = "index"
myst_heading_anchors = 3
suppress_warnings = [
    "myst.xref_missing",
    "intersphinx.external",
    "intersphinx.inventory",
]
intersphinx_cache_limit = 0
user_agent = "sphinx"

# -- Autodoc ----------------------------------------------------------------
autodoc_use_legacy_class_based = True
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "member-order": "bysource",
    "exclude-members": "model_config",
}
autodoc_typehints = "description"
autodoc_type_aliases = {
    "Path": "pathlib.Path",
    "Tensor": "nomad.well_format.Tensor",
    "T0_Tensor": "nomad.well_format.T0_Tensor",
    "T1_Tensor": "nomad.well_format.T1_Tensor",
    "T2_Tensor": "nomad.well_format.T2_Tensor",
    "ServerParameters": "nomad.gateway.config.ServerParameters",
    "MiddlewareEntry": "nomad.gateway.config.MiddlewareEntry",
}
intersphinx_mapping = {
    "python": (
        "https://docs.python.org/3",
        None,
    ),
    "pydantic": (
        "https://pydantic.dev/docs/validation/latest/",
        None,
    ),
    "torch": (
        "https://docs.pytorch.org/docs/stable/",
        None,
    ),
}
nitpick_ignore_regex = [
    ("py:class", r"Path"),
    ("py:class", r"TypeAliasForwardRef"),
    ("py:class", r"'pathlib\.Path'"),
    ("py:class", r"torch(\..*)?"),
    ("py:class", r"_asyncio\.Future"),
    ("py:class", r"(Input|Output|ModelInput|ModelOutput)"),
    ("py:class", r"(Tensor|T0_Tensor|T1_Tensor|T2_Tensor)"),
    ("py:class", r"(ServerParameters|MiddlewareEntry)"),
    ("py:class", r"annotated_types\.Gt"),
    ("py:class", r"gt=0"),
    ("py:class", r"func=.*"),
    ("py:class", r"json_schema_input_type=.*"),
    ("py:class", r"return_type=.*"),
    ("py:class", r"when_used=.*"),
    ("py:class", r"json_schema=.*"),
    ("py:class", r"'contentEncoding': .*"),
    ("py:class", r"'description': .*"),
    ("py:class", r".*base64 zstd-compressed torch serialization.*"),
    ("py:class", r"mode=.*"),
]

# External link aliases used throughout docs pages.
repo_blob_base = f"{source_repository}/blob/{source_branch}/"

extlinks = {
    "mcp": ("https://modelcontextprotocol.io/%s", "MCP %s"),
    "uv": ("https://docs.astral.sh/uv/%s", "uv %s"),
    "ursa": ("https://github.com/lanl/ursa/blob/main/%s", "ursa/%s"),
    "repo_file": (f"{repo_blob_base}%s", "%s"),
}

# -- Options for HTML output -------------------------------------------------
templates_path = ["_templates"]
html_static_path = ["_static"]
html_logo = str(PROJECT_ROOT.joinpath("assets", "icon.png"))
html_theme = "furo"
html_copy_source = False
html_show_sourcelink = True
html_theme_options = {
    "sidebar_hide_name": True,
    "source_repository": source_repository,
    "source_branch": source_branch,
    "source_directory": source_directory,
    "source_view_link": f"{source_repository}/blob/{docs_source_prefix}/{{filename}}",
    "source_edit_link": f"{source_repository}/edit/{docs_source_prefix}/{{filename}}",
    "navigation_with_keys": True,
}

html_css_files = ["custom.css"]

viewcode_line_numbers = True

# -- MyST-NB -----------------------------------------------------------------
nb_execution_in_temp = False
nb_execution_mode = "cache"
nb_execution_timeout = 600
nb_render_markdown_format = "myst"
myst_enable_extensions = [
    "fieldlist",
    "colon_fence",
]


def _looks_like_secret_name(name: str) -> bool:
    upper_name = name.upper()
    return upper_name.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")) or (
        upper_name.startswith(("OPENAI_", "ANTHROPIC_", "HF_"))
    )


def _candidate_secret_values() -> list[str]:
    values: set[str] = set()

    for name, value in os.environ.items():
        if value and _looks_like_secret_name(name):
            values.add(value.strip())

    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for name, value in dotenv_values(env_path).items():
            if name and value and _looks_like_secret_name(name):
                values.add(value.strip())

    return sorted(
        value
        for value in values
        if len(value) >= 8
        and "..." not in value
        and "<" not in value
        and "your-api-key" not in value.lower()
    )


def _check_generated_docs_for_secrets(app, exception) -> None:
    if exception is not None or app.builder.format != "html":
        return

    secret_values = _candidate_secret_values()
    if not secret_values:
        return

    leaked_paths: list[str] = []
    scan_suffixes = {".css", ".html", ".js", ".json", ".py", ".svg", ".txt"}
    for path in Path(app.outdir).rglob("*"):
        if not path.is_file() or path.suffix not in scan_suffixes:
            continue

        contents = path.read_text(errors="ignore")
        if any(secret_value in contents for secret_value in secret_values):
            leaked_paths.append(str(path.relative_to(app.outdir)))

    if leaked_paths:
        joined_paths = ", ".join(sorted(leaked_paths))
        raise SphinxError(
            "Generated docs appear to contain secret material from the environment "
            f"or .env file: {joined_paths}"
        )


def _write_generated_metric_docs() -> None:
    from nomad.metrics import metrics_markdown_table

    generated_dir = DOCS_ROOT / "reference" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    (generated_dir / "metrics.md").write_text(
        "\n".join(
            [
                "<!-- Auto-generated by docs/conf.py from src/nomad/metrics.py. -->",
                "",
                "<!-- nomad-server-metrics-start -->",
                metrics_markdown_table("serve"),
                "<!-- nomad-server-metrics-end -->",
                "",
                "<!-- nomad-gateway-metrics-start -->",
                metrics_markdown_table("gateway"),
                "<!-- nomad-gateway-metrics-end -->",
                "",
            ]
        ),
        encoding="utf-8",
    )


_write_generated_metric_docs()


def setup(app):
    # Treat `jsonc` fences as JSON5 so comments are highlighted without warnings.
    lexers["jsonc"] = get_lexer_by_name("json5")
    app.connect("build-finished", _check_generated_docs_for_secrets)
