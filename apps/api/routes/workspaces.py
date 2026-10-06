import logging
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from postgrest.exceptions import APIError

from db import queries
from db.client import get_service_role_client
from errors import error_envelope
from models.workspaces import WorkspaceListResponse, WorkspaceNameRequest, WorkspaceResponse

logger = logging.getLogger(__name__)

router = APIRouter()

NAME_MAX_LENGTH = 60
MAX_WORKSPACES_PER_USER = 20


def _response(row: dict) -> WorkspaceResponse:
    # PostgREST returns the embedded count as {"documents": [{"count": N}]}.
    return WorkspaceResponse(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        document_count=row["documents"][0]["count"] if row["documents"] else 0,
    )


def _validation_error(message: str) -> JSONResponse:
    return JSONResponse(status_code=422, content=error_envelope("VALIDATION_ERROR", message))


def _not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "workspace not found"))


def _clean_name(raw: str) -> tuple[str | None, JSONResponse | None]:
    name = raw.strip()
    if not name:
        return None, _validation_error("name must not be empty")
    if len(name) > NAME_MAX_LENGTH:
        return None, _validation_error(f"name must be at most {NAME_MAX_LENGTH} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return None, _validation_error("name must not contain control characters")
    return name, None


def _name_conflict() -> JSONResponse:
    return JSONResponse(status_code=409, content=error_envelope("CONFLICT", "you already have a workspace with that name"))


@router.get("/workspaces", response_model=WorkspaceListResponse)
def list_workspaces(request: Request):
    rows = queries.list_workspaces(get_service_role_client(), user_id=request.state.user_id)
    return WorkspaceListResponse(workspaces=[_response(row) for row in rows])


@router.post("/workspaces", status_code=201, response_model=WorkspaceResponse)
def create_workspace(payload: WorkspaceNameRequest, request: Request):
    name, error = _clean_name(payload.name)
    if error is not None:
        return error

    user_id = request.state.user_id
    client = get_service_role_client()
    if len(queries.list_workspaces(client, user_id=user_id)) >= MAX_WORKSPACES_PER_USER:
        return JSONResponse(
            status_code=409,
            content=error_envelope("CONFLICT", f"you can have at most {MAX_WORKSPACES_PER_USER} workspaces"),
        )
    if queries.workspace_name_taken(client, user_id=user_id, name=name):
        return _name_conflict()
    try:
        row = queries.create_workspace(client, user_id=user_id, name=name)
    except APIError as exc:
        if exc.code != "23505":
            raise
        # Two creates racing past the check above: the unique index decides.
        logger.warning("create_workspace failed for user %s", user_id, exc_info=True)
        return _name_conflict()
    return _response(row)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
def rename_workspace(workspace_id: UUID, payload: WorkspaceNameRequest, request: Request):
    name, error = _clean_name(payload.name)
    if error is not None:
        return error

    user_id = request.state.user_id
    client = get_service_role_client()
    if queries.get_workspace(client, workspace_id=str(workspace_id), user_id=user_id) is None:
        return _not_found()
    if queries.workspace_name_taken(client, user_id=user_id, name=name, excluding_id=str(workspace_id)):
        return _name_conflict()
    try:
        row = queries.rename_workspace(client, workspace_id=str(workspace_id), user_id=user_id, name=name)
    except APIError as exc:
        if exc.code != "23505":
            raise
        logger.warning("rename_workspace failed for user %s", user_id, exc_info=True)
        return _name_conflict()
    return _response(row)


@router.delete("/workspaces/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: UUID, request: Request):
    user_id = request.state.user_id
    client = get_service_role_client()

    workspaces = queries.list_workspaces(client, user_id=user_id)
    target = next((row for row in workspaces if row["id"] == str(workspace_id)), None)
    if target is None:
        return _not_found()
    if len(workspaces) == 1:
        return JSONResponse(status_code=409, content=error_envelope("CONFLICT", "you cannot delete your only workspace"))
    # Documents have files in Storage that only DELETE /documents/{id} removes,
    # so a workspace with documents is never deleted from under them. Its
    # conversations are deleted with it.
    if _response(target).document_count > 0:
        return JSONResponse(
            status_code=409,
            content=error_envelope("CONFLICT", "delete this workspace's documents first, then delete the workspace"),
        )
    try:
        queries.delete_workspace(client, workspace_id=str(workspace_id), user_id=user_id)
    except APIError as exc:
        if exc.code != "23503":
            raise
        # A document was added between the check and the delete; the foreign key refused.
        logger.warning("delete_workspace refused for user %s", user_id, exc_info=True)
        return JSONResponse(status_code=409, content=error_envelope("CONFLICT", "the workspace is no longer empty"))
    return Response(status_code=204)
