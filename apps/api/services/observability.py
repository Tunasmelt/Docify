"""Error tracking with Sentry (free tier). Does nothing unless SENTRY_DSN is
set, so local development and tests never send anything.

What gets reported: unhandled exceptions, and every `logger.error` /
`logger.exception` call (the logging integration's default event level),
which covers failures the routes catch and turn into error responses, and
background ingest failures that never surface as an HTTP error.

Privacy: requests carry users' questions and documents carry their content,
so request bodies and local variables in stack frames are left out. Default
PII (IP addresses, cookies, auth headers) is sent, by the owner's choice.
Performance tracing is off to stay inside the free tier's quota.
"""

import os

import sentry_sdk


def sentry_options() -> dict | None:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return None
    return {
        "dsn": dsn,
        "environment": os.environ.get("SENTRY_ENVIRONMENT", "production"),
        # Render injects the deployed commit; ties each error to a release.
        "release": os.environ.get("RENDER_GIT_COMMIT") or None,
        "send_default_pii": True,
        "max_request_body_size": "never",
        "include_local_variables": False,
        "traces_sample_rate": 0.0,
    }


def init_sentry() -> bool:
    options = sentry_options()
    if options is None:
        return False
    sentry_sdk.init(**options)
    return True
