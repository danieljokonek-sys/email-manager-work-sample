"""
One-time script: Delete all emails from before 1/1/2026 UNLESS they are:
- Product keys/downloads (Licenses and Keys, Product Downloads)
- Personal email from friends/family (Personal)
- Have an attachment
"""
import logging
import yaml
from dotenv import load_dotenv
from src.gmail_client import GmailClient
from src.cleanup.organizer import EmailOrganizer

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("delete_old")

PROTECTED_CATEGORIES = {"Licenses and Keys", "Product Downloads", "Personal"}
CUTOFF_QUERY = "before:2026/01/01"
BATCH_SIZE = 50

with open("config.yaml") as f:
    config = yaml.safe_load(f)

cleanup_cfg = config.get("cleanup", {})
cleanup_accounts = cleanup_cfg.get("accounts", [])
if not cleanup_accounts:
    raise SystemExit("No cleanup account configured. Set cleanup.accounts in config.yaml.")
cleanup_account = cleanup_accounts[0]
account_cfg = next(a for a in config.get("accounts", []) if a["email"] == cleanup_account)

client = GmailClient(
    credentials_file=config["google"]["credentials_file"],
    token_file=account_cfg["token_file"],
    account_email=cleanup_account,
)
client.authenticate()

organizer = EmailOrganizer(
    owner_name=config["owner"]["name"],
    account_emails=[cleanup_account],
    model=cleanup_cfg.get("model", "claude-sonnet-4-6"),
    batch_size=25,
)

total = {"kept_category": 0, "kept_attachment": 0, "trashed": 0, "errors": 0}
page_token = None
page_num = 0

while True:
    page_num += 1
    log.info(f"Page {page_num}: fetching up to {BATCH_SIZE} emails before 2026/01/01...")

    emails, page_token = client.fetch_all_emails_paged(
        max_results=BATCH_SIZE, page_token=page_token, extra_query=CUTOFF_QUERY,
    )
    if not emails:
        log.info("No more emails.")
        break

    log.info(f"  Classifying {len(emails)} emails...")
    classifications = organizer.classify_batch(emails)
    email_map = {e["id"]: e for e in emails}

    for email_id, result in classifications.items():
        email = email_map.get(email_id, {})
        category = result.get("category", "Personal")
        subj = email.get("subject", "?")[:60]

        if category in PROTECTED_CATEGORIES:
            log.info(f"  KEEP [{category}]: {subj}")
            total["kept_category"] += 1
        elif email.get("has_attachment"):
            log.info(f"  KEEP [attachment]: {subj}")
            total["kept_attachment"] += 1
        else:
            try:
                client.trash_email(email_id)
                log.info(f"  TRASH [{category}]: {subj}")
                total["trashed"] += 1
            except Exception as e:
                log.warning(f"  ERROR trashing {email_id}: {e}")
                total["errors"] += 1

    if not page_token:
        break

print(f"\n{'='*50}")
print(f"DONE — Emails before 1/1/2026:")
print(f"  Kept (protected category): {total['kept_category']}")
print(f"  Kept (has attachment):     {total['kept_attachment']}")
print(f"  Trashed:                   {total['trashed']}")
print(f"  Errors:                    {total['errors']}")
print(f"{'='*50}")
