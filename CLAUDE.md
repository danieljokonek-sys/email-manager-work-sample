# Email Cleanup & Brief Manager

## What This Project Is

A unified AI-powered email management tool that combines two functions:

1. **Briefing Bot** -- Monitors email accounts (Gmail, Outlook, Yahoo, IMAP), analyzes emails using Claude Opus, and sends daily/weekly briefing digests with summaries of deadlines, financial obligations, action items, and agreements. The briefing is personalized using the owner's profile and priorities from config.yaml.

2. **Email Cleanup** -- Classifies, labels, archives, and deletes emails using Claude Sonnet. Keeps the inbox organized with user-defined label categories (configured in config.yaml `labels` section), color-coded Gmail labels, and smart deletion safety guards.

Business entities, label categories, owner profile, and briefing priorities are all user-configurable via the setup wizard and config.yaml. Nothing is hardcoded to a specific user.

## Current Status

- Briefing Bot: complete and in daily personal use
- Email Cleanup: in daily use; classification and labeling stable, deletion guards conservative by design
- Multi-provider support: Gmail, Outlook, Yahoo, generic IMAP
- Cross-platform: Windows (Task Scheduler) and macOS (launchd)
- The `run` command executes the full pipeline: fetch -> analyze -> digest -> cleanup

## Project Structure

```
briefing-cleanup-bot/
├── CLAUDE.md
├── README.md
├── .gitignore
└── email-manager/                 # Main application folder
    ├── main.py                    # Unified CLI entry point
    ├── config.yaml                # Accounts, entities, labels, profile, priorities, schedules (not committed)
    ├── config.template.yaml       # Clean template for new installs
    ├── .env                       # API keys (not committed)
    ├── .env.example               # Template showing required env vars
    ├── requirements.txt
    ├── run.bat / run.sh           # Launch scripts (auto-setup on first run)
    ├── daily_run.bat              # Windows Task Scheduler integration
    ├── install.sh                 # macOS installer
    ├── setup_wizard.py            # Interactive setup (profile, labels, providers, scheduling)
    ├── delete_old_emails.py       # One-time historical email cleanup script
    ├── credentials/               # OAuth tokens (not committed)
    ├── data/
    │   ├── tracker.db             # Briefing database
    │   └── organized.db           # Cleanup database
    ├── docs/
    │   └── GOOGLE_SETUP_GUIDE.md  # Google Cloud OAuth setup
    └── src/
        ├── __init__.py
        ├── email_client.py        # Abstract base for all providers
        ├── client_factory.py      # Builds the right client per provider
        ├── gmail_client.py        # Backward-compat re-export
        ├── calendar_client.py     # Google Calendar client
    ├── dashboard.py           # Local Flask web dashboard for marking items done
        ├── entities.py            # Business entity definitions
        ├── providers/
        │   ├── gmail.py           # Gmail provider (Google API + OAuth)
        │   ├── outlook.py         # Outlook provider (Microsoft Graph + MSAL)
        │   └── imap_client.py     # Yahoo, AOL, iCloud, generic IMAP
        ├── briefing/              # Briefing bot modules
        │   ├── analyzer.py        # Claude-powered email analysis (uses owner profile + priorities)
        │   ├── database.py        # Briefing SQLite schema and operations
        │   ├── digest.py          # HTML digest email generation
        │   ├── scheduler.py       # Recurring task scheduling
        │   └── orders.py          # Chorus Crafters song order tracking
        └── cleanup/               # Cleanup bot modules
            ├── organizer.py       # Claude-powered email classification (labels from config)
            └── database.py        # Cleanup SQLite tracking
```

## How It Works

### Briefing Pipeline
1. **Fetch** -- Pulls new emails from all configured accounts + calendar events
2. **Analyze** -- Claude Opus extracts deadlines, financials, action items, agreements
3. **Label** -- Applies entity labels in Gmail (skipped for non-Gmail providers)
4. **Digest** -- Generates and sends an HTML briefing email with link to web dashboard

### Cleanup Pipeline
1. **Fetch** -- Pulls inbox emails (with pagination)
2. **Check** -- Skips already-processed emails (tracked in organized.db)
3. **Classify** -- Claude Sonnet categorizes into user-defined categories (from config.yaml `labels`) with action + confidence
4. **Act** -- Labels, archives, or trashes based on classification
5. **Record** -- Stores result so emails aren't reprocessed

### Combined `run` Command
Build clients once -> Fetch -> Analyze -> Label -> Follow-ups -> Digest -> (20 min delay) -> Cleanup

## Key Technical Details

- **Python 3.10+** with Click CLI, Rich terminal output
- **Claude Opus** for briefing analysis, **Claude Sonnet** for cleanup classification
- **Multi-provider**: Gmail (full), Outlook (email + calendar), Yahoo/IMAP (email only)
- **Multiple accounts per provider** supported (e.g. 3 Gmail accounts)
- **2 SQLite databases**: tracker.db (briefing) and organized.db (cleanup)
- **Shared client instances** -- built once per `run`, reused across all pipeline stages
- **Database paths** resolve from script location, not CWD (safe for Task Scheduler / launchd)
- **config.yaml** drives all business logic -- owner profile, briefing priorities, entities, label categories, keywords, schedules, feature flags
- **Label categories** are user-defined in config.yaml `labels` section; `build_label_config()` in organizer.py dynamically builds all category lists, label paths, colors, and protection levels at runtime
- **Briefing personalization** uses `owner.profile` and `owner.briefing_priorities` from config.yaml, injected into the Opus system prompt
- **Web dashboard** (`python main.py dashboard`) -- local Flask app at localhost:5050 for marking items done via checkboxes

## Rules

### Do Not Modify Without Asking

- **Core briefing workflow** (fetch -> analyze -> digest pipeline)
- **Order pipeline stages** -- Chorus Crafters order lifecycle
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
- `credentials/`, `data/`, `config.yaml`, and `.env` are user-specific -- never commit these
- Low-confidence junk classifications are archived, not deleted -- this is intentional
- Emails with attachments are never auto-deleted -- safety feature
- The organized.db database prevents reprocessing -- don't clear it unless you want to reclassify everything
- The `run` command is ordered so the digest analyzes emails before cleanup can archive them
- Label operations are no-ops on non-Gmail providers -- this is by design
- The setup wizard overwrites config.yaml and .env completely on each run
- **Google OAuth app must be set to "In production" BEFORE generating tokens** (left nav: Google Auth Platform > Audience > PUBLISH APP — Google renamed the surface in late 2025, the control is on the Audience subpage not Overview). Two failure modes: (a) app stays in Testing → refresh tokens expire every 7 days; (b) app was published *after* tokens were minted → those tokens still carry the 7-day Testing clock and must be re-minted. Re-auth procedure: move all `credentials/token_*.json` files into `credentials/_expired_backup_YYYY-MM-DD/`, then run `python main.py setup-accounts`. The stale-token move is required because `src/providers/gmail.py` calls `creds.refresh()` first and doesn't fall back to the browser flow if a refresh_token is present. No Google review is required for personal use.
- **Windows scheduled task settings matter**: must include `StartWhenAvailable`, `DontStopIfGoingOnBatteries`, `AllowStartIfOnBatteries`, `ExecutionTimeLimit 2h`, `MultipleInstances IgnoreNew`. The setup wizard's `_create_windows_task` writes these correctly; don't downgrade them.
- **`daily_run.bat` writes to `logs/daily_run.log`** (rolling, single file). This is the first place to look when the scheduled task didn't appear to run.
