"""Runtime settings. Everything sensitive comes from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _http_url(value: str) -> str:
    text = value.strip().rstrip("/")
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""


def _positive_int(value: str, default: int, *, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(upper, max(1, parsed))


@dataclass(frozen=True)
class Settings:
    immich_url: str
    immich_api_key: str
    immich_public_url: str
    basic_user: str = ""
    basic_password: str = ""
    page_size: int = 48
    timeout: float = 30.0

    @property
    def basic_auth_enabled(self) -> bool:
        return bool(self.basic_user and self.basic_password)

    @classmethod
    def from_env(cls) -> Settings:
        public = _http_url(os.environ.get("IMMICH_PUBLIC_URL", "http://127.0.0.1:2283"))
        internal = _http_url(os.environ.get("IMMICH_URL", "http://127.0.0.1:2283"))
        if not internal:
            internal = "http://127.0.0.1:2283"
        try:
            timeout = float(os.environ.get("TIMELINE_TIMEOUT", "30"))
        except ValueError:
            timeout = 30.0
        return cls(
            immich_url=internal,
            immich_api_key=os.environ.get("IMMICH_API_KEY", "").strip(),
            immich_public_url=public,
            basic_user=os.environ.get("TIMELINE_BASIC_AUTH_USER", ""),
            basic_password=os.environ.get("TIMELINE_BASIC_AUTH_PASSWORD", ""),
            page_size=_positive_int(os.environ.get("TIMELINE_PAGE_SIZE", "48"), 48, upper=100),
            timeout=timeout if timeout > 0 else 30.0,
        )
