# Changelog

## 1.0.0 (2026-09-22)

The public work-sample branch, repackaged. Same architecture, prompts, and behavior as the private daily-running tool as of 2026-09-16, plus:

### Packaging and tooling
- `pyproject.toml` with a console script (`email-manager`), `src/` layout, pinned `anthropic>=1,<2`.
- Ruff (lint + format), mypy, pytest with coverage, pre-commit config, GitHub Actions on Ubuntu and Windows for Python 3.11 to 3.13.
- All paths resolve from the app directory (`EMAIL_MANAGER_HOME` overrides), not the working directory.

### Claude integration
- One `ClaudeClient` for every call: structured outputs validated by pydantic, per-call effort, prompt caching on the system block, SDK retries and timeouts, per-call usage and cost accounting.
- Classification schema built at runtime with the configured categories as an enum.
- A cut-off or invalid response raises and the batch is retried next run; previously it returned `{}` and the batch was marked processed with nothing extracted.
- Prompts moved to `prompts/*.md`; four copies of a JSON-fence parser deleted.
- `email-manager usage` summarises spend per day and stage from the log.

### Correctness and safety
- The documented attachment guard is now implemented: mail with attachments is archived, never trashed.
- Outlook calendar events are no longer deleted on every start-up (migration normalises datetimes in place).
- Hardcoded owner name removed from queries; owner comes from config everywhere.
- `--strict` cleanup flag removed (it was a no-op). `delete_old_emails.py`, which trashed mail on import, removed.
- Fixed a `str.lstrip` misuse that would have mis-detected subjects starting with the letters in "Re: ".
- Provider date filters are a neutral object translated per provider; IMAP and OData query literals are escaped.
- SQL identifiers used in f-strings pass through an allowlist assertion on the same line.
- UTF-8 on every file read and write; `datetime.utcnow()` retired.

### Structure
- `main.py` (1,600 lines) split into `cli/` (one module per command group), `pipeline.py` (stages returning results), `notify.py`, `horizon.py`.
- `database.py` (1,200 lines) split into `db/` by domain, with versioned migrations that raise on failure.
- Typed config (`config.py`); the wizard's YAML/.env writer extracted into `config_writer.py` using `yaml.safe_dump`.
- Provider message shape unified (`EmailMessage`); providers fully type-annotated; Google calendar client now implements the calendar base class.
- Console output no longer writes ANSI codes into the log file; the log gets stack traces; `run` exits non-zero when a stage failed.

### Removed from the public copy
- Business-specific song-order pipeline (one client's workflow; also the only per-email Claude call).
- In-process `schedule` daemon (the OS scheduler is the supported path).
- SMS-forward detection (computed and stored but never read).

### Added
- `email-manager demo`: seeds a throwaway database with sample data and renders a board.
- Test suite (`tests/`) with fakes for the Anthropic SDK and for a mailbox.
- `ARCHITECTURE.md`, this changelog, `CONTRIBUTING.md`, `SECURITY.md`.

## Earlier history (private tool)

- **2026-09-16**: Digest became a read-only bulletin board (Today / Next Two Weeks) with date-based aging; check-off dashboard, `mark`, and `snooze` retired. Opus/Sonnet split replaced by `claude-sonnet-5` with per-call effort. Per-call token logging. Once-per-day guard in `daily_run.bat` after duplicate briefings.
- **2026-06-22 to 06-24**: Per-account OAuth isolation with email, marker-file, and toast alerts; fail-fast on token-less accounts in scheduled runs; cleanup no longer archives the bot's own digest.
- **2026-05**: OAuth publish-before-token documentation after a 7-day token expiry took the run down.
- **2026-04**: Headlines section and tighter extraction; reply-to-digest replaced by a web dashboard; label taxonomy made config-driven; multi-provider support (Outlook, Yahoo, IMAP); portable folder with launchers and a setup wizard; briefing bot and cleanup bot merged into one tool.
