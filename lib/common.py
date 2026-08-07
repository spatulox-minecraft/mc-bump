"""Errors and HTTP, shared by every other module."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 30


class Failure(Exception):
    """Expected error, printed cleanly without a traceback."""


def user_agent() -> str:
    """Identify the caller to the upstream APIs.

    Derived from the repository rather than hardcoded: mc-bump runs on any mod,
    and a shared user agent would make an abusive repo look like all the others
    to Mojang, Fabric and Modrinth.
    """
    repo = os.environ.get("GITHUB_REPOSITORY") or "local"
    return f"mc-bump ({repo})"


def http_get(
    url: str,
    params: dict[str, str] | None = None,
    allow_status: tuple[int, ...] = (),
) -> str:
    """GET a document as text.

    Any HTTP error raises, EXCEPT the statuses listed in allow_status, whose body
    is returned instead. Without that distinction a 5xx from an upstream API
    would be indistinguishable from a legitimate "not published yet" answer, and
    the workflow would silently stop updating while staying green.
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code not in allow_status:
            raise Failure(f"HTTP {exc.code} from {url}") from exc
        return exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise Failure(f"cannot reach {url}: {exc.reason}") from exc


def get_json(
    url: str,
    params: dict[str, str] | None = None,
    allow_status: tuple[int, ...] = (),
):
    body = http_get(url, params, allow_status)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise Failure(f"invalid JSON from {url}") from exc
