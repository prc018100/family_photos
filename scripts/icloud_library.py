#!/usr/bin/env python3
"""Create or reuse the iCloud external library and queue a scan.

Stdlib only, so the Fedora host and the sync image can both run it.
The API key is sent as a header and is never printed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

IMPORT_PATH = os.environ.get("ICLOUD_IMPORT_PATH", "/mnt/media/icloud")
LIBRARY_NAME = os.environ.get("ICLOUD_LIBRARY_NAME", "iCloud")

AUTH_MARKERS = (
    "two-factor authentication",
    "two-step authentication",
    "failed to login",
    "invalid email/password",
    "pyicloudfailedlogin",
    "pyicloudfailedmfa",
    "authentication is required",
    "eof when reading a line",
    "eoferror",
)


class ImmichLibraryError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def reauth_message(username: str) -> str:
    who = username.strip() or "you@icloud.com"
    return (
        "Apple iCloud session expired or was rejected.\n"
        "Photos are not downloading. Immich will not see new iCloud photos until you re-authenticate.\n"
        "Run this on the server:\n"
        f"  ./import-icloud.sh {who}\n"
    )


def session_needs_reauth(log: str, exit_code: int) -> bool:
    if exit_code == 0:
        return False
    lowered = log.lower()
    return any(marker in lowered for marker in AUTH_MARKERS)


def _request(method: str, url: str, api_key: str, body: dict | None, timeout: float) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def ensure_icloud_library(
    base_url: str,
    api_key: str,
    *,
    import_path: str = IMPORT_PATH,
    name: str = LIBRARY_NAME,
    timeout: float = 60,
    transport=None,
) -> dict:
    """Find or create the library, then queue a scan. Returns the library id."""

    if not api_key:
        raise ImmichLibraryError("IMMICH_API_KEY is empty")
    base = base_url.rstrip("/")
    send = transport or (lambda method, path, body: _request(method, base + path, api_key, body, timeout))

    me = _json(send("GET", "/api/users/me", None), "current user")
    if not isinstance(me, dict) or not me.get("id"):
        raise ImmichLibraryError("Immich did not return the current user id")
    if me.get("isAdmin") is False:
        raise ImmichLibraryError("This API key is not an admin. Library import requires the admin user.")

    libraries = _json(send("GET", "/api/libraries", None), "libraries")
    if not isinstance(libraries, list):
        raise ImmichLibraryError("Immich did not return a library list")

    chosen = next((item for item in libraries if import_path in (item.get("importPaths") or [])), None)
    named = next((item for item in libraries if item.get("name") == name), None)
    action = "scanned"

    if chosen is None and named is None:
        created = _json(
            send(
                "POST",
                "/api/libraries",
                {"ownerId": me["id"], "name": name, "importPaths": [import_path]},
            ),
            "create library",
        )
        library_id = created["id"]
        action = "created"
    else:
        library = chosen or named
        library_id = library["id"]
        paths = list(library.get("importPaths") or [])
        if import_path not in paths:
            paths.append(import_path)
            _json(send("PUT", f"/api/libraries/{library_id}", {"importPaths": paths}), "update library")
            action = "updated"

    _json(send("POST", f"/api/libraries/{library_id}/scan", None), "scan library", allow_empty=True)
    return {"id": library_id, "action": action, "importPath": import_path}


def _json(result: tuple[int, bytes], what: str, allow_empty: bool = False):
    status, payload = result
    if status == 204 or (allow_empty and status < 300 and not payload):
        return None
    if status >= 400:
        detail = payload.decode("utf-8", "replace")[:300]
        if status in {401, 403}:
            raise ImmichLibraryError(
                f"Immich refused the API key while trying to {what} ({status}). "
                "The key's user must be the admin and include user.read, library.read, "
                f"library.create, and library.update. {detail}",
                status,
            )
        raise ImmichLibraryError(f"Immich failed to {what} ({status}). {detail}", status)
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ImmichLibraryError(f"Immich returned unexpected data while trying to {what}") from exc


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--reauth-message":
        sys.stdout.write(reauth_message(os.environ.get("ICLOUD_USERNAME", "")))
        return 0
    if argv and argv[0] == "--session-expired":
        log = open(argv[1], encoding="utf-8", errors="replace").read()
        code = int(argv[2])
        return 0 if session_needs_reauth(log, code) else 1
    base = os.environ.get("IMMICH_URL", "http://127.0.0.1:2283")
    try:
        result = ensure_icloud_library(base, os.environ.get("IMMICH_API_KEY", ""))
    except ImmichLibraryError as exc:
        print(exc, file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Could not reach Immich at {base}: {exc}", file=sys.stderr)
        return 1
    print(f"Immich library {result['id']} {result['action']}; scan queued for {result['importPath']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
