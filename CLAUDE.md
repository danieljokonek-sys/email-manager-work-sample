# Email Manager: agent instructions

This file is what Claude Code reads when working in this repository. Humans should start with README.md and ARCHITECTURE.md.

## What this is

A CLI that reads several mailboxes (Gmail, Outlook, IMAP), extracts deadlines, money, and action items with Claude into SQLite, emails a read-only daily bulletin board, and cleans the inbox under safety guards. Python 3.11+, `src/` layout, package `email_manager`.

## Commands

```bash
pip install -e ".[dev]"
ruff check . && ruff format .      # lint + format (config in pyproject.toml)
mypy                                # type-checks src/
pytest                              # no network, no credentials needed
email-manager --help
email-manager demo                  # one Claude call, sample data, writes logs/last_digest.html
```

## Where things are

- `src/email_manager/cli/`: click commands. Thin. Call `pipeline`, print with rich.
- `src/email_manager/pipeline.py`: stages as functions returning dataclasses. Log, never print.
- `src/email_manager/llm.py`: `ClaudeClient`. The only place the Anthropic SDK is imported.
- `src/email_manager/schemas.py`: pydantic models Claude must return. `prompts/*.md`: the prompt text.
- `src/email_manager/db/`: SQLite. `core.py` owns the connection and numbered migrations; `bulletin.py` owns the board rules.
- `src/email_manager/cleanup/organizer.py`: taxonomy, classification, `decide()` (the guards).
- `src/email_manager/providers/`: one file per provider implementing `email_client.EmailClient`.
- `tests/conftest.py`: `home` (temp app dir), `fake_sdk`/`claude` (fake Anthropic), `mailbox` (fake EmailClient).

## Rules

- Do not import `anthropic` outside `llm.py`. Add a schema in `schemas.py` and call `ClaudeClient.extract`.
- Do not reintroduce a check-off flow (dashboard, mark, snooze). If an item lingers on the board, fix the window rule in `db/bulletin.py`.
- Do not change what may be trashed without updating `decide()` and its tests. The five guards in README.md are the contract.
- Do not put output-shape instructions in prompts; the schema carries them.
- New schema changes are a new migration function appended to `MIGRATIONS` in `db/core.py`. Never edit an applied one. No `try/except: pass` around migrations.
- Keep the digest subject on the "Daily Briefing" / "Weekly Briefing" prefix; cleanup and follow-up detection rely on it (`briefing/digest.py::is_own_briefing`).
- Table and column names interpolated into SQL go through `db.core.assert_identifier` on the same line.
- `open()` always gets `encoding="utf-8"`. Timestamps use `db.utcnow_iso()`.
- Nothing personal in the repository: no real addresses, names, tokens, or email text, including in tests and sample data.
- Run `ruff check`, `ruff format`, `mypy`, and `pytest` before committing. CI runs them on Ubuntu and Windows.

## Model

`claude-sonnet-5` for every call; effort per call from config (`analysis.effort`, `digest.effort`, `cleanup.effort`). This is a cost decision by the owner. Do not change the model without asking.

## Gotchas

- Google OAuth apps must be published ("In production") before minting tokens, or refresh tokens die after 7 days. Symptom: `invalid_grant` after a clean week. Fix: `python main.py setup-accounts`.
- `daily_run.bat` runs at most once per calendar day (`logs/last_success.txt`); pass `--force` to override.
- The SQLite connection is in autocommit mode. Wrap multi-statement writes in explicit `BEGIN`/`COMMIT` (see `store_extractions`).
- `EMAIL_MANAGER_HOME` moves config, data, logs, and credentials; tests rely on it.
