# Email Manager: AI Briefing + Inbox Cleanup

> **📌 Work sample.** This is a real, working personal project, shared publicly as a code sample for recruiters and hiring managers. It is a sanitized copy of a tool the author runs every morning across five inboxes for several small businesses. Secrets, credentials, and personal data have been removed; `config.template.yaml` shows the configurable surface. Built solo with Claude Code.

A self-hosted email tool that does two jobs across all of your inboxes:

1. **Daily briefing.** Reads every account, extracts what matters (deadlines, money owed, action items, calendar commitments), and emails one read-only bulletin board each morning.
2. **Inbox cleanup.** Classifies, labels, archives, and deletes mail against categories you define, with guards so nothing important is lost.

Works with **Gmail, Outlook/Hotmail, Yahoo, and any IMAP provider**. You configure only the accounts you use.

Every Claude call runs on one model, `claude-sonnet-5`, with the effort level set per job.

---

## Highlights

- **The briefing is a read-only bulletin board.** Two sections: "Today" (at most seven items) and "Next Two Weeks" (day by day). There is nothing to check off. Dated items age off once their date passes, and undated items drop about a week after they were first seen. The window rules live in one place in `src/briefing/database.py`.
- **One model, effort dialed per call.** Extraction, duplicate reconciliation, and inbox classification run at low effort. Writing the board runs at medium. Model and effort for each job are set in `config.yaml`.
- **Token cost engineered down.** The board writer receives only the items that will appear on today's board, with trimmed fields and compact JSON. A digest costs roughly 4 to 8K input tokens. The previous version sent every pending row, twice.
- **Every Claude call logs its usage.** A one-line `claude-usage` logger records the tag, model, input tokens, output tokens, and stop reason for each call, so `logs/daily_run.log` shows where the spend went.
- **Dry run.** `python main.py digest --dry-run` writes the board to `logs/last_digest.html` and sends nothing.
- **Provider-agnostic architecture.** A single `EmailClient` abstract base. Gmail (Google API + OAuth), Outlook (Microsoft Graph + MSAL), and Yahoo/IMAP each implement it, and a factory builds the right client per account at runtime. Add a provider by implementing one interface.
- **Config-driven, nothing hardcoded.** Business entities, label taxonomy, owner profile, briefing priorities, and schedules live in `config.yaml` and are injected into the prompts at runtime. The same codebase personalizes to any user through the setup wizard.
- **Safety guards on destructive actions.** "Vital" categories are never auto-deleted, low-confidence classifications are archived instead of trashed, emails with attachments are never auto-deleted, and cleanup skips the tool's own briefing.
- **Resilient unattended runs.** Each account authenticates in isolation, so one dead OAuth token does not stop the others. A failure is emailed to the owner, written to a marker file, and shown as a desktop toast. Scheduled runs fail fast on a missing token instead of waiting on a browser prompt nobody will answer.
- **Once-per-day guard.** `daily_run.bat` records the date of the last successful run and exits if it already ran today, so duplicate scheduler triggers cannot send duplicate briefings. `--force` overrides it.
- **Cross-platform scheduling and one-command setup.** Windows Task Scheduler or macOS launchd. An interactive wizard handles profile, accounts, OAuth, labels, and scheduling; `run.bat` and `install.sh` bootstrap the environment on first launch.

---

## What this work sample demonstrates

For reviewers, this project is here to show:

- **Practical AI integration.** Structured-extraction and classification prompts, effort set per call on a single model, and model output turned into ledger rows that the rest of the tool acts on.
- **Cost engineering.** The digest's input shrank from every pending row to only the rows on today's board, and every call logs its token usage so the cost of each stage is visible in the daily log.
- **Taking a feature out on purpose.** The first version had a local Flask dashboard for checking items off. Keeping it running cost more than the check-off saved, so it was retired and the digest was redesigned so items expire by rule instead of by hand.
- **System design.** One provider interface, config-driven behavior, idempotent processing, and guards around destructive actions.
- **Shipping end to end, solo.** Architecture notes in `CLAUDE.md`, a working CLI, OAuth flows for three email platforms, cross-platform scheduling, setup tooling, and user documentation.
- **AI-native development.** The whole thing was built with Claude Code against a written spec, the same way the author runs day-to-day operations.

It is a finished tool in daily use, shared as evidence of how the author thinks and builds.

---

## How it works

```
                 ┌──────────────────────────────── run ────────────────────────────────┐
  Accounts ──▶ Fetch ──▶ Analyze ──▶ Label ──▶ Follow-ups ──▶ Digest ──▶ (20 min) ──▶ Cleanup
 (Gmail/        +cal      extract     Gmail     sent-folder    2-section   wait          classify →
  Outlook/      events    items to    labels    scan           board                     label / archive /
  Yahoo/IMAP)             SQLite                               emailed                   trash (guarded)
```

**Briefing pipeline.** Fetch new mail plus calendar events. Claude extracts agreements, deadlines, financial items, and action items from each batch into a SQLite ledger. Entity labels are applied in Gmail. A sent-folder scan finds threads still waiting on a reply. Then the board is built: window rules pick what belongs on today's board, one Claude call merges duplicates (an invoice, its reminder, and its past-due notice become one line), and one Claude call writes the two-section HTML, which is emailed.

**Cleanup pipeline.** Fetch the inbox, skip anything already processed (SQLite ledger), have Claude classify each message into your categories with an action and a confidence, then label, archive, or trash under the safety guards and record the result.

### Tech

Python 3.11 · Anthropic SDK (Claude Sonnet 5, effort per call) · Google API, Microsoft Graph (MSAL), IMAP · Click CLI + Rich · SQLite (two stores: briefing + cleanup) · Windows Task Scheduler / macOS launchd.

See [`CLAUDE.md`](CLAUDE.md) for the architecture and design notes. That file is the spec the project was built against with Claude Code.

---

## Setup & Usage

*The setup, commands, and troubleshooting below are included to show the project is complete, documented, and runnable. Reviewers are not expected to install anything.*

### What You Need Before Starting

1. **Python 3.11 or newer**
   - **Windows:** [python.org/downloads](https://www.python.org/downloads/), and check "Add Python to PATH" during install
   - **Mac:** the installer handles it
2. **An Anthropic API key.** Sign up at [console.anthropic.com](https://console.anthropic.com) and create a key (starts with `sk-ant-`). On Sonnet 5 at low effort this typically costs a few dollars a month, depending on email volume.

## Setup (5 minutes)

**Windows:** open the `email-manager` folder, double-click **`run.bat`**, and follow the wizard (profile, API key, accounts, entities, label categories, schedule, OAuth sign-in). Your first briefing runs automatically.

**Mac:** open Terminal, drag the `email-manager` folder in, run `bash install.sh`, and follow the same wizard.

## After Setup

Runs automatically each day at your chosen time. Run manually anytime with `run.bat` (Windows) or `bash run.sh` (Mac/Linux).

### Common Commands

Run from the `email-manager` folder:

| Command | What it does |
|---------|-------------|
| `python main.py run` | Full pipeline now (fetch + analyze + briefing + cleanup) |
| `python main.py digest --dry-run` | Build today's board into `logs/last_digest.html` without emailing it |
| `python main.py status` | See everything currently tracked |
| `python main.py cleanup inbox` | Organize the inbox without the full pipeline |
| `python main.py cleanup stats` | How many emails have been organized, by category |
| `python main.py horizon` | Next 14 days: calendar + pending items together |
| `python main.py tasks add "Do the thing"` | Add a task; it shows on the board until its date passes |

## Supported Email Providers

| Provider | Email | Calendar | Labels |
|----------|-------|----------|--------|
| Gmail | Yes | Yes | Yes (color-coded) |
| Outlook / Hotmail / Live | Yes | Yes | No |
| Yahoo | Yes | No | No |
| Other (IMAP) | Yes | No | No |

Connect as many accounts as you want, in any combination.

## Troubleshooting (highlights)

- **"Python is not recognized" (Windows):** reinstall from python.org with "Add Python to PATH" checked.
- **Gmail stops working after about a week:** the Google OAuth app is still in "Testing" mode, so refresh tokens expire after 7 days. Publish the app at [console.cloud.google.com](https://console.cloud.google.com/apis/credentials/consent) (**PUBLISH APP**), then run `python main.py setup-accounts` to re-authorize. No Google review is needed for personal use.
- **An account needs re-authorization:** the daily run emails you, writes `logs/AUTH_FAILURE.txt`, and keeps going for the accounts that still work. Run `python main.py setup-accounts`.
- **Scheduled run didn't fire:** check `email-manager/logs/daily_run.log` (Windows) or `launchctl list | grep emailmanager` (Mac). If the log says "Skipped: already ran successfully today", the once-per-day guard did its job; use `daily_run.bat --force` to run again.
- **Stuck?** Run `get-help.bat` / `bash get-help.sh`. It runs diagnostics, copies them to your clipboard, and opens Claude to help debug.

## Project Layout

`email-manager/` holds the app: `main.py` (CLI), `setup_wizard.py`, `src/` (providers, briefing, cleanup, `llm_usage.py`), `docs/`. User-specific files (`config.yaml`, `.env`, `credentials/`, `data/`, `logs/`) are created locally and git-ignored. They are never committed.

---

*Shared as a work sample: a real project, sanitized for public release. Built with Claude Code. MIT licensed, see [LICENSE](LICENSE).*
