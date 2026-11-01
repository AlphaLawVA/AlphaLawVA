"""Supabase access token을 검증하고 인증된 사용자 식별자를 반환한다."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AuthenticationRequiredError(Exception):
    """유효한 로그인 토큰이 없는 경우."""


class AuthenticationServiceUnavailableError(Exception):
    """Supabase 인증 서버에 연결할 수 없는 경우."""


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: UUID
    email: str | None = None


class AuthVerifier(Protocol):
    def verify(self, access_token: str) -> AuthenticatedUser: ...


@dataclass(frozen=True)
class SupabaseAuthSettings:
    url: str
    publishable_key: str

    @classmethod
    def from_env(cls) -> SupabaseAuthSettings | None:
        load_dotenv(PROJECT_ROOT / ".env")
        # 로컬 통합 개발에서는 이미 프론트에 설정한 공개 URL/key를 재사용한다.
        load_dotenv(PROJECT_ROOT / "frontend" / ".env.local")
        url = (
            os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL") or ""
        ).strip()
        key = (
            os.getenv("SUPABASE_PUBLISHABLE_KEY")
            or os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
            or ""
        ).strip()
        if not url and not key:
            return None
        if not url or not key:
            missing = "SUPABASE_URL" if not url else "SUPABASE_PUBLISHABLE_KEY"
            raise ValueError(f"Supabase 인증 설정이 없습니다: {missing}")
        return cls(url=url.rstrip("/"), publishable_key=key)


class SupabaseAuthVerifier:
    """Supabase Auth의 user endpoint로 토큰과 사용자 정보를 검증한다."""

    def __init__(
        self, settings: SupabaseAuthSettings, *, timeout_seconds: float = 5.0
    ) -> None:
        self._settings = settings
        self._timeout_seconds = timeout_seconds

    def verify(self, access_token: str) -> AuthenticatedUser:
        request = Request(
            f"{self._settings.url}/auth/v1/user",
            headers={
                "apikey": self._settings.publishable_key,
                "Authorization": f"Bearer {access_token}",
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.load(response)
        except HTTPError as error:
            if error.code in {400, 401, 403}:
                raise AuthenticationRequiredError("로그인이 필요합니다.") from error
            raise AuthenticationServiceUnavailableError(
                "인증 서버 응답을 확인할 수 없습니다."
            ) from error
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise AuthenticationServiceUnavailableError(
                "인증 서버에 연결할 수 없습니다."
            ) from error

        try:
            user_id = UUID(str(payload["id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise AuthenticationRequiredError(
                "사용자 정보가 올바르지 않습니다."
            ) from error
        email = payload.get("email")
        return AuthenticatedUser(
            user_id=user_id,
            email=email if isinstance(email, str) else None,
        )


class UnconfiguredAuthVerifier:
    def verify(self, _access_token: str) -> AuthenticatedUser:
        raise AuthenticationServiceUnavailableError(
            "서버의 Supabase 인증 설정이 필요합니다."
        )


def create_auth_verifier_from_env() -> AuthVerifier:
    settings = SupabaseAuthSettings.from_env()
    return (
        SupabaseAuthVerifier(settings)
        if settings is not None
        else UnconfiguredAuthVerifier()
    )
