from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nomad.copycow import copy_cow

if TYPE_CHECKING:
    from .tool_index import ToolDescriptor

ROOT_TEMPLATE = '''"""Auto-generated MCP tool wrappers."""

from nomad.gateway.runtime import install_wrapper_importer as _install_importer

_install_importer()

__all__ = []
'''


class WrapperGenerator:
    """Provide metadata for dynamically generated MCP tool wrappers."""

    def __init__(
        self,
        root: Path,
        package_name: str,
    ):
        self.root = root
        self.package_name = package_name
        self.package_root = self.root / package_name
        self.package_root.mkdir(parents=True, exist_ok=True)
        self._ensure_nomad_package()
        self._ensure_root_package()

    def _ensure_root_package(self) -> None:
        init_path = self.package_root / "__init__.py"
        if not init_path.exists():
            init_path.write_text(ROOT_TEMPLATE, encoding="utf-8")

    def _ensure_nomad_package(self) -> None:
        pkg_name = __package__.split(".", 1)[0]
        spec = importlib.util.find_spec(pkg_name)
        if spec is None:
            raise ModuleNotFoundError("Could not resolve installed 'nomad' package")

        locations = spec.submodule_search_locations
        if locations:
            source_root = Path(next(iter(locations)))
        elif spec.origin is not None:
            source_root = Path(spec.origin).resolve().parent
        else:
            raise ModuleNotFoundError("Could not determine source path for 'nomad'")

        copy_cow(source_root, self.root / pkg_name)

    def build_module_spec(
        self,
        server: str,
        tools: Sequence[ToolDescriptor],
    ) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        identifiers: list[str] = []
        for tool in tools:
            if tool.server != server:
                raise ValueError(
                    f"Tool descriptor for server '{tool.server}' does not match '{server}'"
                )
            identifiers.append(tool.identifier)
            entries.append(tool.to_wrapper_entry())

        return {
            "package": self.package_name,
            "server": server,
            "module": server,
            "exports": list(dict.fromkeys(identifiers)),
            "tools": entries,
        }
