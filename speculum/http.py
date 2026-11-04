"""Polite HTTP client: retries, per-host pacing, and every response archived before parsing."""
from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlencode, urlparse

import requests

from .archive import RawArchive

log = logging.getLogger("speculum.http")

USER_AGENT = "Speculum/0.1 (congressional accountability research; nonprofit project)"


class Fetcher:
    def __init__(self, archive: RawArchive, min_interval: dict[str, float] | None = None):
        self.archive = archive
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.min_interval = min_interval or {}
        self._last: dict[str, float] = {}
        self.calls = 0

    def _pace(self, host: str) -> None:
        gap = self.min_interval.get(host, 0.2)
        wait = self._last.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def get(self, source: str, name: str, url: str, params: dict | None = None,
            headers: dict | None = None, retries: int = 7) -> bytes:
        full = url + ("?" + urlencode(params, doseq=True) if params else "")
        host = urlparse(url).netloc
        delay = 2.0
        for attempt in range(1, retries + 1):
            self._pace(host)
            try:
                resp = self.session.get(full, headers=headers, timeout=60)
            except requests.RequestException as exc:
                log.warning("%s attempt %d failed: %s", host, attempt, exc)
                time.sleep(delay)
                delay *= 2
                continue
            self.calls += 1
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                pause = float(retry_after) if retry_after and retry_after.isdigit() else delay
                log.warning("%s returned %s; waiting %.0fs", host, resp.status_code, pause)
                time.sleep(min(pause, 120))
                delay *= 2
                continue
            if resp.status_code == 404:
                raise NotFound(full)
            resp.raise_for_status()
            self.archive.save(source, name, resp.content, full)
            return resp.content
        raise RuntimeError(f"gave up after {retries} attempts: {host}")

    def get_json(self, source: str, name: str, url: str, **kw):
        return json.loads(self.get(source, name, url, **kw))


class NotFound(Exception):
    pass
