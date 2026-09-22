# Security

This tool runs on your own machine against your own mailboxes. It holds OAuth tokens, an API key, and the text of your email in local files.

What it does to keep that safe:

- `config.yaml`, `.env`, `credentials/`, `data/`, and `logs/` are git-ignored. The public repository has never contained any of them.
- The setup wizard writes `.env` with owner-only permissions where the OS supports it.
- Scheduled runs never open a browser; a dead token raises and alerts you instead.
- Deletion is always a move to the provider's trash, guarded as described in the README.
- Query literals built from inbound mail (thread ids) are escaped before they reach IMAP or Graph.

If you find a problem, open a GitHub issue. There is no bug bounty and no separate disclosure channel; this is a personal project.
