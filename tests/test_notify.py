from pathlib import Path

import pytest

from email_manager.config import Config
from email_manager.notify import MARKER_FILENAME, notify_auth_failure
from tests.conftest import FakeEmailClient


def test_alert_goes_through_first_working_account_and_writes_marker(
    home: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("email_manager.notify.sys.platform", "linux")
    broken = FakeEmailClient("a@x.com")

    def boom(**kw: object) -> None:
        raise RuntimeError("smtp down")

    broken.send_email = boom  # type: ignore[method-assign]
    working = FakeEmailClient("b@x.com")

    notify_auth_failure(config, [broken, working], [("dead@x.com", "token expired")], total=3)

    assert working.outgoing and working.outgoing[0][0] == "sam@example.com"
    assert "dead@x.com" in working.outgoing[0][2]
    marker = home / "logs" / MARKER_FILENAME
    assert marker.exists() and "setup-accounts" in marker.read_text(encoding="utf-8")


def test_alert_without_recipient_still_writes_marker(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("email_manager.notify.sys.platform", "linux")
    notify_auth_failure(Config(), [], [("dead@x.com", "x")], total=1)
    assert (home / "logs" / MARKER_FILENAME).exists()
