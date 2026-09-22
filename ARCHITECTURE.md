# Architecture

This document is for people reading the code. The README explains what the tool does and why the main decisions were made; this one explains where things are and how they fit.

## Module map

```
cli/            Click commands. Parse arguments, call pipeline functions, print results. No logic.
pipeline.py     One function per stage. Takes a Config and clients, returns a result object. Logs, never prints.
llm.py          ClaudeClient. The only module that imports the Anthropic SDK.
schemas.py      Pydantic models for every Claude response. Passed to the API as the output format.
prompts/        Prompt templates as .md files with str.format placeholders.
config.py       Typed config.yaml (pydantic). load_config() raises ConfigError with a readable message.
config_writer.py  Pure functions the setup wizard calls to write config.yaml, .env, and scheduler definitions.
email_client.py Provider contract: EmailClient ABC, EmailMessage TypedDict, EmailFilter, ProviderError/AuthError.
providers/      gmail.py (Gmail + Google Calendar), outlook.py (Graph + calendar), imap_client.py.
client_factory.py  Builds clients from config; picks the sending account.
briefing/       analyzer.py (extraction + board writer), reconcile.py (duplicate merge), digest.py (HTML + send).
cleanup/organizer.py  Label taxonomy, classification prompt, the guards (decide()), and the actions.
db/             core.py (connection, migrations), emails.py, items.py, calendar.py, tasks.py, follow_ups.py,
                bulletin.py (board window rules, reconcile support, horizon data), cleanup_ledger.py.
notify.py       Auth-failure alerting: email, marker file, desktop toast.
horizon.py      Correlates pending items with calendar events for the CLI horizon view.
demo.py         Seeds a throwaway database with sample data and renders a board.
paths.py        Where config, data, logs, and credentials live (EMAIL_MANAGER_HOME overrides).
logging_setup.py  Plain-text console handler plus a rotating file under logs/.
```

Dependency direction: `cli` depends on `pipeline`, which depends on `briefing`, `cleanup`, `db`, `client_factory`, and `llm`. `db` depends on nothing above it except `schemas` and `paths`. Providers depend only on `email_client`. Nothing imports `cli`.

## A full run

```
run_all(config, analysis_claude, cleanup_claude)
  build_clients(config)                  one EmailClient per configured account
  authenticate_all(clients)              each in isolation; failures collected
  notify_auth_failure(...)               only if something failed
  fetch_emails(config, healthy)          INSERT OR IGNORE into emails, keyword entity guess
  fetch_calendar(config, healthy)        upsert into calendar_events (datetimes normalised on store)
  analyze(config, analysis_claude)       batches of unprocessed emails -> ExtractionBatch -> store_extractions
  apply_labels(config, healthy)          Tracker/<Entity> labels on providers that support labels
  detect_follow_ups(config, healthy)     sent-folder scan -> follow_ups upsert
  build_digest(config, analysis_claude)  board -> reconcile -> write -> send (or write to logs/ on dry run)
  sleep(delay_after_digest_minutes)
  cleanup_inbox(config, cleanup_claude)  classify -> decide -> act -> cleanup_ledger
```

Each stage returns a small dataclass; `RunReport` collects them and a list of errors. The CLI exits non-zero when the report has errors, so the scheduler wrapper only records a success marker for a run that produced a briefing.

## Data

Two SQLite files under `data/`, both opened lazily against a configurable path (tests point them at `tmp_path`):

- `tracker.db`: `emails`, `agreements`, `deadlines`, `financial_items`, `action_items`, `calendar_events`, `tasks`, `follow_ups`, `sync_state`. Item rows carry `status` (`pending`, `resolved`, `merged`) and `merged_into` so reconciliation is reversible.
- `organized.db`: `organized`, a seen-set of cleanup decisions keyed by email id. Kept separate so it can be cleared to force reclassification without touching briefing data.

Migrations are numbered functions recorded in `PRAGMA user_version`, each in its own transaction. Migration 1 creates the schema and backfills columns older databases lack; migration 2 rewrites timezone-suffixed calendar datetimes in place (an earlier version deleted them on every start-up, which silently wiped every Outlook event).

The connection runs in autocommit mode. Multi-statement writes that must be atomic (`store_extractions`, `apply_item_merge`, migrations) issue their own `BEGIN`/`COMMIT`.

Table and column names are never built from user or model input except in `bulletin.py`, where the reconciler's target table passes through `assert_identifier()` with an allowlist on the line before the f-string.

## The board

`get_bulletin_items()` is the one query that decides what the owner sees. Its rules, in days:

| Kind | Shown when |
|------|------------|
| Dated deadline, action, money, task | due date between 3 days ago and 14 days ahead |
| Undated action or money item | first seen within the last 7 days |
| Undated manual task | added within the last 14 days |
| Unanswered thread | re-detected within the last 2 days, waiting 3 to 21 days, recipient is not one of the owner's own addresses |
| Calendar event | starts within 14 days |

Rows are trimmed to the fields the writer needs before they go into the prompt.

## Talking to Claude

`ClaudeClient` has two methods. `extract()` takes a pydantic schema and returns a validated instance via the Messages API structured-output format. `text()` returns a text completion (used only for the board HTML). Both take an `effort` value, mark the system prompt as a cache breakpoint, record a `Usage` row with token counts and estimated cost, and raise `LLMOutputError` when the response was cut off or unparseable.

The SDK client is injected, which is how the whole layer is tested without network access (`tests/conftest.py::FakeAnthropic`). SDK retries (rate limits, 5xx) and a per-request timeout are configured on the client.

Prompt text lives in `prompts/*.md` so it can be diffed as prose. The output shape is not described in the prompts; the schema carries it, including field descriptions.

## Providers

`EmailClient` declares eight abstract methods (authenticate, fetch, sent, thread, two paged fetches, send, trash) and no-op defaults for label operations. `supports_labels` and `supports_calendar` let the pipeline skip what a provider cannot do instead of guessing.

`authenticate(interactive=False)` is the contract that makes unattended runs safe: a dead token raises `AuthError` instead of opening a browser nobody will see. `setup-accounts` calls it with `interactive=True`.

`EmailFilter(after, before)` is translated per provider: Gmail search operators, an OData `$filter`, or IMAP `SINCE`/`BEFORE`. Query literals are escaped (`_odata_literal`, `_imap_quoted`) because thread ids come from inbound mail.

## Errors and logging

Provider and SDK exceptions are caught at stage boundaries in `pipeline.py`, logged with a stack trace (`exc_info=True`), and turned into result fields. Operator-facing failures (`ConfigError`, `PipelineError`) become a one-line message and exit code 1 in the CLI. Nothing catches an exception and passes silently.

Logging goes to stderr as plain text and to `logs/email_manager.log` (rotating). The `claude-usage` logger writes one line per model call; `email-manager usage` parses that file.

## Testing

`tests/` runs with no network and no credentials. Fixtures:

- `home`: sets `EMAIL_MANAGER_HOME` to `tmp_path` and points both databases at it.
- `fake_sdk` / `claude`: a stand-in for `anthropic.Anthropic` whose `messages.parse` returns whatever schema instance the test's handler builds, and whose `messages.create` returns text. Every call is recorded so tests can assert on the effort, the schema, and the cached system block.
- `mailbox`: an in-memory `EmailClient` that records trashes, labels, and sent mail.

Coverage is highest where bugs would cost the most: the board window rules, the cleanup guards, the migration path from a legacy database, the LLM wrapper's error paths, and the config writer's escaping. The tkinter wizard pages and the live provider calls are the two areas without tests; both are exercised by hand.

## Decisions log

- 2026-04: Two projects (briefing bot, cleanup bot) merged into one CLI with a shared client factory.
- 2026-04: Reply-to-digest check-off replaced by a Flask dashboard; label taxonomy made config-driven.
- 2026-06: Per-account auth isolation and alerting after a 7-day OAuth token expiry silently stopped the daily run.
- 2026-09-16: Dashboard removed; digest became a read-only bulletin board with date-based aging. Opus/Sonnet split replaced by one model with per-call effort. Per-call token logging added. Once-per-day guard added after duplicate briefings.
- 2026-09-22 (this repository): packaging, typed config, structured outputs, provider filter object, versioned migrations, test suite, CI. Business-specific order pipeline and the in-process scheduler removed from the public copy.
