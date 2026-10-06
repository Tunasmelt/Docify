"""Sentry wiring (services/observability.py). Runs Sentry for real in a
subprocess, with a transport that records envelopes instead of sending them,
so the test process's own Sentry state is never touched."""

import json
import subprocess
import sys
import textwrap

from services.observability import init_sentry, sentry_options


def test_sentry_is_off_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert sentry_options() is None
    assert init_sentry() is False


def test_sentry_options_leave_out_bodies_locals_and_pii(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@o0.ingest.sentry.io/0")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    options = sentry_options()

    assert options["release"] == "abc123"
    assert options["send_default_pii"] is False
    assert options["max_request_body_size"] == "never"
    assert options["include_local_variables"] is False
    assert options["traces_sample_rate"] == 0.0


_SCRIPT = textwrap.dedent(
    """
    import json, logging, os
    import sentry_sdk
    from sentry_sdk.transport import Transport

    sent = []

    class Recorder(Transport):
        def capture_envelope(self, envelope):
            sent.append(envelope.serialize().decode("utf-8", "replace"))

    os.environ["SENTRY_DSN"] = "https://public@o0.ingest.sentry.io/0"
    from services.observability import sentry_options
    sentry_sdk.init(**sentry_options(), transport=Recorder)

    logging.getLogger("routes.ingest").error("ingest pipeline failed for document doc-1")

    def handle(question):
        secret_document_text = "Q3 revenue was 4.2M"
        raise RuntimeError("boom")

    try:
        handle("what was revenue?")
    except RuntimeError:
        sentry_sdk.capture_exception()
    sentry_sdk.flush()
    print(json.dumps(sent))
    """
)


def test_logged_errors_and_exceptions_are_reported_without_local_variables():
    result = subprocess.run([sys.executable, "-c", _SCRIPT], capture_output=True, text=True, check=True, cwd=".")
    envelopes = "\n".join(json.loads(result.stdout.strip().splitlines()[-1]))

    assert "ingest pipeline failed for document doc-1" in envelopes
    assert "RuntimeError" in envelopes and "boom" in envelopes
    assert "Q3 revenue was 4.2M" not in envelopes
    assert "what was revenue?" not in envelopes
