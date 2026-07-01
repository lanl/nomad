import keyword
import logging
import re
import string
import sys

LOGGER = logging.getLogger(__name__)


def sanitize_mcp_name(raw: str):
    """Convert a raw name into Nomad's MCP-compatible tool-name form."""
    allowed = set(string.ascii_letters + string.digits)
    mapped = {".": "p", "/": "---", "\\": "---"}
    name = "".join(ch if ch in allowed else mapped.get(ch, "_") for ch in str(raw))
    if len(name) >= 128:
        LOGGER.warning("Tool Name %s (%s) too long", name, raw)
    return name


def sanitize_export_name(raw: str) -> str:
    """Convert a raw model source name into a safe export directory name."""
    safe = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-" for char in str(raw)
    ).strip("-")
    return safe or "model"


def sanitize_python_name(raw, *, module=False, allow_unicode=True):
    """
    Convert an arbitrary string into a *safe* Python identifier.

    Parameters
    ----------
    raw : str
        The input string to be sanitized.
    module : bool, optional
        If True, the returned name will be PEP‑8 compliant for modules
        (lower‑case, words separated by single underscores).  Default is False.
    allow_unicode : bool, optional
        If False, any non‑ASCII character is replaced with an underscore.
        Default is True.

    Returns
    -------
    str
        A valid Python identifier that can be used as a function name,
        module name, variable name, etc.

    Examples
    --------
    >>> sanitize_python_name('my function! 2')
    'my_function_2'
    >>> sanitize_python_name('my function! 2', module=True)
    'my_function_2'
    >>> sanitize_python_name('class')
    'class_'
    >>> sanitize_python_name('def', module=True)
    'def_'
    >>> sanitize_python_name('2cool')
    '_2cool'
    >>> sanitize_python_name('!!!', module=True)
    'module'
    """
    # 1. Turn into string and normalize
    name = str(raw)

    # 2. Optionally strip non‑ASCII characters
    if not allow_unicode:
        name = re.sub(r"[^\x00-\x7F]", "_", name)

    # 3. Replace any character that is not part of a valid identifier
    #    (`\w` = [A-Za-z0-9_] plus Unicode word chars when `allow_unicode=True`)
    name = re.sub(r"[^\w]", "_", name)

    # 4. Collapse consecutive underscores
    name = re.sub(r"_+", "_", name)

    # 5. Strip leading/trailing underscores
    name = name.strip("_")

    # 6. If the name is now empty, give it a generic fallback
    if not name:
        name = "module" if module else "func"
        name += f"_{hash(raw)}".replace("-", "n")

    # 7. If the name starts with a digit, prepend an underscore
    if re.match(r"^\d", name):
        name = "_" + name

    # 8. If the name is a Python keyword or a builtin, add a trailing underscore
    if (
        keyword.iskeyword(name)
        or name in sys.builtin_module_names
        or name in dir(__builtins__)
    ):
        name += "_"

    # 9. If asked for a module name, lower‑case it (PEP‑8)
    if module:
        name = name.lower()

    # 10. Final sanity check: if we somehow broke the identifier rules,
    #     prepend an underscore to force validity.
    if not name.isidentifier():
        name = "_" + name

    assert name.isidentifier() and not keyword.iskeyword(name), (
        f"Failed for {raw}: {name}"
    )

    return name
