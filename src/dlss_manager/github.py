"""Shared GitHub HTTP access: turns network/HTTP failures into the caller's own
error type with a readable message, instead of a bare requests exception that
the GUI would silently swallow."""

from __future__ import annotations

import os
from datetime import datetime

import requests

API_PREFIX = "https://api.github.com/"
BASE_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "dlss-manager",
}


def _headers_for(url: str) -> dict[str, str]:
    headers = dict(BASE_HEADERS)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    # Only the API gets the token; release downloads redirect to other hosts.
    if token and url.startswith(API_PREFIX):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _describe(exc: requests.RequestException) -> str:
    resp = getattr(exc, "response", None)
    if resp is not None and resp.status_code in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
        reset = resp.headers.get("X-RateLimit-Reset")
        when = ""
        if reset and reset.isdigit():
            when = f" Сбросится около {datetime.fromtimestamp(int(reset)):%H:%M}."
        return (
            "Лимит запросов к GitHub API исчерпан (60 в час без токена)." + when +
            " Можно задать переменную окружения GITHUB_TOKEN, чтобы поднять лимит."
        )
    if resp is not None:
        return f"GitHub вернул HTTP {resp.status_code} для {resp.url}"
    return f"Не удалось связаться с GitHub: {exc}"


def get(url: str, error_cls: type[Exception], *, stream: bool = False, timeout: float = 20, params=None):
    try:
        resp = requests.get(url, headers=_headers_for(url), stream=stream, timeout=timeout, params=params)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise error_cls(_describe(e)) from e
    return resp
