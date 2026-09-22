# Email Cleanup & Brief Manager

## What This Project Is

A unified AI-powered email management tool that combines two functions:

1. **Briefing Bot** -- Monitors email accounts (Gmail, Outlook, Yahoo, IMAP), extracts deadlines, financial items, and action items with Claude, and emails a daily **read-only bulletin board**: a "Today" section (top things to be aware of) and a "Next Two Weeks" section (day by day). There is no check-off; items age off by date (see `get_bulletin_items()` in `src/briefing/database.py`). Personalized using the owner's profile and priorities from config.yaml.

2. **Email Cleanup** -- Classifies, labels, archives, and deletes emails with Claude. Keeps the inbox organized with user-defined label categories (configured in config.yaml `labels` section), color-coded Gmail labels, and deletion safety guards.

Business entities, label categories, owner profile, and briefing priorities are all user-configurable via the setup wizard and config.yaml. Nothing is hardcoded to a specific user.

## Current Status

- Briefing Bot: complete and in daily personal use
- Email Cleanup: in daily use; classification and labeling stable, deletion guards conservative by design
- Multi-provider support: Gmail, Outlook, Yahoo, generic IMAP
- Cross-platform: Windows (Task Scheduler) and macOS (launchd)
- The `run` command executes the full pipeline: fetch -> analyze -> digest -> cleanup
- The check-off web dashboard, `mark`, and `snooze` were retired on 2026-09-16 when the digest became a bulletin board

## Project Structure

```
email-manager-work-sample/
├── CLAUDE.md
├── README.md
├── LICENSE
├── .gitignore
└── email-manager/                 # Main application folder
    ├── main.py                    # Unified CLI entry point
    ├── config.yaml                # Accounts, entities, labels, profile, priorities, schedules (not committed)
    ├── config.template.yaml       # Clean template for new installs
    ├── .env                       # API keys (not committed)
    ├── .env.example               # Template showing required env vars
    ├── requirements.txt
    ├── run.bat / run.sh           # Launch scripts (auto-setup on first run)
    ├── daily_run.bat              # Windows Task Scheduler integration, once-per-day guard
    ├── install.sh                 # macOS installer
    ├── get-help.bat / get-help.sh # Diagnostics for support
    ├── setup_wizard.py            # Interactive setup (profile, labels, providers, scheduling)
    ├── delete_old_emails.py       # One-time historical email cleanup script
    ├── credentials/               # OAuth tokens (not committed)
    ├── data/
    │   ├── tracker.db             # Briefing database
    │   └── organized.db           # Cleanup database
    ├── logs/                      # daily_run.log, last_digest.html, last_success.txt (not committed)
    ├── docs/
    │   └── GOOGLE_SETUP_GUIDE.md  # Google Cloud OAuth setup
    └── src/
        ├── __init__.py
        ├── email_client.py        # Abstract base for all providers
        ├── client_factory.py      # Builds the right client per provider
        ├── gmail_client.py        # Backward-compat re-export
        ├── calendar_client.py     # Google Calendar client
        ├── llm_usage.py           # Logs token usage for every Claude call ("claude-usage" logger)
        ├── entities.py            # Business entity definitions
        ├── providers/
        │   ├── gmail.py           # Gmail provider (Google API + OAuth)
        │   ├── outlook.py         # Outlook provider (Microsoft Graph + MSAL)
        │   └── imap_client.py     # Yahoo, AOL, iCloud, generic IMAP
        ├── briefing/              # Briefing bot modules
        │   ├── analyzer.py        # Claude extraction + bulletin-board writer (uses owner profile + priorities)
        │   ├── database.py        # Briefing SQLite schema, queries, get_bulletin_items() window rules
        │   ├── digest.py          # Builds the board and emails it
        │   ├── reconcile.py       # Merges duplicate board items before the digest (1 Claude call)
        │   ├── scheduler.py       # Recurring task scheduling
        │   └── orders.py          # Song order tracking for a custom-song business
        └── cleanup/               # Cleanup bot modules
            ├── organizer.py       # Claude-powered email classification (labels from config)
            └── database.py        # Cleanup SQLite tracking
```

## How It Works

### Briefing Pipeline
1. **Fetch** -- Pulls new emails from all configured accounts + calendar events
2. **Analyze** -- Claude extracts deadlines, financials, action items, agreements
3. **Label** -- Applies entity labels in Gmail (skipped for non-Gmail providers)
4. **Digest** -- `get_bulletin_items()` selects what is on today's board (windowed by date), `reconcile.py` merges duplicates among those items, then Claude writes the two-section board and it is emailed. `python main.py digest --dry-run` writes it to `logs/last_digest.html` instead of sending.

### Bulletin-board window rules (no check-off, so everything must age off)
- Dated items (deadlines, action items, money, tasks): shown from 3 days after their date passes through 14 days ahead (`digest.days_ahead`).
- Undated action/money items: shown for 7 days after first seen (`source_date`, else `created_at`), then drop.
- Undated manual tasks: 14 days after being added.
- Waiting-for-reply threads: only while the sent-folder scan keeps re-detecting them (updated within 2 days) and 3 to 21 days waiting.
- Agreements are not on the board at all.
- Constants live at the top of the Bulletin board section in `src/briefing/database.py`.

### Cleanup Pipeline
1. **Fetch** -- Pulls inbox emails (with pagination)
2. **Check** -- Skips already-processed emails (tracked in organized.db)
3. **Classify** -- Claude categorizes into user-defined categories (from config.yaml `labels`) with action + confidence
4. **Act** -- Labels, archives, or trashes based on classification
5. **Record** -- Stores result so emails aren't reprocessed

### Combined `run` Command
Build clients once -> Authenticate each account in isolation -> Fetch -> Analyze -> Label -> Follow-ups -> Digest -> (20 min delay) -> Cleanup

## Key Technical Details

- **Python 3.10+** with Click CLI, Rich terminal output
- **Claude Sonnet 5** (`claude-sonnet-5`) for everything. Every call passes `output_config={"effort": ...}`: `analysis.effort` (extraction, order extraction, reconcile; default low), `digest.effort` (board writer; default medium), `cleanup.effort` (classification; default low). Every call logs `in=/out=` token counts via `src/llm_usage.py` (logger `claude-usage`), so `logs/daily_run.log` shows where the tokens go.
- **Token budget per digest**: only board items are sent (trimmed fields, compact JSON, no agreements, no duplicated horizon dict). The old digest shipped every pending row twice.
- **Multi-provider**: Gmail (full), Outlook (email + calendar), Yahoo/IMAP (email only)
- **Multiple accounts per provider** supported (e.g. 3 Gmail accounts)
- **2 SQLite databases**: tracker.db (briefing) and organized.db (cleanup)
- **Shared client instances** -- built once per `run`, reused across all pipeline stages
- **Per-account auth isolation** -- `run` authenticates each account separately; a dead token is logged, the owner is alerted (email via a working account, `logs/AUTH_FAILURE.txt`, Windows toast), and the pipeline continues for healthy accounts. Scheduled runs call `authenticate(interactive=False)` so a dead Gmail token raises a clear error instead of waiting on a browser.
- **Database paths** resolve from script location, not CWD (safe for Task Scheduler / launchd)
- **config.yaml** drives all business logic -- owner profile, briefing priorities, entities, label categories, keywords, schedules, feature flags
- **Label categories** are user-defined in config.yaml `labels` section; `build_label_config()` in organizer.py dynamically builds all category lists, label paths, colors, and protection levels at runtime
- **Briefing personalization** uses `owner.profile` and `owner.briefing_priorities` from config.yaml, injected into the board-writer system prompt
- **No check-off surface.** The web dashboard, `mark`, and `snooze` commands were retired 2026-09-16. Do not reintroduce a mark-done flow. If an item lingers, fix the window rule, not the item.

## Rules

### Do Not Modify Without Asking

- **Core briefing workflow** (fetch -> analyze -> digest pipeline)
- **Order pipeline stages** -- song order lifecycle
- **Digest email format and content**
- **Briefing database schema** (tracker.db)
- **OAuth authentication flow** -- each account has its own token file
- **Business entity definitions** in config.yaml
- **Label category system** -- user-defined via config.yaml `labels` section; `build_label_config()` in organizer.py builds all runtime structures. Do not hardcode categories.
- **Classification prompt builder** -- `_build_classification_system_prompt()` in organizer.py dynamically generates the Claude prompt from config labels. Do not re-hardcode category descriptions.
- **Deletion safety guards** -- vital category protection, low-confidence skip, attachment preservation
- **The EmailClient abstraction** -- providers must implement this interface

### Safe to Change Without Asking

- Bug fixes, dependency updates, and maintenance
- Code cleanup that doesn't alter behavior
- Improving error messages or logging
- Performance improvements
- Adding new email providers (implement EmailClient ABC)
- Adding new CLI subcommands that don't touch existing pipelines

### Important Gotchas

- Each email account requires its own authentication token -- don't consolidate token management
- `credentials/`, `data/`, `logs/`, `config.yaml`, and `.env` are user-specific -- never commit these
- Low-confidence junk classifications are archived, not deleted -- this is intentional
- **Cleanup must never touch the bot's own digest.** The digest is emailed to the owner's own inbox, which the cleanup stage sweeps ~20 min later in the same `run`. `do_cleanup_inbox()` filters out messages whose subject starts with "Daily Briefing"/"Weekly Briefing" via `_is_own_briefing()`. Keep digest subjects on that prefix (set in `digest.py`), or update the filter in lockstep.
- Emails with attachments are never auto-deleted -- safety feature
- The organized.db database prevents reprocessing -- don't clear it unless you want to reclassify everything
- The `run` command is ordered so the digest analyzes emails before cleanup can archive them
- Label operations are no-ops on non-Gmail providers -- this is by design
- The setup wizard overwrites config.yaml and .env completely on each run
- **Google OAuth app must be set to "In production" BEFORE generating tokens** (Google Auth Platform > Audience > PUBLISH APP). Two failure modes: (a) app stays in Testing, so refresh tokens expire every 7 days; (b) app was published *after* tokens were minted, so those tokens still carry the 7-day clock and must be re-minted. Symptom is `invalid_grant: Bad Request` on `creds.refresh()` after a clean week of runs. Re-auth: run `python main.py setup-accounts`. `gmail.py authenticate(interactive=True)` catches the dead-refresh `RefreshError` and falls back to the browser flow. No Google review is required for personal use.
- **Windows scheduled task settings matter**: must include `StartWhenAvailable`, `DontStopIfGoingOnBatteries`, `AllowStartIfOnBatteries`, `ExecutionTimeLimit 2h`, `MultipleInstances IgnoreNew`. The setup wizard's `_create_windows_task` writes these correctly; don't downgrade them.
- **`daily_run.bat` writes to `logs/daily_run.log`** (rolling, single file). This is the first place to look when the scheduled task didn't appear to run.
- **`daily_run.bat` runs at most once per calendar day.** After a successful run it writes today's `%DATE%` to `logs/last_success.txt`; any later launch that day logs "Skipped: already ran successfully today" and exits 0. `daily_run.bat --force` overrides. This exists because a second scheduled task or an at-logon trigger can otherwise send a duplicate digest. Never register a second task for this file.
