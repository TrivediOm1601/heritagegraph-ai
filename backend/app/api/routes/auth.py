"""B6 — authentication routes (signup, login, logout, session).

Contract and security properties:

* the session lives in an **HttpOnly, SameSite=Lax cookie** (``Secure`` in
  production / over HTTPS); no token is ever returned in a JSON body, so
  the frontend cannot accidentally persist it in ``localStorage``;
* the identity used for every ownership check comes from the validated
  server-side session, never from the request body;
* login failures are deliberately generic and take the same time for an
  unknown address as for a wrong password (no account enumeration);
* credential endpoints are rate-limited per client address;
* password hashes never leave the database layer — responses use
  :class:`~app.schemas.auth.AuthUser`.

Password reset / email verification are **not** implemented: there is no
email delivery mechanism in this deployment, and faking one would be
worse than documenting its absence (see README → Known limitations).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from ...core.config import ConfigurationError
from ...core.errors import (
    ConflictApiError,
    DependencyUnavailableError,
    UnauthorizedError,
    error_response,
)
from ...core.logging import get_logger
from ...core.security import (
    AUTH_UNAVAILABLE,
    CurrentUser,
    client_ip,
    clear_session_cookie,
    current_user_optional,
    limit_credentials,
    require_user,
    set_session_cookie,
    verify_same_origin,
)
from ...db.session import RepositoryError, session_scope
from ...repositories.user_repository import (
    create_session,
    create_user,
    delete_session,
    get_user_by_email,
)
from ...schemas.auth import AuthResponse, AuthUser, LoginRequest, SignupRequest
from ...services.auth_service import (
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE,
    hash_password,
    new_session_token,
    normalize_email,
    session_expiry,
    session_token_hash,
    session_ttl_hours,
    validate_email,
    validate_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

logger = get_logger("auth")

#: Generic credential failure — identical for unknown email and bad password.
INVALID_CREDENTIALS = "Email or password is incorrect."

#: Message for a duplicate registration (signup must tell the user why it
#: failed; login never reveals whether an address exists).
DUPLICATE_ACCOUNT = (
    "An account with that email address already exists. Try signing in instead."
)


def _to_schema(user: CurrentUser) -> AuthUser:
    return AuthUser(id=user.id, email=user.email, display_name=user.display_name)


@router.post(
    "/signup",
    response_model=AuthResponse,
    status_code=201,
    summary="Create an account",
    description=(
        "Registers an account, hashes the password with PBKDF2-HMAC-SHA256, "
        "starts a session and sets the HttpOnly session cookie. The password "
        "hash is never returned."
    ),
    responses={
        409: error_response("An account with that email address already exists."),
        422: error_response("The submitted email, password or name is invalid."),
        429: error_response("Too many attempts from this address."),
        503: error_response("The account database is unavailable."),
    },
)
async def signup(
    payload: SignupRequest, request: Request, response: Response
) -> AuthResponse:
    verify_same_origin(request)
    limit_credentials.hit(f"signup:{client_ip(request)}")

    email = normalize_email(payload.email)
    problem = validate_email(email) or validate_password(payload.password)
    if problem:
        from ...core.errors import UnprocessableEntityError

        raise UnprocessableEntityError(problem)

    display_name = (payload.display_name or "").strip() or None
    password_hash = hash_password(payload.password)

    try:
        async with session_scope() as session:
            account = await create_user(
                session,
                email=email,
                password_hash=password_hash,
                display_name=display_name,
            )
    except RepositoryError as exc:
        from ...db.session import ConflictError

        if isinstance(exc, ConflictError):
            # Duplicate address: a clear signup error is acceptable here
            # (the account is being created, not authenticated).
            raise ConflictApiError(DUPLICATE_ACCOUNT) from exc
        raise DependencyUnavailableError(AUTH_UNAVAILABLE) from exc
    except ConfigurationError as exc:
        raise DependencyUnavailableError(AUTH_UNAVAILABLE) from exc

    token = new_session_token()
    hours = session_ttl_hours()
    async with session_scope() as session:
        await create_session(
            session,
            user_id=account.id,
            token_hash=session_token_hash(token),
            expires_at=session_expiry(hours),
            user_agent=request.headers.get("user-agent"),
        )
    set_session_cookie(request, response, token, max_age=hours * 3600)
    logger.info("account created (user id %s)", account.id)
    return AuthResponse(
        user=AuthUser(
            id=account.id, email=account.email, display_name=account.display_name
        )
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Sign in",
    description=(
        "Validates credentials and starts a session. Failures are generic "
        "so an attacker cannot discover which email addresses have accounts."
    ),
    responses={
        401: error_response("The email or password is incorrect."),
        429: error_response("Too many attempts from this address."),
        503: error_response("The account database is unavailable."),
    },
)
async def login(
    payload: LoginRequest, request: Request, response: Response
) -> AuthResponse:
    verify_same_origin(request)
    limit_credentials.hit(f"login:{client_ip(request)}")

    email = normalize_email(payload.email)

    try:
        async with session_scope() as session:
            user = await get_user_by_email(session, email)
            encoded = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
            # Always run the KDF: constant work for unknown accounts.
            valid = verify_password(payload.password, encoded)
            if user is None or not valid:
                raise UnauthorizedError(INVALID_CREDENTIALS)
            user_id, display_name = user.id, user.display_name
            token = new_session_token()
            hours = session_ttl_hours()
            await create_session(
                session,
                user_id=user_id,
                token_hash=session_token_hash(token),
                expires_at=session_expiry(hours),
                user_agent=request.headers.get("user-agent"),
            )
    except UnauthorizedError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(AUTH_UNAVAILABLE) from exc

    set_session_cookie(request, response, token, max_age=hours * 3600)
    logger.info("sign-in succeeded (user id %s)", user_id)
    return AuthResponse(
        user=AuthUser(id=user_id, email=email, display_name=display_name)
    )


@router.post(
    "/logout",
    status_code=204,
    summary="Sign out",
    description=(
        "Deletes the server-side session so the cookie can never be replayed, "
        "then expires the cookie in the browser."
    ),
    responses={204: {"description": "Signed out (idempotent)."}},
)
async def logout(request: Request, response: Response) -> Response:
    verify_same_origin(request)
    token = (request.cookies.get(SESSION_COOKIE) or "").strip()
    if token:
        try:
            async with session_scope() as session:
                await delete_session(session, session_token_hash(token))
        except (ConfigurationError, RepositoryError) as exc:
            raise DependencyUnavailableError(AUTH_UNAVAILABLE) from exc
    clear_session_cookie(request, response)
    response.status_code = 204
    return response


@router.get(
    "/me",
    response_model=AuthResponse,
    summary="Current session",
    description=(
        "Returns the signed-in user. Used on page load to restore auth state "
        "after a refresh; an expired or missing session is a canonical 401."
    ),
    responses={
        401: error_response("No valid session."),
        503: error_response("The account database is unavailable."),
    },
)
async def me(request: Request) -> AuthResponse:
    user = await current_user_optional(request)
    if user is None:
        raise UnauthorizedError("Sign in to continue.")
    return AuthResponse(user=_to_schema(user))


# ---------------------------------------------------------------------------
# Convenience dependency for other routers
# ---------------------------------------------------------------------------

SessionUser = Annotated[CurrentUser, Depends(require_user)]
