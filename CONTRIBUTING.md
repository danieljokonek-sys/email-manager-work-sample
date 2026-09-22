# Contributing

This is a personal tool shared as a work sample, so there is no roadmap and no promise of review turnaround. Issues and pull requests are still welcome, especially for a new provider or a bug in the guards.

## Setup

```bash
pip install -e ".[dev]"
pre-commit install
```

## Before you push

```bash
ruff check . && ruff format .
mypy
pytest
```

CI runs the same steps on Ubuntu and Windows.

## Ground rules

- Anything that can trash mail goes through `cleanup/organizer.py::decide()` and gets a test in `tests/test_organizer.py`.
- Every Claude call goes through `llm.ClaudeClient`. Do not import the SDK anywhere else.
- Prompts live in `prompts/*.md`. Output shape lives in `schemas.py`, not in prompt text.
- New providers implement `EmailClient` in `providers/` and translate `EmailFilter` themselves.
- Schema changes are a new numbered migration in `db/core.py`, never an edit to an existing one.
- No secrets, tokens, real addresses, or real email content in the repository, including in tests.
