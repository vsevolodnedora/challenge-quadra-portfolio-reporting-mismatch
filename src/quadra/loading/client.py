"""HTTP client for the QUADRA API.

Robustness here (rate-limit pacing, retry on transient failures, an explicit error
taxonomy) is about extracting *completely and correctly* — it is not defensive data
handling. Response bodies are returned untouched to the caller for verbatim persistence.
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("quadra.client")

# 120 req/min/key. Pace a little under that so the header-driven wait is only a backstop.
_DEFAULT_MIN_INTERVAL = 60.0 / 110.0


class QuadraError(Exception):
    """Base error for the loader."""


class QuadraAuthError(QuadraError):
    """401 — missing/unknown API key. Aborts the run (a credential problem, not data)."""


class QuadraParamError(QuadraError):
    """400 — invalid parameter. Aborts the affected surface; the call is deterministic."""

    def __init__(self, param: object, message: object):
        self.param = param
        self.message = message
        super().__init__(f"400 invalid parameter param={param!r}: {message!r}")


class QuadraClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        min_interval: float = _DEFAULT_MIN_INTERVAL,
        max_retries: int = 5,
        timeout: float = 60.0,
        ratelimit_floor: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.ratelimit_floor = ratelimit_floor
        self.session = requests.Session()
        self.session.headers["X-API-Key"] = api_key  # the only place the key is used
        self._last_request = 0.0
        self.request_count = 0

    # -- pacing / rate-limit ------------------------------------------------
    def _pace(self) -> None:
        gap = time.monotonic() - self._last_request
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)

    def _respect_ratelimit(self, resp: requests.Response) -> None:
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")
        try:
            if remaining is not None and int(remaining) <= self.ratelimit_floor and reset:
                wait = max(0, int(reset) - int(time.time())) + 1
                log.warning("rate-limit floor (remaining=%s); sleeping %ds", remaining, wait)
                time.sleep(wait)
        except (TypeError, ValueError):
            pass  # headers absent/odd — the pacing gate still protects us

    def _wait_for_429(self, resp: requests.Response) -> None:
        retry_after = resp.headers.get("Retry-After")
        reset = resp.headers.get("X-RateLimit-Reset")
        if retry_after is not None:
            try:
                wait = int(float(retry_after))
            except ValueError:
                wait = 5
        elif reset:
            wait = max(0, int(reset) - int(time.time())) + 1
        else:
            wait = 5
        log.warning("429 rate limited; sleeping %ds before retry", wait)
        time.sleep(wait)

    # -- the one entry point ------------------------------------------------
    def get(self, path: str, params: dict) -> requests.Response:
        """GET a path, returning the raw Response on 200. Handles retries and the error
        taxonomy. Raises QuadraAuthError/QuadraParamError/QuadraError on terminal failures."""
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            self._pace()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise QuadraError(f"network error after {attempt} attempts: {exc}") from exc
                backoff = 2 ** (attempt - 1)
                log.warning("network error (%s); backoff %ds (attempt %d)", exc, backoff, attempt)
                time.sleep(backoff)
                continue
            finally:
                self._last_request = time.monotonic()

            self.request_count += 1
            status = resp.status_code

            if status == 200:
                self._respect_ratelimit(resp)
                return resp
            if status == 429:
                self._wait_for_429(resp)
                continue
            if 500 <= status < 600:
                attempt += 1
                if attempt > self.max_retries:
                    raise QuadraError(f"{status} server error after {attempt} attempts: {resp.text[:200]}")
                backoff = 2 ** (attempt - 1)
                log.warning("%d server error; backoff %ds (attempt %d)", status, backoff, attempt)
                time.sleep(backoff)
                continue

            # deterministic 4xx (not 429) — do not retry
            if status == 401:
                raise QuadraAuthError("401 unauthorized — check QUADRA_API_KEY")
            if status == 400:
                param, message = self._parse_error(resp)
                raise QuadraParamError(param, message)
            raise QuadraError(f"unexpected HTTP {status} for {path}: {resp.text[:200]}")

    @staticmethod
    def _parse_error(resp: requests.Response) -> tuple[object, object]:
        """400's `error` is an object {param,message}; 401's is a bare string. Handle both."""
        try:
            err = resp.json().get("error")
        except ValueError:
            return None, resp.text[:200]
        if isinstance(err, dict):
            return err.get("param"), err.get("message")
        return None, err
