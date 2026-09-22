"""SQLite persistence for the briefing side of the app.

One database file (``data/tracker.db``) holds fetched emails, the items Claude
extracted from them, calendar events, manual tasks, and follow-up threads. The
modules are split by domain; this package re-exports the public API so callers
write ``from email_manager import db`` and ``db.get_bulletin_items(...)``.

Connection handling and schema migrations live in :mod:`core`. The cleanup
ledger (``data/organized.db``) is a separate, much smaller store in
:mod:`cleanup_ledger` and follows the same pattern.
"""

from email_manager.db.bulletin import (
    BULLETIN_DAYS_AHEAD,
    BULLETIN_OVERDUE_GRACE_DAYS,
    BULLETIN_UNDATED_TTL_DAYS,
    apply_item_merge,
    get_bulletin_items,
    get_horizon_data,
    get_reconcilable_items,
)
from email_manager.db.calendar import get_upcoming_events, normalize_datetime, store_calendar_events
from email_manager.db.core import (
    SCHEMA_VERSION,
    close,
    configure,
    configured_path,
    get_connection,
    init_db,
    utcnow_iso,
)
from email_manager.db.emails import (
    get_sync_state,
    get_unlabeled_emails,
    get_unprocessed_emails,
    mark_email_processed,
    mark_emails_labeled,
    set_email_entity,
    set_sync_state,
    store_email,
)
from email_manager.db.follow_ups import (
    dismiss_follow_up,
    get_pending_follow_ups,
    mark_follow_up_replied,
    upsert_follow_up,
)
from email_manager.db.items import (
    get_active_agreements,
    get_pending_actions,
    get_pending_deadlines,
    get_pending_financial,
    get_status_summary,
    store_extractions,
)
from email_manager.db.tasks import add_task, complete_task, delete_task, get_pending_tasks

__all__ = [
    "BULLETIN_DAYS_AHEAD",
    "BULLETIN_OVERDUE_GRACE_DAYS",
    "BULLETIN_UNDATED_TTL_DAYS",
    "SCHEMA_VERSION",
    "add_task",
    "apply_item_merge",
    "close",
    "complete_task",
    "configure",
    "configured_path",
    "delete_task",
    "dismiss_follow_up",
    "get_active_agreements",
    "get_bulletin_items",
    "get_connection",
    "get_horizon_data",
    "get_pending_actions",
    "get_pending_deadlines",
    "get_pending_financial",
    "get_pending_follow_ups",
    "get_pending_tasks",
    "get_reconcilable_items",
    "get_status_summary",
    "get_sync_state",
    "get_unlabeled_emails",
    "get_unprocessed_emails",
    "get_upcoming_events",
    "init_db",
    "mark_email_processed",
    "mark_emails_labeled",
    "mark_follow_up_replied",
    "normalize_datetime",
    "set_email_entity",
    "set_sync_state",
    "store_calendar_events",
    "store_email",
    "store_extractions",
    "upsert_follow_up",
    "utcnow_iso",
]
