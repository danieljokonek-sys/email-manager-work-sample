#!/usr/bin/env python3
"""
Setup Wizard for Email Cleanup & Brief Manager
A multi-page tkinter GUI that walks the user through initial configuration.

Adapts to:
- Which email/calendar providers the user selects (Gmail, Outlook, Yahoo, IMAP)
- Multiple accounts per provider (e.g. 3 Gmail accounts)
- Windows vs macOS (Task Scheduler vs launchd)

Usage:
    python setup_wizard.py [--app-dir /path/to/app] [--python /path/to/python]
"""

import argparse
import os
import platform
import subprocess
import sys
import threading
import webbrowser

# Python version check
if sys.version_info < (3, 10):
    print(f"ERROR: Python 3.10 or newer is required. You have {sys.version}")
    print("Download the latest Python from https://www.python.org/downloads/")
    sys.exit(1)

# tkinter availability check
try:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, scrolledtext, ttk
except ImportError:
    print("ERROR: tkinter is not installed.")
    if platform.system() == "Darwin":
        print("Fix: brew install python-tk")
    elif platform.system() == "Linux":
        print("Fix: sudo apt install python3-tk  (or equivalent for your distro)")
    else:
        print("Fix: Reinstall Python from python.org and check 'tcl/tk' during install")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BG = "#f5f5f5"
ACCENT = "#1a1a2e"
BTN_COLOR = "#e94560"
BTN_FG = "#ffffff"
ENTRY_BG = "#ffffff"
LABEL_FG = "#333333"
MUTED_FG = "#666666"
HIGHLIGHT = "#e94560"

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

PROVIDERS = ["gmail", "outlook", "yahoo", "imap"]
PROVIDER_LABELS = {
    "gmail": "Gmail (Google)",
    "outlook": "Outlook / Hotmail / Live",
    "yahoo": "Yahoo Mail",
    "imap": "Other (IMAP)",
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="Email Manager Setup Wizard")
parser.add_argument("--app-dir", default=os.path.dirname(os.path.abspath(__file__)))
parser.add_argument("--python", default=sys.executable)
args, _unknown = parser.parse_known_args()

APP_DIR = os.path.abspath(args.app_dir)
PYTHON_BIN = args.python


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_button(parent, text, command, **kwargs):
    return tk.Button(parent, text=text, command=command, bg=BTN_COLOR, fg=BTN_FG,
        activebackground="#c73652", activeforeground=BTN_FG, relief="flat", bd=0,
        padx=20, pady=10, cursor="hand2", font=("", 11, "bold"), **kwargs)

def make_secondary_button(parent, text, command, **kwargs):
    return tk.Button(parent, text=text, command=command, bg=BG, fg=ACCENT,
        activebackground="#e0e0e0", activeforeground=ACCENT, relief="solid", bd=1,
        padx=16, pady=8, cursor="hand2", font=("", 10), **kwargs)

def make_label(parent, text, size=10, bold=False, color=LABEL_FG, **kwargs):
    weight = "bold" if bold else "normal"
    return tk.Label(parent, text=text, bg=BG, fg=color, font=("", size, weight), **kwargs)

def make_entry(parent, width=40, show=None):
    return tk.Entry(parent, width=width, bg=ENTRY_BG, fg=LABEL_FG, relief="solid", bd=1,
        font=("", 11), show=show or "")

def make_frame(parent, **kwargs):
    return tk.Frame(parent, bg=BG, **kwargs)


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

class SetupWizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Email Cleanup & Brief Manager — Setup Wizard")
        self.configure(bg=BG)
        self.resizable(False, False)

        w, h = 720, 600
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        # Collected data
        self.data = {
            "owner_name": tk.StringVar(),
            "owner_phone": tk.StringVar(),
            "owner_profile": "",       # free-text, set from Text widget
            "briefing_priorities": "",  # free-text, set from Text widget
            "api_key": tk.StringVar(),
            "accounts": [],          # list of dicts: {name, email, provider, imap_server, smtp_server, password}
            "entities": [],
            "labels": [],            # list of dicts: {name, group, description, protection, auto_delete, enabled}
            "digest_email": tk.StringVar(),
            "digest_time": tk.StringVar(value="08:00"),
            "digest_account": tk.StringVar(),
            "ms_client_id": tk.StringVar(),
        }

        self.current_page = 0
        self.page_sequence = []  # dynamic — rebuilt based on provider selection
        self.all_pages = {}      # name -> frame
        self._existing_config = None  # loaded from disk if available

        self._load_existing_config()
        self._build_chrome()
        self._build_all_pages()
        self._rebuild_sequence()
        self._show_page(0)

    # ------------------------------------------------------------------
    # Load previous config (for re-runs)
    # ------------------------------------------------------------------

    def _load_existing_config(self):
        """Load existing config.yaml and .env so re-running the wizard
        pre-fills all fields from the previous setup. The wizard then
        overwrites both files completely on finish."""
        import re as _re

        # Load .env
        env_path = os.path.join(APP_DIR, ".env")
        if os.path.isfile(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ANTHROPIC_API_KEY"):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        self.data["api_key"].set(val)
                    elif line.startswith("MS_CLIENT_ID"):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        self.data["ms_client_id"].set(val)

        # Load config.yaml
        config_path = os.path.join(APP_DIR, "config.yaml")
        if not os.path.isfile(config_path):
            return
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            return

        self._existing_config = cfg

        # Owner
        owner = cfg.get("owner", {})
        if owner.get("name"):
            self.data["owner_name"].set(owner["name"])
        if owner.get("phone"):
            self.data["owner_phone"].set(owner["phone"])
        if owner.get("profile"):
            self.data["owner_profile"] = owner["profile"]
        if owner.get("briefing_priorities"):
            self.data["briefing_priorities"] = owner["briefing_priorities"]

        # Labels
        if cfg.get("labels"):
            self.data["labels"] = list(cfg["labels"])

        # Digest settings
        digest = cfg.get("digest", {})
        if digest.get("send_to"):
            self.data["digest_email"].set(digest["send_to"])
        if digest.get("send_from_account"):
            self.data["digest_account"].set(digest["send_from_account"])
        schedule = digest.get("schedule", {})
        if schedule.get("daily_summary"):
            self.data["digest_time"].set(schedule["daily_summary"])

        # Microsoft client ID
        ms = cfg.get("microsoft", {})
        if ms.get("client_id") and not self.data["ms_client_id"].get():
            self.data["ms_client_id"].set(ms["client_id"])

    # ------------------------------------------------------------------
    # Chrome
    # ------------------------------------------------------------------

    def _build_chrome(self):
        header = tk.Frame(self, bg=ACCENT, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="Email Manager Setup", bg=ACCENT, fg="#ffffff",
                 font=("", 14, "bold")).pack(side="left", padx=20, pady=12)

        os_label = "Windows" if IS_WINDOWS else "macOS" if IS_MAC else "Linux"
        tk.Label(header, text=os_label, bg=ACCENT, fg="#888888",
                 font=("", 9)).pack(side="right", padx=20)

        prog_frame = tk.Frame(self, bg=ACCENT, height=6)
        prog_frame.pack(fill="x")
        prog_frame.pack_propagate(False)
        self._prog_canvas = tk.Canvas(prog_frame, bg="#3a3a5c", height=6, bd=0, highlightthickness=0)
        self._prog_canvas.pack(fill="x")
        self._prog_bar = self._prog_canvas.create_rectangle(0, 0, 0, 6, fill=BTN_COLOR, width=0)

        self._step_frame = tk.Frame(self, bg=BG, pady=6)
        self._step_frame.pack(fill="x")
        self._step_labels = []

        tk.Frame(self, bg="#dddddd", height=1).pack(fill="x")
        self._page_container = tk.Frame(self, bg=BG)
        self._page_container.pack(fill="both", expand=True, padx=40, pady=20)

        tk.Frame(self, bg="#dddddd", height=1).pack(fill="x")
        footer = tk.Frame(self, bg=BG, pady=14)
        footer.pack(fill="x", padx=40)
        self._back_btn = make_secondary_button(footer, "< Back", self._go_back)
        self._back_btn.pack(side="left")
        self._next_btn = make_button(footer, "Next >", self._go_next)
        self._next_btn.pack(side="right")

    def _rebuild_step_labels(self):
        for w in self._step_frame.winfo_children():
            w.destroy()
        self._step_labels = []
        for i, name in enumerate(self.page_sequence):
            display = name.replace("_", " ").title()
            lbl = tk.Label(self._step_frame, text=f"{i+1}. {display}", bg=BG, fg=MUTED_FG,
                           font=("", 8), anchor="center")
            lbl.pack(side="left", expand=True, fill="x")
            self._step_labels.append(lbl)

    def _update_progress(self, page_idx):
        self.update_idletasks()
        total_w = self._prog_canvas.winfo_width() or 720
        total = len(self.page_sequence)
        filled = int(total_w * (page_idx + 1) / total) if total else 0
        self._prog_canvas.coords(self._prog_bar, 0, 0, filled, 6)
        for i, lbl in enumerate(self._step_labels):
            if i == page_idx:
                lbl.config(fg=HIGHLIGHT, font=("", 8, "bold"))
            elif i < page_idx:
                lbl.config(fg="#888888", font=("", 8))
            else:
                lbl.config(fg=MUTED_FG, font=("", 8))

    # ------------------------------------------------------------------
    # Dynamic page sequence
    # ------------------------------------------------------------------

    def _get_providers_in_use(self):
        return set(a.get("provider_var", tk.StringVar()).get() for a in self.data["accounts"] if a.get("provider_var"))

    def _rebuild_sequence(self):
        providers = self._get_providers_in_use()
        seq = ["welcome", "profile", "api_key", "accounts", "entities", "labels", "digest", "priorities"]
        if "gmail" in providers or not providers:
            seq.append("google_setup")
        if "outlook" in providers:
            seq.append("microsoft_setup")
        if providers & {"yahoo", "imap"}:
            seq.append("imap_credentials")
        seq.append("authorize")
        seq.append("scheduling")
        seq.append("done")
        self.page_sequence = seq
        self._rebuild_step_labels()

    # ------------------------------------------------------------------
    # Page management
    # ------------------------------------------------------------------

    def _show_page(self, idx):
        for page in self.all_pages.values():
            page.pack_forget()
        self.current_page = idx
        page_name = self.page_sequence[idx]
        self.all_pages[page_name].pack(fill="both", expand=True)
        self._update_progress(idx)

        self._back_btn.config(state="disabled" if idx == 0 else "normal")
        if idx == len(self.page_sequence) - 1:
            self._next_btn.config(text="Finish", command=self._finish)
        else:
            self._next_btn.config(text="Next >", command=self._go_next)

        on_show = getattr(self.all_pages[page_name], "on_show", None)
        if callable(on_show):
            on_show()

    def _go_next(self):
        page_name = self.page_sequence[self.current_page]
        validate = getattr(self.all_pages[page_name], "validate", None)
        if callable(validate) and not validate():
            return
        # Rebuild sequence in case accounts page changed providers
        if page_name == "accounts":
            self._rebuild_sequence()
        if self.current_page < len(self.page_sequence) - 1:
            self._show_page(self.current_page + 1)

    def _go_back(self):
        if self.current_page > 0:
            self._show_page(self.current_page - 1)

    def _finish(self):
        try:
            self._write_config()
            self._setup_scheduling()
            messagebox.showinfo("Setup Complete",
                "Your Email Manager is configured!\n\nThe wizard will now close.")
            self.quit()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to write config:\n{exc}")

    # ------------------------------------------------------------------
    # Build all pages
    # ------------------------------------------------------------------

    def _build_all_pages(self):
        self.all_pages = {
            "welcome": self._build_welcome(),
            "profile": self._build_profile(),
            "api_key": self._build_api_key(),
            "accounts": self._build_accounts(),
            "entities": self._build_entities(),
            "labels": self._build_labels(),
            "digest": self._build_digest(),
            "priorities": self._build_priorities(),
            "google_setup": self._build_google_setup(),
            "microsoft_setup": self._build_microsoft_setup(),
            "imap_credentials": self._build_imap_credentials(),
            "authorize": self._build_authorize(),
            "scheduling": self._build_scheduling(),
            "done": self._build_done(),
        }

    # ---- Welcome ----

    def _build_welcome(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Welcome to Email Manager", size=18, bold=True, color=ACCENT).pack(pady=(20, 8))
        make_label(frame, "AI-powered email digest + inbox cleanup", size=11, color=MUTED_FG).pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=20)

        os_name = "Windows" if IS_WINDOWS else "macOS" if IS_MAC else "Linux"
        desc = (
            f"Detected OS: {os_name}\n\n"
            "This wizard will guide you through setup in about 5 minutes.\n\n"
            "By the end you will have:\n"
            "  -  Email accounts connected (Gmail, Outlook, Yahoo, or IMAP)\n"
            "  -  AI-powered daily briefing digests\n"
            "  -  Automatic inbox cleanup and labeling\n"
            "  -  A daily schedule configured for your OS\n\n"
            "Make sure you have your Anthropic API key ready."
        )
        tk.Label(frame, text=desc, bg=BG, fg=LABEL_FG, font=("", 10),
                 justify="left", anchor="w").pack(fill="x", padx=10)

        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=12)
        row = make_frame(frame)
        row.pack(fill="x", padx=10)
        make_label(row, "Your name:", size=10).pack(side="left", padx=(0, 8))
        ne = make_entry(row, width=25)
        ne.config(textvariable=self.data["owner_name"])
        ne.pack(side="left", padx=(0, 16))
        make_label(row, "Phone (optional):", size=10).pack(side="left", padx=(0, 8))
        pe = make_entry(row, width=16)
        pe.config(textvariable=self.data["owner_phone"])
        pe.pack(side="left")

        return frame

    # ---- Profile ----

    def _build_profile(self):
        frame = make_frame(self._page_container)
        make_label(frame, "About You", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))
        make_label(frame, "Tell the AI who you are. This helps it personalize your daily briefing\n"
                   "and understand context when analyzing your emails.",
                   size=10, color=MUTED_FG, justify="left").pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=12)

        make_label(frame, "Describe yourself, your work, and what you do:", size=10, bold=True).pack(anchor="w")
        make_label(frame, "Example: \"I'm a freelance designer managing two client businesses and a rental property.\n"
                   "I work with 3 regular clients and track invoices monthly.\"",
                   size=9, color=MUTED_FG, justify="left").pack(anchor="w", pady=(2, 6))

        self._profile_text = tk.Text(frame, height=8, width=70, bg=ENTRY_BG, fg=LABEL_FG,
                                     relief="solid", bd=1, font=("", 10), wrap="word")
        self._profile_text.pack(fill="x")
        if self.data["owner_profile"]:
            self._profile_text.insert("1.0", self.data["owner_profile"])

        def on_leave(*_):
            self.data["owner_profile"] = self._profile_text.get("1.0", "end-1c").strip()
        self._profile_text.bind("<FocusOut>", on_leave)

        def validate():
            self.data["owner_profile"] = self._profile_text.get("1.0", "end-1c").strip()
            return True
        frame.validate = validate
        return frame

    # ---- API Key ----

    def _build_api_key(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Anthropic API Key", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))
        make_label(frame, "Required for AI-powered email analysis and cleanup.", size=10, color=MUTED_FG).pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=16)

        make_label(frame, "Get your API key at:", size=10).pack(anchor="w")
        link = tk.Label(frame, text="  console.anthropic.com", bg=BG, fg=BTN_COLOR,
                        font=("", 10, "underline"), cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda _: webbrowser.open("https://console.anthropic.com"))

        make_label(frame, "\nPaste your API key below:", size=10).pack(anchor="w")
        entry = make_entry(frame, width=48, show="*")
        entry.config(textvariable=self.data["api_key"])
        entry.pack(anchor="w", pady=(4, 0))
        make_label(frame, "Stored locally in .env only — never transmitted.", size=9, color=MUTED_FG).pack(anchor="w", pady=(8, 0))

        def validate():
            key = self.data["api_key"].get().strip()
            if not key:
                messagebox.showwarning("Required", "Please enter your Anthropic API key.")
                return False
            return True
        frame.validate = validate
        return frame

    # ---- Accounts (multi-provider) ----

    def _build_accounts(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Email Accounts", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))
        make_label(frame, "Add the email accounts you want to monitor.\nOnly add the ones you use — you can have multiple of the same type.",
                   size=10, color=MUTED_FG, justify="left").pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=10)

        # Account list area (scrollable-ish)
        self._acc_list_frame = make_frame(frame)
        self._acc_list_frame.pack(fill="x")

        # Pre-populate from existing config, or start with one empty row
        if self._existing_config and self._existing_config.get("accounts"):
            for acct_cfg in self._existing_config["accounts"]:
                self._add_account_row(
                    name=acct_cfg.get("name", ""),
                    email=acct_cfg.get("email", ""),
                    provider=acct_cfg.get("provider", "gmail"),
                    imap_server=acct_cfg.get("imap_server", ""),
                    smtp_server=acct_cfg.get("smtp_server", ""),
                )
        else:
            self._add_account_row()

        btn_row = make_frame(frame)
        btn_row.pack(anchor="w", pady=(8, 0))
        make_secondary_button(btn_row, "+ Add Account", self._add_account_row).pack(side="left")

        def validate():
            if not self.data["accounts"]:
                messagebox.showwarning("Required", "Add at least one email account.")
                return False
            for i, acct in enumerate(self.data["accounts"]):
                email = acct["email_var"].get().strip()
                name = acct["name_var"].get().strip()
                provider = acct["provider_var"].get()
                if not name:
                    messagebox.showwarning("Required", f"Enter a name for account {i+1}.")
                    return False
                if not email or "@" not in email:
                    messagebox.showwarning("Required", f"Enter a valid email for account {i+1}.")
                    return False
                if provider == "imap":
                    if not acct.get("imap_var", tk.StringVar()).get().strip():
                        messagebox.showwarning("Required", f"Enter IMAP server for '{name}' (IMAP account).")
                        return False
            return True
        frame.validate = validate
        return frame

    def _add_account_row(self, name="", email="", provider="gmail", imap_server="", smtp_server=""):
        idx = len(self.data["accounts"])
        acct = {
            "name_var": tk.StringVar(value=name),
            "email_var": tk.StringVar(value=email),
            "provider_var": tk.StringVar(value=provider),
            "imap_var": tk.StringVar(value=imap_server),
            "smtp_var": tk.StringVar(value=smtp_server),
        }
        self.data["accounts"].append(acct)

        row = make_frame(self._acc_list_frame)
        row.pack(fill="x", pady=4)
        acct["_row_widget"] = row

        # Name
        make_label(row, "Name:", size=9, color=MUTED_FG).pack(side="left")
        name_e = make_entry(row, width=14)
        name_e.config(textvariable=acct["name_var"])
        name_e.pack(side="left", padx=(2, 6))

        # Email
        make_label(row, "Email:", size=9, color=MUTED_FG).pack(side="left")
        email_e = make_entry(row, width=22)
        email_e.config(textvariable=acct["email_var"])
        email_e.pack(side="left", padx=(2, 6))

        # Provider dropdown
        provider_menu = tk.OptionMenu(row, acct["provider_var"], *PROVIDERS)
        provider_menu.config(bg=ENTRY_BG, fg=LABEL_FG, relief="solid", bd=1, font=("", 9), width=8)
        provider_menu.pack(side="left", padx=(0, 4))

        # Remove button
        def remove(a=acct, r=row):
            r.destroy()
            self.data["accounts"].remove(a)
        if idx > 0:
            tk.Button(row, text="X", command=remove, bg="#cc3333", fg="white",
                      font=("", 8, "bold"), bd=0, padx=6, pady=2).pack(side="left")

        # IMAP fields (shown inline, initially hidden for gmail)
        imap_row = make_frame(self._acc_list_frame)
        acct["_imap_row"] = imap_row

        def on_provider_change(*_args, a=acct, ir=imap_row):
            for w in ir.winfo_children():
                w.destroy()
            if a["provider_var"].get() == "imap":
                ir.pack(fill="x", pady=(0, 4))
                make_label(ir, "    IMAP server:", size=9, color=MUTED_FG).pack(side="left")
                ie = make_entry(ir, width=20)
                ie.config(textvariable=a["imap_var"])
                ie.pack(side="left", padx=(2, 8))
                make_label(ir, "SMTP server:", size=9, color=MUTED_FG).pack(side="left")
                se = make_entry(ir, width=20)
                se.config(textvariable=a["smtp_var"])
                se.pack(side="left", padx=2)
            else:
                ir.pack_forget()

        acct["provider_var"].trace_add("write", on_provider_change)

    # ---- Entities ----

    def _build_entities(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Business Entities", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))
        make_label(frame, "Define up to 4 entities to track. Leave blank to skip.",
                   size=10, color=MUTED_FG).pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=12)

        # Pre-populate from existing config, or use placeholder examples
        existing_entities = []
        if self._existing_config:
            for key, ent in (self._existing_config.get("entities") or {}).items():
                kw = ent.get("keywords", [])
                kw_str = ", ".join(kw) if isinstance(kw, list) else str(kw)
                existing_entities.append((ent.get("name", ""), ent.get("description", ""), kw_str))

        defaults = [
            ("My Business", "Primary business", "invoice, client, contract"),
            ("Rental Property", "Rental income and tenants", "rent, tenant, lease"),
            ("Side Project", "Freelance work", "freelance, project, proposal"),
            ("", "", ""),
        ]
        for i in range(4):
            if i < len(existing_entities):
                vals = existing_entities[i]
            elif not existing_entities:
                vals = defaults[i]
            else:
                vals = ("", "", "")
            self.data["entities"].append({
                "name": tk.StringVar(value=vals[0]),
                "description": tk.StringVar(value=vals[1]),
                "keywords": tk.StringVar(value=vals[2]),
            })

        headers = make_frame(frame)
        headers.pack(fill="x", pady=(0, 2))
        make_label(headers, "Name", size=9, color=MUTED_FG).pack(side="left", padx=(0, 80))
        make_label(headers, "Description", size=9, color=MUTED_FG).pack(side="left", padx=(0, 80))
        make_label(headers, "Keywords", size=9, color=MUTED_FG).pack(side="left")

        for i in range(4):
            row = make_frame(frame)
            row.pack(fill="x", pady=3)
            ne = make_entry(row, width=16); ne.config(textvariable=self.data["entities"][i]["name"]); ne.pack(side="left", padx=(0, 6))
            de = make_entry(row, width=22); de.config(textvariable=self.data["entities"][i]["description"]); de.pack(side="left", padx=(0, 6))
            ke = make_entry(row, width=26); ke.config(textvariable=self.data["entities"][i]["keywords"]); ke.pack(side="left")

        return frame

    # ---- Labels ----

    # Standard label groups available to any user
    STANDARD_LABEL_GROUPS = {
        "Finance": [
            {"name": "Tax", "description": "Tax documents, W-2s, 1099s, CPA correspondence, tax refunds", "protection": "vital"},
            {"name": "Fees and Bills", "description": "Subscription charges, utility bills, payment confirmations, invoices", "protection": "standard"},
            {"name": "Banking and Investments", "description": "Bank statements, credit cards, investment accounts, loans", "protection": "vital"},
        ],
        "Legal & Government": [
            {"name": "Legal", "description": "Contracts, legal notices, attorney correspondence", "protection": "vital"},
            {"name": "Government", "description": "DMV, agencies, voter registration, government correspondence", "protection": "standard"},
        ],
        "Shopping": [
            {"name": "Product Purchases", "description": "Order confirmations, receipts, warranty info", "protection": "standard"},
            {"name": "Product Downloads", "description": "Software downloads, digital product delivery", "protection": "standard"},
            {"name": "Licenses and Keys", "description": "Software license keys, activation codes, serial numbers", "protection": "vital"},
            {"name": "Shipping and Delivery", "description": "Package tracking, delivery notifications", "protection": "standard"},
        ],
        "Security": [
            {"name": "Account Security", "description": "Password resets, 2FA codes, security alerts, breach notifications", "protection": "vital"},
            {"name": "Logins and Verification", "description": "Email verification, new device sign-ins, login confirmations", "protection": "standard"},
        ],
        "Health & Travel": [
            {"name": "Health and Insurance", "description": "Medical, dental, prescriptions, insurance claims", "protection": "vital"},
            {"name": "Travel", "description": "Flight confirmations, hotel reservations, trip itineraries", "protection": "standard"},
        ],
        "Personal": [
            {"name": "Personal", "description": "Friends, family, personal correspondence", "protection": "standard"},
            {"name": "Education", "description": "Courses, certifications, training, academic", "protection": "standard"},
            {"name": "Employment", "description": "Job-related, HR, payroll, benefits", "protection": "vital"},
        ],
    }

    def _build_labels(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Label Categories", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))
        make_label(frame, "Choose which label categories the AI uses to organize your inbox.\n"
                   "Entity labels are created automatically from your entities above.\n"
                   "You can also add your own custom categories.",
                   size=10, color=MUTED_FG, justify="left").pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=8)

        # Scrollable area
        canvas = tk.Canvas(frame, bg=BG, highlightthickness=0, height=300)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        self._labels_inner = make_frame(canvas)
        self._labels_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._labels_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Track checkbox vars for standard labels
        self._label_vars = {}  # name -> BooleanVar
        self._label_desc_widgets = {}  # name -> Entry widget

        # Pre-populate: which labels are already enabled from existing config?
        existing_names = {l["name"] for l in self.data.get("labels", [])}
        existing_by_name = {l["name"]: l for l in self.data.get("labels", [])}

        # Standard groups
        for group_name, cats in self.STANDARD_LABEL_GROUPS.items():
            make_label(self._labels_inner, group_name, size=10, bold=True, color=ACCENT).pack(anchor="w", pady=(8, 2))
            for cat in cats:
                row = make_frame(self._labels_inner)
                row.pack(fill="x", pady=1, padx=(16, 0))

                # Default: enable if in existing config, or if no existing config (first run)
                default_on = cat["name"] in existing_names if existing_names else True
                var = tk.BooleanVar(value=default_on)
                self._label_vars[cat["name"]] = var

                cb = tk.Checkbutton(row, variable=var, bg=BG, activebackground=BG)
                cb.pack(side="left")
                make_label(row, cat["name"], size=9, bold=True).pack(side="left", padx=(0, 6))

                # Editable description
                existing_desc = existing_by_name.get(cat["name"], {}).get("description", cat["description"])
                desc_entry = make_entry(row, width=50)
                desc_entry.insert(0, existing_desc)
                desc_entry.pack(side="left", fill="x", expand=True)
                self._label_desc_widgets[cat["name"]] = desc_entry

                # Store metadata on the var for retrieval
                var._group = group_name
                var._protection = cat["protection"]

        # Junk (always on, not a checkbox)
        tk.Frame(self._labels_inner, bg="#dddddd", height=1).pack(fill="x", pady=8)
        make_label(self._labels_inner, "Junk (always enabled — auto-deletes spam, marketing, newsletters)",
                   size=9, bold=True, color="#cc3333").pack(anchor="w", padx=16)

        # Custom categories section
        tk.Frame(self._labels_inner, bg="#dddddd", height=1).pack(fill="x", pady=8)
        make_label(self._labels_inner, "Custom Categories", size=10, bold=True, color=ACCENT).pack(anchor="w")
        make_label(self._labels_inner, "Add your own categories. Format: group name, category name, description.",
                   size=9, color=MUTED_FG).pack(anchor="w")

        self._custom_labels_frame = make_frame(self._labels_inner)
        self._custom_labels_frame.pack(fill="x", pady=4)
        self._custom_label_rows = []

        # Pre-populate custom labels from existing config (ones not in standard groups)
        standard_names = set()
        for cats in self.STANDARD_LABEL_GROUPS.values():
            for cat in cats:
                standard_names.add(cat["name"])
        # Also exclude entity names (those are auto-generated)
        entity_names = {self.data["entities"][i]["name"].get().strip() for i in range(len(self.data["entities"])) if self.data["entities"][i]["name"].get().strip()}

        for lbl in self.data.get("labels", []):
            if lbl["name"] not in standard_names and lbl["name"] != "Junk" and lbl["name"] not in entity_names:
                self._add_custom_label_row(
                    group=lbl.get("group", ""),
                    name=lbl.get("name", ""),
                    description=lbl.get("description", ""),
                    protection=lbl.get("protection", "standard"),
                )

        add_btn = make_secondary_button(self._labels_inner, "+ Add Custom Category", self._add_custom_label_row)
        add_btn.pack(anchor="w", pady=(4, 8), padx=16)

        def validate():
            self._collect_labels()
            return True
        frame.validate = validate
        return frame

    def _add_custom_label_row(self, group="", name="", description="", protection="standard"):
        row_data = {
            "group_var": tk.StringVar(value=group),
            "name_var": tk.StringVar(value=name),
            "desc_var": tk.StringVar(value=description),
            "protection_var": tk.StringVar(value=protection),
        }
        self._custom_label_rows.append(row_data)

        row = make_frame(self._custom_labels_frame)
        row.pack(fill="x", pady=2, padx=16)
        row_data["_widget"] = row

        make_label(row, "Group:", size=8, color=MUTED_FG).pack(side="left")
        ge = make_entry(row, width=12)
        ge.config(textvariable=row_data["group_var"])
        ge.pack(side="left", padx=(2, 4))

        make_label(row, "Name:", size=8, color=MUTED_FG).pack(side="left")
        ne = make_entry(row, width=14)
        ne.config(textvariable=row_data["name_var"])
        ne.pack(side="left", padx=(2, 4))

        make_label(row, "Desc:", size=8, color=MUTED_FG).pack(side="left")
        de = make_entry(row, width=24)
        de.config(textvariable=row_data["desc_var"])
        de.pack(side="left", padx=(2, 4))

        prot_menu = tk.OptionMenu(row, row_data["protection_var"], "vital", "standard", "none")
        prot_menu.config(bg=ENTRY_BG, fg=LABEL_FG, relief="solid", bd=1, font=("", 8), width=7)
        prot_menu.pack(side="left", padx=(0, 4))

        def remove(rd=row_data, r=row):
            r.destroy()
            self._custom_label_rows.remove(rd)
        tk.Button(row, text="X", command=remove, bg="#cc3333", fg="white",
                  font=("", 8, "bold"), bd=0, padx=6, pady=2).pack(side="left")

    def _collect_labels(self):
        """Gather all label selections into self.data['labels']."""
        labels = []

        # Entity-derived labels (auto-generated from entities page)
        for ent_data in self.data["entities"]:
            ename = ent_data["name"].get().strip()
            edesc = ent_data["description"].get().strip()
            if ename:
                labels.append({
                    "name": ename,
                    "group": "Business",
                    "description": edesc,
                    "protection": "vital",
                })

        # Standard labels (checked ones only)
        for group_name, cats in self.STANDARD_LABEL_GROUPS.items():
            for cat in cats:
                var = self._label_vars.get(cat["name"])
                if var and var.get():
                    desc_widget = self._label_desc_widgets.get(cat["name"])
                    desc = desc_widget.get().strip() if desc_widget else cat["description"]
                    labels.append({
                        "name": cat["name"],
                        "group": group_name,
                        "description": desc,
                        "protection": cat["protection"],
                    })

        # Custom labels
        for row_data in self._custom_label_rows:
            name = row_data["name_var"].get().strip()
            if name:
                labels.append({
                    "name": name,
                    "group": row_data["group_var"].get().strip(),
                    "description": row_data["desc_var"].get().strip(),
                    "protection": row_data["protection_var"].get(),
                })

        # Junk is always included
        labels.append({
            "name": "Junk",
            "group": "",
            "description": "Marketing, spam, newsletters, unsolicited bulk email",
            "protection": "none",
            "auto_delete": True,
        })

        self.data["labels"] = labels

    # ---- Digest ----

    def _build_digest(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Digest Settings", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))
        make_label(frame, "Configure your daily AI briefing email.", size=10, color=MUTED_FG).pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=16)

        make_label(frame, "Send daily briefing to:", size=10, bold=True).pack(anchor="w")
        de = make_entry(frame, width=42); de.config(textvariable=self.data["digest_email"]); de.pack(anchor="w", pady=(4, 12))

        make_label(frame, "Send from which account:", size=10, bold=True).pack(anchor="w")
        self._digest_acct_frame = make_frame(frame)
        self._digest_acct_frame.pack(anchor="w", pady=(4, 12))

        make_label(frame, "Delivery time (24h HH:MM):", size=10, bold=True).pack(anchor="w")
        te = make_entry(frame, width=10); te.config(textvariable=self.data["digest_time"]); te.pack(anchor="w", pady=(4, 0))

        def on_show():
            for w in self._digest_acct_frame.winfo_children():
                w.destroy()
            choices = [a["name_var"].get() or f"Account {i+1}" for i, a in enumerate(self.data["accounts"])]
            if not self.data["digest_account"].get() and choices:
                self.data["digest_account"].set(choices[0])
            if choices:
                tk.OptionMenu(self._digest_acct_frame, self.data["digest_account"], *choices).pack(side="left")
        frame.on_show = on_show

        def validate():
            if not self.data["digest_email"].get().strip() or "@" not in self.data["digest_email"].get():
                messagebox.showwarning("Required", "Enter a valid email for the digest.")
                return False
            return True
        frame.validate = validate
        return frame

    # ---- Briefing Priorities ----

    def _build_priorities(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Briefing Priorities", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))
        make_label(frame, "What types of information matter most in your daily briefing?\n"
                   "The AI uses this to decide what to emphasize, what to flag as urgent,\n"
                   "and how to organize your digest.",
                   size=10, color=MUTED_FG, justify="left").pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=12)

        make_label(frame, "Describe your priorities:", size=10, bold=True).pack(anchor="w")
        make_label(frame, "Example: \"Financial obligations and deadlines are top priority. Always surface\n"
                   "anything about invoices, upcoming client events, or overdue payments. I care less\n"
                   "about routine shipping notifications.\"",
                   size=9, color=MUTED_FG, justify="left").pack(anchor="w", pady=(2, 6))

        self._priorities_text = tk.Text(frame, height=8, width=70, bg=ENTRY_BG, fg=LABEL_FG,
                                        relief="solid", bd=1, font=("", 10), wrap="word")
        self._priorities_text.pack(fill="x")
        if self.data["briefing_priorities"]:
            self._priorities_text.insert("1.0", self.data["briefing_priorities"])

        def on_leave(*_):
            self.data["briefing_priorities"] = self._priorities_text.get("1.0", "end-1c").strip()
        self._priorities_text.bind("<FocusOut>", on_leave)

        def validate():
            self.data["briefing_priorities"] = self._priorities_text.get("1.0", "end-1c").strip()
            return True
        frame.validate = validate
        return frame

    # ---- Google Cloud Setup (conditional: only if Gmail accounts) ----

    def _build_google_setup(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Google Cloud Setup", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))

        gmail_count = sum(1 for a in self.data["accounts"] if a.get("provider_var", tk.StringVar()).get() == "gmail")
        make_label(frame, "Required because you have Gmail account(s). One-time setup.",
                   size=10, color=MUTED_FG).pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=10)

        steps = (
            "1.  Open Google Cloud Console (button below)\n"
            "2.  Create a new project (or select existing)\n"
            "3.  Go to APIs & Services > Library\n"
            "       - Enable  Gmail API\n"
            "       - Enable  Google Calendar API\n"
            "4.  Go to Google Auth Platform > Branding\n"
            "       - User type: External, fill in app name + emails, Save\n"
            "       - Skip adding Test Users (step 5 makes them unnecessary)\n"
            "5.  PUBLISH THE APP NOW (before generating tokens):\n"
            "       - Go to Google Auth Platform > Audience\n"
            "       - Click  PUBLISH APP  (status becomes 'In production')\n"
            "       - Tokens minted while still in Testing expire after 7 days,\n"
            "         even if you publish later -- so publish FIRST.\n"
            "       - No Google review needed for personal use.\n"
            "6.  Go to APIs & Services > Credentials > Create Credentials\n"
            "       - Choose OAuth client ID > Desktop app\n"
            "7.  Download the JSON and rename to credentials.json\n"
            f"8.  Place it in:\n"
            f"       {os.path.join(APP_DIR, 'credentials', 'credentials.json')}"
        )
        tk.Label(frame, text=steps, bg=BG, fg=LABEL_FG, font=("", 9),
                 justify="left", anchor="w").pack(fill="x", padx=4)

        btn_row = make_frame(frame)
        btn_row.pack(fill="x", pady=(12, 4))
        make_button(btn_row, "Open Google Cloud Console",
                    lambda: webbrowser.open("https://console.cloud.google.com")).pack(side="left", padx=(0, 10))

        self._creds_status = tk.StringVar()
        tk.Label(btn_row, textvariable=self._creds_status, bg=BG, fg="#2ecc71", font=("", 9, "bold")).pack(side="left")

        def check():
            path = os.path.join(APP_DIR, "credentials", "credentials.json")
            if os.path.isfile(path):
                self._creds_status.set("credentials.json found!")
            else:
                self._creds_status.set("Not found yet")
        make_secondary_button(frame, "Check for credentials.json", check).pack(anchor="w", pady=(6, 0))
        return frame

    # ---- Microsoft Setup (conditional: only if Outlook accounts) ----

    def _build_microsoft_setup(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Microsoft Azure Setup", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))
        make_label(frame, "Required because you have Outlook/Hotmail account(s).",
                   size=10, color=MUTED_FG).pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=10)

        steps = (
            "1.  Go to portal.azure.com > App registrations\n"
            "2.  Click 'New registration'\n"
            "       - Name: Email Manager\n"
            "       - Supported account types: Personal Microsoft accounts\n"
            "       - Redirect URI: leave blank (we use device code flow)\n"
            "3.  Copy the Application (client) ID below\n"
            "4.  Go to API permissions > Add > Microsoft Graph:\n"
            "       - Mail.ReadWrite, Mail.Send, Calendars.Read\n"
            "5.  Click 'Grant admin consent' if prompted"
        )
        tk.Label(frame, text=steps, bg=BG, fg=LABEL_FG, font=("", 9),
                 justify="left", anchor="w").pack(fill="x", padx=4)

        btn_row = make_frame(frame)
        btn_row.pack(fill="x", pady=(10, 0))
        make_button(btn_row, "Open Azure Portal",
                    lambda: webbrowser.open("https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade")).pack(side="left")

        make_label(frame, "\nApplication (client) ID:", size=10, bold=True).pack(anchor="w")
        ce = make_entry(frame, width=42)
        ce.config(textvariable=self.data["ms_client_id"])
        ce.pack(anchor="w", pady=(4, 0))

        def validate():
            has_outlook = any(a["provider_var"].get() == "outlook" for a in self.data["accounts"])
            if has_outlook and not self.data["ms_client_id"].get().strip():
                messagebox.showwarning("Required", "Enter your Azure app client ID for Outlook access.")
                return False
            return True
        frame.validate = validate
        return frame

    # ---- IMAP Credentials (conditional: only if Yahoo/IMAP accounts) ----

    def _build_imap_credentials(self):
        frame = make_frame(self._page_container)
        make_label(frame, "IMAP / Yahoo Credentials", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))
        make_label(frame, "Enter app passwords for your IMAP-based email accounts.\nThese are stored in the local .env file only.",
                   size=10, color=MUTED_FG, justify="left").pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=10)

        self._imap_cred_frame = make_frame(frame)
        self._imap_cred_frame.pack(fill="x")

        info = (
            "\nYahoo: Go to Yahoo Account > Security > App Passwords > generate one\n"
            "Other IMAP: Use your email password or generate an app-specific password"
        )
        make_label(frame, info, size=9, color=MUTED_FG, justify="left").pack(anchor="w")

        def on_show():
            for w in self._imap_cred_frame.winfo_children():
                w.destroy()
            for acct in self.data["accounts"]:
                prov = acct["provider_var"].get()
                if prov not in ("yahoo", "imap"):
                    continue
                name = acct["name_var"].get() or acct["email_var"].get()
                row = make_frame(self._imap_cred_frame)
                row.pack(fill="x", pady=4)
                make_label(row, f"{name} ({prov}):", size=10).pack(side="left", padx=(0, 8))
                if "password_var" not in acct:
                    acct["password_var"] = tk.StringVar()
                pe = make_entry(row, width=30, show="*")
                pe.config(textvariable=acct["password_var"])
                pe.pack(side="left")
        frame.on_show = on_show
        return frame

    # ---- Authorize ----

    def _build_authorize(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Authorize Accounts", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))
        make_label(frame, "Click below to authenticate each email account.\nGmail opens a browser; Outlook shows a device code; IMAP tests the connection.",
                   size=10, color=MUTED_FG, justify="left").pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=10)

        self._auth_output = scrolledtext.ScrolledText(frame, height=10, bg="#1a1a2e", fg="#00ff88",
            font=("Courier", 9), relief="flat", state="disabled")
        self._auth_output.pack(fill="both", expand=True)

        self._auth_btn = make_button(frame, "Authorize All Accounts", self._run_auth)
        self._auth_btn.pack(pady=(10, 0))
        self._auth_done = False

        def validate():
            if not self._auth_done:
                return messagebox.askyesno("Skip?", "Authorization not completed. Skip?")
            return True
        frame.validate = validate
        return frame

    def _run_auth(self):
        self._auth_btn.config(state="disabled", text="Running...")
        self._append_auth("Starting account authorization...\n")
        def worker():
            try:
                cmd = [PYTHON_BIN, os.path.join(APP_DIR, "main.py"), "setup-accounts"]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, cwd=APP_DIR)
                for line in proc.stdout:
                    self._append_auth(line)
                proc.wait()
                if proc.returncode == 0:
                    self._append_auth("\nAuthorization complete!\n")
                    self._auth_done = True
                else:
                    self._append_auth(f"\nProcess exited with code {proc.returncode}\n")
            except Exception as exc:
                self._append_auth(f"\nError: {exc}\n")
            finally:
                self.after(0, lambda: self._auth_btn.config(state="normal", text="Re-run Authorization"))
        threading.Thread(target=worker, daemon=True).start()

    def _append_auth(self, text):
        def _do():
            self._auth_output.config(state="normal")
            self._auth_output.insert("end", text)
            self._auth_output.see("end")
            self._auth_output.config(state="disabled")
        self.after(0, _do)

    # ---- Scheduling (OS-specific) ----

    def _build_scheduling(self):
        frame = make_frame(self._page_container)
        make_label(frame, "Daily Schedule", size=16, bold=True, color=ACCENT).pack(pady=(16, 4))

        if IS_WINDOWS:
            make_label(frame, "We'll set up Windows Task Scheduler to run daily.", size=10, color=MUTED_FG).pack()
            tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=10)
            info = (
                "A scheduled task will be created that:\n"
                "  - Runs daily at your configured digest time\n"
                "  - Also runs at logon if the scheduled time was missed\n"
                "  - Runs on battery power\n\n"
                "This requires admin privileges. Click the button below\n"
                "and approve the elevation prompt."
            )
            tk.Label(frame, text=info, bg=BG, fg=LABEL_FG, font=("", 10), justify="left", anchor="w").pack(fill="x")
            self._sched_status = tk.StringVar()
            make_button(frame, "Create Scheduled Task", self._create_windows_task).pack(anchor="w", pady=(12, 4))
            tk.Label(frame, textvariable=self._sched_status, bg=BG, fg="#2ecc71", font=("", 10, "bold")).pack(anchor="w")

        elif IS_MAC:
            make_label(frame, "We'll set up a macOS launchd job to run daily.", size=10, color=MUTED_FG).pack()
            tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=10)
            info = (
                "A LaunchAgent plist will be created that:\n"
                "  - Runs daily at your configured digest time\n"
                "  - Catches up if your Mac was asleep at the scheduled time\n\n"
                "No admin privileges needed."
            )
            tk.Label(frame, text=info, bg=BG, fg=LABEL_FG, font=("", 10), justify="left", anchor="w").pack(fill="x")
            self._sched_status = tk.StringVar()
            make_button(frame, "Install Launch Agent", self._create_mac_launchd).pack(anchor="w", pady=(12, 4))
            tk.Label(frame, textvariable=self._sched_status, bg=BG, fg="#2ecc71", font=("", 10, "bold")).pack(anchor="w")

        else:
            make_label(frame, "Set up a cron job to run the manager daily.", size=10, color=MUTED_FG).pack()
            tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=10)
            time = self.data["digest_time"].get() or "08:00"
            parts = time.split(":")
            h, m = parts[0] if len(parts) > 0 else "8", parts[1] if len(parts) > 1 else "0"
            cmd = f"{m} {h} * * * cd {APP_DIR} && {PYTHON_BIN} main.py run >> logs/cron.log 2>&1"
            tk.Label(frame, text=f"Add this line to your crontab (crontab -e):\n\n{cmd}",
                     bg=BG, fg=LABEL_FG, font=("Courier", 9), justify="left", anchor="w").pack(fill="x")
            self._sched_status = tk.StringVar()

        return frame

    def _create_windows_task(self):
        time_str = self.data["digest_time"].get().strip() or "09:00"
        bat_path = os.path.join(APP_DIR, "daily_run.bat")
        # Write a temp PowerShell script to avoid escaping hell with spaces in paths
        ps_script = os.path.join(APP_DIR, "_create_task.ps1")
        with open(ps_script, "w") as f:
            f.write(f'$action = New-ScheduledTaskAction -Execute \'{bat_path}\' -WorkingDirectory \'{APP_DIR}\'\n')
            f.write(f'$t1 = New-ScheduledTaskTrigger -Daily -At \'{time_str}\'\n')
            f.write(f'$t2 = New-ScheduledTaskTrigger -AtLogOn\n')
            # Safety settings learned from production:
            #   StartWhenAvailable: catches missed runs when PC was off
            #   DontStopIfGoingOnBatteries + AllowStartIfOnBatteries: works on laptops
            #   ExecutionTimeLimit 2h: kills hung runs before they block the next day
            #   MultipleInstances=Parallel: stale runs don't block new ones (combined with ExecutionTimeLimit)
            f.write(f'$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew\n')
            f.write(f'Register-ScheduledTask -TaskName \'EmailManager\' -Action $action -Trigger $t1,$t2 -Settings $settings ')
            f.write(f'-Description \'Email Cleanup and Brief Manager - daily run. Logs to logs/daily_run.log\' -Force\n')
        try:
            subprocess.Popen([
                "powershell", "-Command",
                f"Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -File \"{ps_script}\"'"
            ])
            self._sched_status.set("Task creation requested -- approve the admin prompt")
        except Exception as e:
            self._sched_status.set(f"Failed: {e}")

    def _create_mac_launchd(self):
        time = self.data["digest_time"].get().strip() or "08:00"
        try:
            hour, minute = map(int, time.split(":"))
        except ValueError:
            hour, minute = 8, 0

        plist_dir = os.path.expanduser("~/Library/LaunchAgents")
        os.makedirs(plist_dir, exist_ok=True)
        plist_path = os.path.join(plist_dir, "com.emailmanager.daily.plist")

        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.emailmanager.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>{PYTHON_BIN}</string>
        <string>{os.path.join(APP_DIR, "main.py")}</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key><string>{APP_DIR}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>{hour}</integer>
        <key>Minute</key><integer>{minute}</integer>
    </dict>
    <key>StandardOutPath</key><string>{os.path.join(APP_DIR, "logs", "run.log")}</string>
    <key>StandardErrorPath</key><string>{os.path.join(APP_DIR, "logs", "run_error.log")}</string>
</dict>
</plist>"""
        os.makedirs(os.path.join(APP_DIR, "logs"), exist_ok=True)
        with open(plist_path, "w") as f:
            f.write(plist)
        try:
            subprocess.run(["launchctl", "unload", plist_path], capture_output=True, check=False)
            subprocess.run(["launchctl", "load", plist_path], capture_output=True, check=False)
            self._sched_status.set("Launch agent installed!")
        except Exception as e:
            self._sched_status.set(f"Plist saved but launchctl failed: {e}")

    # ---- Done ----

    def _build_done(self):
        frame = make_frame(self._page_container)
        make_label(frame, "You're all set!", size=20, bold=True, color=ACCENT).pack(pady=(20, 8))
        make_label(frame, "Email Cleanup & Brief Manager is configured.", size=11, color=MUTED_FG).pack()
        tk.Frame(frame, bg="#dddddd", height=1).pack(fill="x", pady=16)

        self._summary_lbl = tk.Label(frame, text="", bg=BG, fg=LABEL_FG, font=("", 10),
                                     justify="left", anchor="w")
        self._summary_lbl.pack(fill="x", padx=8)

        os_name = "Windows" if IS_WINDOWS else "macOS" if IS_MAC else "Linux"
        next_steps = (
            "Next steps:\n"
            f"  -  Your daily run is scheduled via {'Task Scheduler' if IS_WINDOWS else 'launchd' if IS_MAC else 'cron'}\n"
            "  -  Run  python main.py run  at any time to trigger manually\n"
            "  -  Edit  config.yaml  to adjust entities, keywords, schedules\n"
            f"  -  App directory:  {APP_DIR}"
        )
        make_label(frame, next_steps, size=9, color=MUTED_FG, justify="left", anchor="w").pack(fill="x", padx=8, pady=(12, 0))

        def on_show():
            accts = []
            for a in self.data["accounts"]:
                email = a["email_var"].get()
                prov = a["provider_var"].get()
                if email:
                    accts.append(f"{email} ({prov})")
            entities = [self.data["entities"][i]["name"].get() for i in range(4) if self.data["entities"][i]["name"].get().strip()]
            label_count = len([l for l in self.data.get("labels", []) if l.get("name") != "Junk"])
            has_profile = "Yes" if self.data.get("owner_profile") else "No"
            has_priorities = "Yes" if self.data.get("briefing_priorities") else "No"
            summary = (
                f"  Accounts:     {', '.join(accts) or '(none)'}\n"
                f"  Entities:     {', '.join(entities) or 'None'}\n"
                f"  Labels:       {label_count} categories + Junk\n"
                f"  Profile:      {has_profile}\n"
                f"  Priorities:   {has_priorities}\n"
                f"  Digest to:    {self.data['digest_email'].get() or '(not set)'}\n"
                f"  Daily time:   {self.data['digest_time'].get()}"
            )
            self._summary_lbl.config(text=summary)
        frame.on_show = on_show
        return frame

    # ------------------------------------------------------------------
    # Config writing
    # ------------------------------------------------------------------

    def _write_config(self):
        os.makedirs(APP_DIR, exist_ok=True)
        os.makedirs(os.path.join(APP_DIR, "credentials"), exist_ok=True)

        # .env
        env_lines = [f'ANTHROPIC_API_KEY="{self.data["api_key"].get().strip()}"']
        ms_id = self.data["ms_client_id"].get().strip()
        if ms_id:
            env_lines.append(f'MS_CLIENT_ID="{ms_id}"')
        # IMAP/Yahoo passwords
        for acct in self.data["accounts"]:
            prov = acct["provider_var"].get()
            if prov in ("yahoo", "imap") and "password_var" in acct:
                pw = acct["password_var"].get().strip()
                if pw:
                    name = acct["name_var"].get().strip().upper().replace(" ", "_") or "ACCOUNT"
                    env_lines.append(f'{prov.upper()}_PASSWORD_{name}="{pw}"')

        env_path = os.path.join(APP_DIR, ".env")
        with open(env_path, "w") as f:
            f.write("\n".join(env_lines) + "\n")

        # config.yaml
        accounts = []
        for acct in self.data["accounts"]:
            name = acct["name_var"].get().strip()
            email = acct["email_var"].get().strip()
            provider = acct["provider_var"].get()
            if not name or not email:
                continue
            safe_name = name.lower().replace(" ", "_")
            entry = {
                "name": name, "email": email, "provider": provider,
                "token_file": f"credentials/token_{safe_name}.json",
            }
            if provider == "imap":
                imap_s = acct.get("imap_var", tk.StringVar()).get().strip()
                smtp_s = acct.get("smtp_var", tk.StringVar()).get().strip()
                if imap_s:
                    entry["imap_server"] = imap_s
                if smtp_s:
                    entry["smtp_server"] = smtp_s
            accounts.append(entry)

        digest_email = self.data["digest_email"].get().strip()
        digest_time = self.data["digest_time"].get().strip()
        digest_account = self.data["digest_account"].get().strip() or (accounts[0]["name"] if accounts else "")

        entities = {}
        for i in range(4):
            ename = self.data["entities"][i]["name"].get().strip()
            edesc = self.data["entities"][i]["description"].get().strip()
            ekw_raw = self.data["entities"][i]["keywords"].get().strip()
            ekw = [k.strip() for k in ekw_raw.split(",") if k.strip()] if ekw_raw else []
            if ename:
                # Generate a clean key from the entity name (e.g. "My Business" -> "my_business")
                ekey = ename.lower().replace(" ", "_").replace("-", "_")
                ekey = "".join(c for c in ekey if c.isalnum() or c == "_")
                entities[ekey] = {"name": ename, "description": edesc, "keywords": ekw}

        # Collect labels from the labels page
        self._collect_labels()

        # Build YAML
        owner_name = self.data["owner_name"].get().strip()
        owner_phone = self.data["owner_phone"].get().strip()
        owner_profile = self.data.get("owner_profile", "")
        briefing_priorities = self.data.get("briefing_priorities", "")

        y = ["# Email Cleanup & Brief Manager Configuration", "# Generated by setup_wizard.py", ""]
        y += ["owner:", f'  name: "{owner_name}"', f'  phone: "{owner_phone}"']

        # Multi-line profile and priorities use YAML literal block style
        if owner_profile:
            y.append("  profile: |")
            for line in owner_profile.split("\n"):
                y.append(f"    {line}")
        else:
            y.append('  profile: ""')

        if briefing_priorities:
            y.append("  briefing_priorities: |")
            for line in briefing_priorities.split("\n"):
                y.append(f"    {line}")
        else:
            y.append('  briefing_priorities: ""')
        y.append("")

        y += ["accounts:"]
        for acct in accounts:
            y.append(f'  - name: "{acct["name"]}"')
            y.append(f'    provider: "{acct["provider"]}"')
            y.append(f'    email: "{acct["email"]}"')
            y.append(f'    token_file: "{acct["token_file"]}"')
            if acct.get("imap_server"):
                y.append(f'    imap_server: "{acct["imap_server"]}"')
            if acct.get("smtp_server"):
                y.append(f'    smtp_server: "{acct["smtp_server"]}"')
            y.append('    primary_entities: ["personal"]')
        y.append("")

        y += ["digest:", f'  send_to: "{digest_email}"', f'  send_from_account: "{digest_account}"',
              "  schedule:", f'    daily_summary: "{digest_time}"', '    weekly_deep_dive: "monday 09:00"', ""]

        y += ["entities:"]
        if entities:
            for key, ent in entities.items():
                kw = "[" + ", ".join(f'"{k}"' for k in ent["keywords"]) + "]"
                y += [f"  {key}:", f'    name: "{ent["name"]}"', f'    description: "{ent["description"]}"',
                      f"    keywords: {kw}", "    accounts_receivable_from: []", "    accounts_payable_to: []"]
        y.append("")

        # Labels
        y += ["labels:"]
        for lbl in self.data.get("labels", []):
            y.append(f'  - name: "{lbl["name"]}"')
            y.append(f'    group: "{lbl.get("group", "")}"')
            # Escape quotes in description
            desc = lbl.get("description", "").replace('"', '\\"')
            y.append(f'    description: "{desc}"')
            y.append(f'    protection: "{lbl.get("protection", "standard")}"')
            if lbl.get("auto_delete"):
                y.append("    auto_delete: true")
        y.append("")

        # Provider-specific sections
        has_gmail = any(a["provider"] == "gmail" for a in accounts)
        has_outlook = any(a["provider"] == "outlook" for a in accounts)

        if has_gmail:
            y += ["google:", '  credentials_file: "credentials/credentials.json"',
                  "  max_emails_per_fetch: 100", "  lookback_days: 90", "  calendar_days_ahead: 14", ""]
        if has_outlook and ms_id:
            y += ["microsoft:", f'  client_id: "{ms_id}"', '  tenant_id: "common"', ""]

        y += ["sms:", '  forwarding_email_subject_prefix: "Fwd: Text from"', "  enabled: false", ""]
        y += ["analysis:", '  model: "claude-opus-4-6"', "  batch_size: 20", ""]

        cleanup_accounts = [a["email"] for a in accounts[:1]]
        y += ["cleanup:", '  model: "claude-sonnet-4-6"', "  batch_size: 25",
              "  delay_after_digest_minutes: 20", "  accounts:"]
        for ca in cleanup_accounts:
            y.append(f'    - "{ca}"')
        y.append("")

        y += ["features:", "  auto_label_emails: true", "  follow_up_detection: true",
              "  follow_up_days: 3", "  nag_after_days: 3", ""]

        config_path = os.path.join(APP_DIR, "config.yaml")
        with open(config_path, "w") as f:
            f.write("\n".join(y))

    def _setup_scheduling(self):
        # Scheduling is handled by the scheduling page buttons, not here
        pass


if __name__ == "__main__":
    app = SetupWizard()
    app.mainloop()
