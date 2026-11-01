"""Authorization 헤더에서 Supabase 로그인 사용자를 확인한다."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.services.supabase_auth import (
    AuthenticatedUser,
    AuthenticationRequiredError,
    AuthVerifier,
)


bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_verifier(request: Request) -> AuthVerifier:
    verifier = getattr(request.app.state, "auth_verifier", None)
    if verifier is None:
        raise RuntimeError("사용자 인증 서비스가 초기화되지 않았습니다.")
    return verifier


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    verifier: Annotated[AuthVerifier, Depends(get_auth_verifier)],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationRequiredError("로그인이 필요합니다.")
    return verifier.verify(credentials.credentials)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
