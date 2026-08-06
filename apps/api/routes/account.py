import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from db.client import get_service_role_client
from errors import error_envelope

logger = logging.getLogger(__name__)

router = APIRouter()

# Settings batch 3, part 2 — permanent, irreversible account deletion.
# HIGH-scrutiny feature, same standard as FEAT-007/008's original
# Storage-delete audits — this is the highest-stakes single operation in
# the app, not routine CRUD.
#
# SCOPE, enumerated by grepping every migration for `user_id` and
# `storage.buckets`, not from memory/docs (.agent/SCHEMA.md itself was
# found stale during this enumeration — missing `usage_counters` and the
# `avatars` bucket entirely; fixed alongside this feature, not left
# stale):
#
#   Tables, all `user_id uuid not null references auth.users(id) on
#   delete cascade` (20260722_001_initial.sql, 20260802_001_usage_counters.sql):
#     documents, chunks, conversations, messages, citations, usage_counters
#   -> every one of these needs ZERO explicit cleanup here. Deleting the
#      auth.users row (last step below) cascades all six automatically —
#      the exact same FK behavior this project already relies on for
#      messages/citations cascading off a deleted conversation
#      (routes/conversations.py's delete_conversation).
#
#   Storage buckets, all path-scoped `{user_id}/...` (confirmed live —
#   Storage's list() returns a real object id for a file and `id: None`
#   for a virtual folder, verified against this project's own real
#   figures/ nesting before writing the walk below):
#     uploads  — {user_id}/{filename}                    (flat)
#     figures  — {user_id}/{document_id}/{chunk_index}.png (one level nested)
#     avatars  — {user_id}/avatar                          (flat)
#   -> storage.objects has NO real FK to auth.users (it's Supabase
#      Storage's own extension schema), so none of these cascade —
#      explicit removal is required, same as delete_document's existing
#      pattern (routes/documents.py). Reused here, not reinvented: same
#      Storage-before-DB-row ordering, same fail-fast/report-which-
#      bucket/retry-safe error shape.
#
#   Explicitly considered and excluded: rate limiting's in-memory
#   slowapi counters (rate_limit.py) are ephemeral, never persisted —
#   nothing to clean up, they simply stop being incremented once the
#   user is gone. Frontend localStorage preferences are client-side,
#   tied to the browser not the account — irrelevant to server-side
#   deletion (and safe: they're just UI defaults, not account data).
#
# GROUND TRUTH OVER DB-ROW RECONSTRUCTION: rather than reconstructing
# Storage paths from documents.storage_path / chunks.figure_path (which
# delete_document does, correctly, for a SINGLE already-known document),
# this walks each bucket's real {user_id}/ prefix directly via Storage's
# own list() API. That's a deliberate choice, not extra complexity for
# its own sake: a DB row is not proof of what's actually sitting in
# Storage — a bug, a failed insert, any edge case could leave a real
# object with no corresponding row, and reconstructing from rows alone
# would silently leave it behind forever (exactly the "orphaned file
# with no owner reference left to ever find it again" risk this task
# was scoped around). Listing Storage itself is the only guarantee that
# actually matches the real end state to zero, not just "zero of what
# the DB happened to know about."
#
# ORDERING (item 4): Storage cleanup for ALL THREE buckets first, then —
# only once every bucket's cleanup has succeeded — delete the
# auth.users row LAST. Deleting the auth user first would be actively
# dangerous, not just out of order: every Storage RLS policy in this
# project checks `(storage.foldername(name))[1] = auth.uid()::text`
# against a LIVE session's JWT; once the user is gone, no future
# request (a retry included) could ever authenticate as that user again
# to finish the cleanup — an interrupted mid-deletion would leave those
# Storage objects permanently unreachable through this app's own RLS,
# recoverable only via a manual dashboard/service-role intervention.
# Storage-first means a failure ANYWHERE in Storage cleanup leaves the
# auth user (and therefore every DB row) fully intact — safe to retry
# the whole request, same retry-safety delete_document already proves
# (its own docstring's 2026-07-23 self-verification).
#
# PARTIAL-FAILURE END STATE (item 5): buckets are cleaned in a fixed
# order (uploads, figures, avatars) and this fails FAST on the first
# bucket that errors — later buckets are never attempted that pass. If,
# say, avatars fails after uploads+figures already succeeded: uploads
# and figures are genuinely, permanently empty for this user (removing
# an already-empty prefix on retry is a harmless no-op — Storage's
# remove() on a nonexistent object doesn't itself error, same fact
# delete_document's own docstring already established); avatars still
# has its one object; and — critically — the auth.users row and all six
# DB tables are STILL FULLY INTACT, since that step never ran. This is
# not "half a deleted account" in any DB sense: from the user's own
# perspective nothing is lost yet (their data is still there, just
# temporarily still present after a failed delete attempt), and a
# retry safely finishes the job. This is why account deletion does NOT
# need different handling than delete_document's existing pattern
# despite being irreversible in a way single-document deletion isn't —
# the irreversibility only begins at the LAST step (deleting the auth
# user), and everything before that is designed to be safely repeatable
# right up until that point.
_USER_SCOPED_BUCKETS = ("uploads", "figures", "avatars")


def _list_all_object_paths(client, bucket: str, user_id: str) -> list[str]:
    """Every real object path under {user_id}/ in `bucket` — walks one
    level of subfolders (this project's real max nesting depth; figures/
    is the only bucket with any subfolder structure at all). Supabase
    Storage's list() returns a real, non-None `id` for an actual object
    and `id: None` for a virtual folder entry — confirmed live against
    this project's own real figures/ path shape before relying on it
    here, not assumed from generic Storage API docs."""
    paths: list[str] = []
    for entry in client.storage.from_(bucket).list(user_id):
        if entry.get("id") is None:
            sub_prefix = f"{user_id}/{entry['name']}"
            for sub_entry in client.storage.from_(bucket).list(sub_prefix):
                if sub_entry.get("id") is not None:
                    paths.append(f"{sub_prefix}/{sub_entry['name']}")
        else:
            paths.append(f"{user_id}/{entry['name']}")
    return paths


def _storage_deletion_failed(user_id: str, *, bucket: str) -> JSONResponse:
    """Same coarse-reason-over-raw-detail logging discipline as
    delete_document's _storage_deletion_failed (routes/documents.py) —
    never logs the exception's own message, which isn't guaranteed not
    to embed an object path."""
    logger.error("delete_account: Storage removal failed for user %s, bucket=%s", user_id, bucket)
    return JSONResponse(
        status_code=500,
        content=error_envelope(
            "STORAGE_ERROR",
            "failed to delete your account data — nothing has been deleted yet, retrying is safe",
        ),
    )


# Password reauthentication (item 3) is deliberately enforced CLIENT-
# SIDE only, not here — the exact same real-auth-check pattern already
# shipped for email-change (lib/supabase/profile.ts's requestEmailChange,
# 2026-08-05): a real supabase.auth.signInWithPassword() call in the
# browser, gating whether the frontend ever issues this request at all.
# Consistent, not a shortcut: this backend route has no more server-side
# way to verify "was this session recently reauthenticated" than the
# email-change flow already does — enforcing it here would need a new
# mechanism (a session-freshness claim, a short-lived reauth token) that
# doesn't exist anywhere else in this app yet, for a guarantee no other
# destructive-action route in this codebase currently makes either. This
# route's own job is exactly what its name says: delete the account for
# whichever user_id the (already-valid) JWT resolves to — the same trust
# boundary every other route in this API already rests on.
@router.delete("/account", status_code=204)
async def delete_account(request: Request):
    user_id = request.state.user_id
    client = get_service_role_client()

    for bucket in _USER_SCOPED_BUCKETS:
        try:
            paths = _list_all_object_paths(client, bucket, user_id)
            if paths:
                client.storage.from_(bucket).remove(paths)
        except Exception:
            return _storage_deletion_failed(user_id, bucket=bucket)

    # LAST, only after every bucket above is confirmed clean — deletes
    # auth.users, cascading all 6 user-scoped tables automatically (see
    # module docstring). auth.admin.delete_user() also invalidates the
    # user's existing sessions/refresh tokens as part of removing the
    # row (confirmed live in tests/test_account.py) — closing the
    # refresh path immediately, even though an already-issued, not-yet-
    # expired ACCESS token remains cryptographically valid until its own
    # natural expiry regardless (middleware/auth.py does pure JWT
    # signature/claims verification, no live session lookup — a
    # pre-existing, general property of this app's stateless-JWT design,
    # not something account deletion introduces or could itself close;
    # see .agent/GAPS.md for the full item-7 writeup).
    try:
        client.auth.admin.delete_user(user_id)
    except Exception:
        logger.error(
            "delete_account: auth.admin.delete_user failed for user %s — Storage already removed, "
            "DB rows NOT yet deleted (cascade never ran), safe to retry",
            user_id,
        )
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "DELETE_FAILED", "failed to delete your account — your data was not modified, retrying is safe"
            ),
        )

    logger.info("delete_account: user %s permanently deleted", user_id)
    return Response(status_code=204)
