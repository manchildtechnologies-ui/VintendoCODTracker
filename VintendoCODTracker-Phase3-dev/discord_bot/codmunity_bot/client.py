"""Conservative async wrapper around the observed read-only endpoint."""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .decoder import extract_result

ENDPOINT = "https://codmunity.gg/_serverFn/9000fe5e5cae7ca2ad1efe30cc6cf4a20c58d0632b8cdd371a024e80356a1a0e"


class CODMunityError(RuntimeError):
    pass


class RateLimited(CODMunityError):
    pass


@dataclass
class _CacheEntry:
    expires: float
    data: dict[str, Any]


class CODMunityClient:
    def __init__(self, endpoint: str = ENDPOINT, timeout: float = 12, cache_ttl: float = 60, min_interval: float = 2):
        self.endpoint, self.timeout, self.cache_ttl, self.min_interval = endpoint, timeout, cache_ttl, min_interval
        self._cache: dict[str, _CacheEntry] = {}
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def payload(uid: str) -> dict[str, Any]:
        return {"t": 10, "i": 0, "p": {"k": ["data"], "v": [{"t": 10, "i": 1, "p": {"k": ["uid"], "v": [{"t": 1, "s": uid}]}, "o": 0}]}, "f": 127, "m": []}

    def _fetch(self, uid: str) -> dict[str, Any]:
        body = json.dumps(self.payload(uid), separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "VintendoCODBot/0.1 (read-only client)",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status == 429:
                    raise RateLimited("CODMunity asked the client to slow down")
                raw = response.read(2_000_000)
                if len(raw) >= 2_000_000:
                    raise CODMunityError("CODMunity response exceeded size limit")
                parsed = json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise RateLimited("CODMunity asked the client to slow down") from exc
            raise CODMunityError(f"CODMunity returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CODMunityError(f"CODMunity request failed: {type(exc).__name__}") from exc
        except json.JSONDecodeError as exc:
            raise CODMunityError("CODMunity returned invalid JSON") from exc
        return extract_result(parsed)

    async def get_player(self, uid: str) -> dict[str, Any]:
        uid = uid.strip()
        if not uid or len(uid) > 128:
            raise ValueError("UID must contain 1 to 128 characters")
        now = time.monotonic()
        hit = self._cache.get(uid)
        if hit and hit.expires > now:
            return hit.data
        async with self._lock:
            now = time.monotonic()
            hit = self._cache.get(uid)
            if hit and hit.expires > now:
                return hit.data
            delay = self.min_interval - (now - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()
            data = await asyncio.to_thread(self._fetch, uid)
            self._cache[uid] = _CacheEntry(time.monotonic() + self.cache_ttl, data)
            return data
