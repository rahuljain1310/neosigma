"""Route decorators for explicit authorization demarcation."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from fastapi import HTTPException, status

from app.models.user import Role, User

F = TypeVar("F", bound=Callable[..., Any])


def admin(endpoint: F) -> F:
    """Demarcate and enforce an admin-only handler.

    The wrapped callable must accept a ``user: User`` (or ``actor: User``)
    argument — typically via ``Depends(get_current_user)``. Enforcement uses
    ``User.role``. Cross-org checks remain the authorization policy's job.
    """

    @wraps(endpoint)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        user = _extract_user(endpoint, args, kwargs)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid Authorization header. Use: Bearer <access_token>",
            )
        if user.role != Role.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin role required",
            )
        return await endpoint(*args, **kwargs)

    # Preserve the original signature so FastAPI can still inject Depends(...).
    wrapper.__signature__ = inspect.signature(endpoint)  # type: ignore[attr-defined]
    wrapper.__authz_requires_admin__ = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]


def _extract_user(endpoint: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> User | None:
    if isinstance(kwargs.get("user"), User):
        return kwargs["user"]
    if isinstance(kwargs.get("actor"), User):
        return kwargs["actor"]

    bound = inspect.signature(endpoint).bind_partial(*args, **kwargs)
    for value in bound.arguments.values():
        if isinstance(value, User):
            return value
    return None
