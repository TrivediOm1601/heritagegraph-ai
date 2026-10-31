"""Research workspace routes: collections, notes and collection reports."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy import func, select

from ...core.config import ConfigurationError
from ...core.errors import (
    ApiError,
    DependencyUnavailableError,
    NotFoundError,
    UnprocessableEntityError,
    error_response,
)
from ...core.logging import get_logger
from ...core.security import CurrentUser, limit_export, require_user, verify_same_origin
from ...db.models import CollectionItem, ResearchCollection, ResearchNote
from ...db.session import RepositoryError, session_scope
from ...schemas.research import (
    CollectionCreateRequest,
    CollectionDetailResponse,
    CollectionItemRef,
    CollectionItemRequest,
    CollectionListResponse,
    CollectionSummary,
    CollectionUpdateRequest,
    NoteCreateRequest,
    NoteItem,
    NoteUpdateRequest,
)
from ...services.research_service import (
    add_item,
    add_note,
    create_collection,
    delete_note,
    get_collection,
    list_collections,
    list_items,
    list_notes,
    remove_item,
    render_collection_report,
    soft_delete_collection,
    update_collection,
    update_note,
)

router = APIRouter(tags=["research"])

logger = get_logger("research.routes")

RESEARCH_UNAVAILABLE = (
    "The research workspace is unavailable: the database could not be reached."
)
REPORT_UNAVAILABLE = "The collection report could not be generated."

_ID = {"min_length": 1, "max_length": 128, "pattern": r"^[^\s]+$"}

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


async def _counts(session, collection: ResearchCollection) -> tuple[int, int]:
    items = (
        await session.scalar(
            select(func.count())
            .select_from(CollectionItem)
            .where(CollectionItem.collection_id == collection.id)
        )
        or 0
    )
    notes = (
        await session.scalar(
            select(func.count())
            .select_from(ResearchNote)
            .where(ResearchNote.collection_id == collection.id)
        )
        or 0
    )
    return items, notes


def _note_item(note: ResearchNote) -> NoteItem:
    return NoteItem(
        id=note.id,
        body=note.body,
        subjectQuery=note.subject_query,
        createdAt=note.created_at,
        updatedAt=note.updated_at,
    )


async def _summary(session, collection: ResearchCollection) -> CollectionSummary:
    items, notes = await _counts(session, collection)
    return CollectionSummary(
        id=collection.id,
        name=collection.name,
        description=collection.description,
        itemCount=items,
        noteCount=notes,
        createdAt=collection.created_at,
        updatedAt=collection.updated_at,
    )


async def _owned(session, collection_id: str, user: CurrentUser) -> ResearchCollection:
    collection = await get_collection(session, collection_id, user_id=user.id)
    if collection is None:
        raise NotFoundError("No research collection exists with that id.")
    return collection


@router.post(
    "/collections",
    response_model=CollectionDetailResponse,
    status_code=201,
    summary="Create a research collection",
)
async def create_collection_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    payload: CollectionCreateRequest,
) -> CollectionDetailResponse:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            collection = await create_collection(
                session,
                user_id=user.id,
                name=payload.name,
                description=payload.description,
            )
            summary = await _summary(session, collection)
            return CollectionDetailResponse(
                **summary.model_dump(), items=[], notes=[]
            )
    except ValueError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc


@router.get(
    "/collections",
    response_model=CollectionListResponse,
    summary="List this account's research collections",
)
async def list_collections_route(
    user: Annotated[CurrentUser, Depends(require_user)],
) -> CollectionListResponse:
    try:
        async with session_scope() as session:
            rows = await list_collections(session, user_id=user.id)
            items = [await _summary(session, row) for row in rows]
            return CollectionListResponse(items=items, total=len(items))
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc


@router.get(
    "/collections/{collection_id}",
    response_model=CollectionDetailResponse,
    summary="One collection with its archives, files and notes",
)
async def get_collection_route(
    user: Annotated[CurrentUser, Depends(require_user)],
    collection_id: Annotated[str, Path(**_ID)],
) -> CollectionDetailResponse:
    try:
        async with session_scope() as session:
            collection = await _owned(session, collection_id, user)
            summary = await _summary(session, collection)
            items = await list_items(session, collection=collection)
            notes = await list_notes(
                session, collection_id=collection.id, user_id=user.id
            )
            return CollectionDetailResponse(
                **summary.model_dump(),
                items=[CollectionItemRef(**item) for item in items],
                notes=[_note_item(note) for note in notes],
            )
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc


@router.patch(
    "/collections/{collection_id}",
    response_model=CollectionDetailResponse,
    summary="Rename or re-describe a collection",
)
async def update_collection_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    collection_id: Annotated[str, Path(**_ID)],
    payload: CollectionUpdateRequest,
) -> CollectionDetailResponse:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            collection = await _owned(session, collection_id, user)
            collection = await update_collection(
                session,
                collection,
                name=payload.name,
                description=payload.description,
            )
            summary = await _summary(session, collection)
            items = await list_items(session, collection=collection)
            notes = await list_notes(
                session, collection_id=collection.id, user_id=user.id
            )
            return CollectionDetailResponse(
                **summary.model_dump(),
                items=[CollectionItemRef(**item) for item in items],
                notes=[_note_item(note) for note in notes],
            )
    except ValueError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc


@router.delete(
    "/collections/{collection_id}",
    status_code=204,
    summary="Remove a collection (its archives and files are untouched)",
)
async def delete_collection_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    collection_id: Annotated[str, Path(**_ID)],
) -> Response:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            collection = await _owned(session, collection_id, user)
            await soft_delete_collection(session, collection)
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc
    return Response(status_code=204)


@router.post(
    "/collections/{collection_id}/items",
    response_model=CollectionDetailResponse,
    summary="Add an archive or file to a collection",
    responses={
        401: error_response("Sign in to edit a collection."),
        404: error_response("The collection, archive or file does not exist."),
        422: error_response("The item reference is invalid."),
    },
)
async def add_item_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    collection_id: Annotated[str, Path(**_ID)],
    payload: CollectionItemRequest,
) -> CollectionDetailResponse:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            collection = await _owned(session, collection_id, user)
            await add_item(
                session,
                collection=collection,
                target_type=payload.type,
                target_id=payload.id,
            )
            summary = await _summary(session, collection)
            items = await list_items(session, collection=collection)
            notes = await list_notes(
                session, collection_id=collection.id, user_id=user.id
            )
            return CollectionDetailResponse(
                **summary.model_dump(),
                items=[CollectionItemRef(**item) for item in items],
                notes=[_note_item(note) for note in notes],
            )
    except LookupError as exc:
        raise NotFoundError(str(exc)) from exc
    except ValueError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc


@router.delete(
    "/collections/{collection_id}/items",
    status_code=204,
    summary="Remove one item from a collection",
)
async def remove_item_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    collection_id: Annotated[str, Path(**_ID)],
    type: Annotated[str, Query(pattern="^(archive|file)$")],
    id: Annotated[str, Query(min_length=1, max_length=128)],
) -> Response:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            collection = await _owned(session, collection_id, user)
            removed = await remove_item(
                session, collection=collection, target_type=type, target_id=id
            )
            if not removed:
                raise NotFoundError("That item is not in this collection.")
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc
    return Response(status_code=204)


@router.post(
    "/collections/{collection_id}/notes",
    response_model=NoteItem,
    status_code=201,
    summary="Add a research note",
)
async def add_note_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    collection_id: Annotated[str, Path(**_ID)],
    payload: NoteCreateRequest,
) -> NoteItem:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            collection = await _owned(session, collection_id, user)
            note = await add_note(
                session,
                collection=collection,
                body=payload.body,
                subject_query=payload.subject_query,
            )
            return _note_item(note)
    except ValueError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc


@router.patch(
    "/notes/{note_id}",
    response_model=NoteItem,
    summary="Edit a research note",
)
async def update_note_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    note_id: Annotated[str, Path(**_ID)],
    payload: NoteUpdateRequest,
) -> NoteItem:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            note = await session.get(ResearchNote, note_id)
            if note is None or note.user_id != user.id:
                raise NotFoundError("No note exists with that id.")
            note = await update_note(session, note, body=payload.body, user_id=user.id)
            return _note_item(note)
    except LookupError as exc:
        raise NotFoundError(str(exc)) from exc
    except ValueError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc


@router.delete(
    "/notes/{note_id}",
    status_code=204,
    summary="Delete a research note",
)
async def delete_note_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    note_id: Annotated[str, Path(**_ID)],
) -> Response:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            note = await session.get(ResearchNote, note_id)
            if note is None or note.user_id != user.id:
                raise NotFoundError("No note exists with that id.")
            await delete_note(session, note, user_id=user.id)
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc
    return Response(status_code=204)


@router.get(
    "/collections/{collection_id}/report.pdf",
    summary="Research report for a whole collection",
    responses={
        401: error_response("Sign in to export."),
        404: error_response("No collection exists with that id."),
        429: error_response("Too many exports; please wait."),
    },
)
async def collection_report_route(
    user: Annotated[CurrentUser, Depends(require_user)],
    collection_id: Annotated[str, Path(**_ID)],
) -> Response:
    limit_export.hit(f"export:{user.id}")
    try:
        async with session_scope() as session:
            collection = await _owned(session, collection_id, user)
            payload = await render_collection_report(
                session, collection=collection, user_email=user.email
            )
            filename = "heritagegraph-collection-report.pdf"
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESEARCH_UNAVAILABLE) from exc
    except Exception as exc:  # noqa: BLE001 — never leak internals
        logger.error("collection report failed: %s", type(exc).__name__, exc_info=exc)
        raise DependencyUnavailableError(REPORT_UNAVAILABLE) from exc

    return Response(
        content=payload,
        media_type="application/pdf",
        headers={
            **_PRIVATE_HEADERS,
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
