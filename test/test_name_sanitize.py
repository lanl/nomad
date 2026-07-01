import keyword
import string
from itertools import product
from random import randbytes

import pytest

from nomad.common.name_sanitize import sanitize_mcp_name, sanitize_python_name

EXAMPLE_INPUTS = [
    "my function! 2",
    "class",
    "def",
    "module--foo.bar",
    "2cool",
    "!!!",
    "Python©",
    "mist-models/mist-28M-kw4ks27p-tox21",
    "awesome-model/thebest@v3.2",
]


@pytest.mark.parametrize("raw,module", list(product(EXAMPLE_INPUTS, [True, False])))
def test_sanitize_python_name(raw, module):
    out = sanitize_python_name(raw, module=module)
    assert out.isidentifier()
    assert not keyword.iskeyword(out)
    assert not keyword.issoftkeyword(out)


def test_sanitize_python_name_fuzz():
    for _ in range(128):
        raw = (randbytes(64).decode(encoding="ascii", errors="replace"),)
        for module in [True, False]:
            out = sanitize_python_name(raw, module=module)
            assert out.isidentifier()
            assert not keyword.iskeyword(out)
            assert not keyword.issoftkeyword(out)


@pytest.mark.parametrize("raw", EXAMPLE_INPUTS)
def test_sanitize_mcp_name(raw):
    out = sanitize_mcp_name(raw)
    allowed = set(string.ascii_letters + string.digits + "_-.")
    assert set(out).issubset(allowed), f"Illegal chars in '{out}'"
