"""Prompt templates, stored as text files next to this module.

Keeping prompts out of the Python source makes them diffable and reviewable as
prose. Each file is a ``str.format`` template; the placeholders it expects are
listed at the top of the file. Load one with :func:`load`.
"""

from __future__ import annotations

from functools import cache
from importlib import resources


@cache
def load(name: str) -> str:
    """Return the template text for ``prompts/<name>.md`` with the header comment stripped."""
    text = resources.files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    # Drop a leading HTML comment block used to document placeholders.
    if lines and lines[0].startswith("<!--"):
        while lines and not lines[0].endswith("-->"):
            lines.pop(0)
        if lines:
            lines.pop(0)
    return "\n".join(lines).strip() + "\n"
