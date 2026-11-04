"""The status email (#96): what it says, and that it never stops a run."""

from pharos import health, notify
from test_design_faults import RUN1, at, published  # noqa: F401  (fixture)


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout):
        assert (host, port) == ("smtp.gmail.com", 465)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if password != "right":
            raise OSError("535 bad credentials")

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_email_says_what_ran(published):
    msg = notify.compose(RUN1, health.evaluate(at(10, 7, 15)))
    assert msg["Subject"] == "Pharos GREEN: run published"
    body = msg.get_content()
    assert "Pharos ran. Health GREEN, run published." in body
    assert "yf/prices:" in body and "fred/observations:" in body


def test_email_sends_to_the_sender_by_default(published):
    published.setenv("PHAROS_MAIL_FROM", "me@example.com")
    published.setenv("PHAROS_MAIL_APP_PASSWORD", "right")
    published.delenv("PHAROS_MAIL_TO", raising=False)
    FakeSMTP.sent = []
    assert notify.send_status(RUN1, health.evaluate(at(10, 7, 15)), smtp=FakeSMTP)
    (msg,) = FakeSMTP.sent
    assert (msg["From"], msg["To"]) == ("me@example.com", "me@example.com")


def test_a_failed_send_is_logged_never_raised(published, root):
    published.setenv("PHAROS_MAIL_FROM", "me@example.com")
    published.setenv("PHAROS_MAIL_APP_PASSWORD", "wrong")
    assert notify.send_status(RUN1, None, smtp=FakeSMTP) is False
    assert "535 bad credentials" in (root / "logs" / "notify.log").read_text(encoding="utf-8")
    published.delenv("PHAROS_MAIL_APP_PASSWORD")
    assert notify.send_status(RUN1, None, smtp=FakeSMTP) is False   # no settings: logged too


def test_the_suite_never_sends_for_real(published):
    published.setenv("PHAROS_MAIL_FROM", "me@example.com")
    published.setenv("PHAROS_MAIL_APP_PASSWORD", "right")
    assert notify.send_status(RUN1, None) is False                  # PHAROS_TEST_MODE=1
