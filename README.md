# Email Manager — AI Briefing + Inbox Cleanup

> **📌 Work sample.** This is a real, working personal project, shared publicly as a code/work sample for recruiters and hiring managers. It is a sanitized copy of a tool the author uses daily to run several small businesses out of five inboxes — all secrets, credentials, and personal data have been removed (see `config.template.yaml` for the configurable surface). Built solo with Claude Code.

A self-hosted, AI-powered email tool that does two jobs across all of your inboxes:

1. **Daily briefing** — reads every account, extracts what actually matters (deadlines, money owed, action items, calendar-linked commitments), and emails you one clean digest each morning.
2. **Inbox cleanup** — classifies, labels, archives, and safely deletes mail against categories you define, with guardrails so nothing important is ever lost.

Built to run a portfolio of small businesses and personal accounts out of five inboxes without drowning in email. Works with **Gmail, Outlook/Hotmail, Yahoo, and any IMAP provider** — you configure only the ones you use.

> Built solo with **Claude Code**. Two Claude models do the reasoning: **Opus** for the deeper briefing analysis, **Sonnet** for fast, high-volume inbox classification.

---

## Highlights

- **Two AI pipelines, two model tiers.** Opus extracts structured items (agreements, deadlines, receivables/payables, action items) from email batches; Sonnet handles fast inbox classification. Model choice is matched to the cost/latency profile of each job.
- **Provider-agnostic architecture.** A single `EmailClient` abstract base; Gmail (Google API + OAuth), Outlook (Microsoft Graph + MSAL), and Yahoo/IMAP each implement it. A factory builds the right client per account at runtime. Add a provider by implementing one interface.
- **Config-driven, nothing hardcoded.** Business entities, label taxonomy, owner profile, briefing priorities, and schedules all live in `config.yaml` and are injected into the prompts at runtime. The same codebase personalizes to any user via the setup wizard.
- **Safety guards on destructive actions.** "Vital" categories are never auto-deleted, low-confidence classifications are archived rather than trashed, and emails with attachments are never auto-deleted.
- **Cross-platform scheduling.** Unattended daily runs via Windows Task Scheduler or macOS launchd, with logging and idempotent processing (a SQLite ledger prevents re-processing the same mail).
- **One-command setup.** An interactive wizard handles profile, accounts, OAuth, labels, scheduling, and provider credentials; `run.bat` / `install.sh` bootstrap the environment on first launch.
- **Local web dashboard.** A small Flask app (`localhost:5050`) to check off tracked items, linked from the top of every briefing.

---

## What this work sample demonstrates

For reviewers, this project is here to show:

- **Practical AI integration** — designing structured-extraction and classification prompts, choosing the right model tier per task (Opus vs. Sonnet), and turning model output into reliable, acted-upon data.
- **System design** — a clean provider abstraction, config-driven behavior, idempotent processing, and safety guards around destructive actions.
- **Shipping end to end, solo** — from architecture (`CLAUDE.md`) through a working CLI, OAuth flows for three email platforms, a local dashboard, cross-platform scheduling, setup tooling, and user documentation.
- **AI-native development** — the whole thing was built with Claude Code against a written spec, the same way the author runs day-to-day operations.

It is not a library to adopt or a product to install — it is a finished, real-world tool shared as evidence of how the author thinks and builds.

---

## How it works

```
                ┌─────────────────────────── run ───────────────────────────┐
  Accounts ──▶ Fetch ──▶ Analyze (Opus) ──▶ Label ──▶ Digest email ──▶ (20m) ──▶ Cleanup (Sonnet)
 (Gmail/        +cal      structured        Gmail      HTML brief +              classify → label /
  Outlook/      events    extraction        labels     dashboard link            archive / trash
  Yahoo/IMAP)                                                                    (guarded)
```

- **Briefing pipeline:** fetch new mail + calendar → Opus extracts structured items → apply entity labels → generate and send an HTML digest (Headlines, Urgent, a 14-day horizon that ties pending items to upcoming calendar events, financial status, tasks).
- **Cleanup pipeline:** fetch inbox → skip already-processed (SQLite ledger) → Sonnet classifies into your categories with an action + confidence → label / archive / trash under the safety guards → record the result.

### Tech

Python 3.11 · Anthropic SDK (Opus + Sonnet) · Google API & Microsoft Graph (MSAL) & IMAP · Click CLI + Rich · SQLite (two stores: briefing + cleanup) · Flask (local dashboard) · Windows Task Scheduler / macOS launchd.

See [`CLAUDE.md`](CLAUDE.md) for the full architecture and design notes (this is the spec the project was built against with Claude Code).

---

## Setup & Usage

*The setup, commands, and troubleshooting below are included to show the project is complete, documented, and genuinely runnable — not as a request for reviewers to install anything.*

### What You Need Before Starting

1. **Python 3.11 or newer**
   - **Windows:** [python.org/downloads](https://www.python.org/downloads/) — check "Add Python to PATH" during install
   - **Mac:** the installer handles it
2. **An Anthropic API key** — sign up at [console.anthropic.com](https://console.anthropic.com), create a key (starts with `sk-ant-`). Roughly $5–10/month depending on email volume.

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
| `python main.py dashboard` | Open the web dashboard to mark items done |
| `python main.py status` | See everything currently tracked |
| `python main.py cleanup inbox` | Organize the inbox without the full pipeline |
| `python main.py horizon` | Next 14 days: calendar + pending items together |
| `python main.py tasks add "Do the thing"` | Add a floating task |

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
- **Gmail stops working after ~a week:** the Google OAuth app is still in "Testing" mode (refresh tokens expire after 7 days). Publish the app at [console.cloud.google.com](https://console.cloud.google.com/apis/credentials/consent) → **PUBLISH APP**, then re-authorize. No Google review is needed for personal use.
- **Scheduled run didn't fire:** check `email-manager/logs/daily_run.log` (Windows) or `launchctl list | grep emailmanager` (Mac).
- **Stuck?** Run `get-help.bat` / `bash get-help.sh` — it runs diagnostics, copies them to your clipboard, and opens Claude to help debug.

## Project Layout

`email-manager/` holds the app: `main.py` (CLI), `setup_wizard.py`, `src/` (providers, briefing, cleanup), `docs/`. User-specific files (`config.yaml`, `.env`, `credentials/`, `data/`) are created locally and are git-ignored — they are never committed.

---

*Shared as a work sample — a real project, sanitized for public release. Built with Claude Code. MIT licensed — see [LICENSE](LICENSE).*
