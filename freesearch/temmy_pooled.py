"""Connection-pooled Temmy client for the free search.

Why this exists
---------------
The shared `TemmyClient._request` calls `requests.request(...)`, which opens a
fresh TCP+TLS connection for *every* call. The free search fans out many small
detail fetches, so those per-call TLS handshakes to Cloud Run dominated the
latency (~8s). Reusing one keep-alive `requests.Session` across all of a
request's calls collapses that to a couple of seconds.

This is a thin subclass so we do NOT touch `deploy-v2-hotfix/temmy.py`, which
the paid audit + forensic layers also depend on. Same interface, same retry
behaviour — only the transport is pooled. The free-search service is handed one
of these by `api._make_client`; nothing else changes.

Query Runs note (09 Jul 2026): the ideal retriever is a single Query Runs SQL
query, but `/api/v2/query-runs` is not enabled on the current deployment for
these credentials (returns a custom 404 for every auth variant while the
standard search endpoint returns 200). Once Temmy grants Query Runs, the
retriever moves to SQL and this pooling stops mattering. Until then, pooling is
what keeps the REST path fast.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import requests

_DEPLOY = Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'
if str(_DEPLOY) not in sys.path:
    sys.path.insert(0, str(_DEPLOY))

from temmy import TemmyClient, TemmyError  # type: ignore  # noqa: E402


class PooledTemmyClient(TemmyClient):
    """TemmyClient that reuses a single keep-alive Session for all calls.

    Overrides only `_request`; every public method (`search_trademarks`,
    `get_trademark`, …) inherits unchanged and gains pooling for free.

    Thread-safety: `requests.Session` is safe for concurrent GETs across
    threads when each call gets its own Response (which it does here). The
    free-search service fans detail fetches across a small thread pool, all
    sharing this one connection pool — exactly what we want.
    """

    def __init__(self, *args, pool_maxsize: int = 20, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=pool_maxsize,
            pool_maxsize=pool_maxsize,
            max_retries=0,          # we do our own retry/backoff below
        )
        self._session.mount('https://', adapter)
        self._session.mount('http://', adapter)

    def _request(self, method: str, path: str, *,
                 params: dict | None = None,
                 json: Any = None,
                 accept_404_as_none: bool = False) -> dict | None:
        """Same retry/backoff contract as TemmyClient._request, but over the
        pooled Session instead of module-level requests."""
        url = f'{self._base_url}{path}'
        last_err: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            self._sleep_for_rate_limit()
            try:
                resp = self._session.request(
                    method, url, headers=self._headers(),
                    params=params or None, json=json, timeout=self._timeout)
            except requests.RequestException as exc:
                last_err = exc
                time.sleep(min(2 ** attempt, 8))
                continue
            finally:
                self._last_request_at = time.time()

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise TemmyError(
                        f'Temmy returned non-JSON 200 response: {exc}') from exc
            if resp.status_code == 404 and accept_404_as_none:
                return None
            if resp.status_code == 429:
                retry_after = float(resp.headers.get('Retry-After', '')
                                    or 2 ** attempt)
                time.sleep(min(retry_after, 10))
                last_err = TemmyError(f'Rate limited (429); attempt {attempt}')
                continue
            if 500 <= resp.status_code < 600:
                last_err = TemmyError(f'Temmy server error {resp.status_code}')
                time.sleep(min(2 ** attempt, 8))
                continue
            body = (resp.text or '')[:400]
            raise TemmyError(f'Temmy {resp.status_code}: {body}')
        raise TemmyError(
            f'Temmy request failed after {self._max_retries} attempts: {last_err}')

    def close(self) -> None:
        self._session.close()
