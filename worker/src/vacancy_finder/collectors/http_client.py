"""The HTTP client every collector and checker uses: an honest User-Agent, and a polite
response to 429 Too Many Requests.

When a site answers 429, the request is sent again after a wait instead of failing the
run: the site's Retry-After (seconds or a date) when it gives one, otherwise 30s, 60s,
120s and 240s, never more than 5 minutes at a time. After four retries the 429 is
returned to the caller like any other error. Himalayas started answering 429 after about
26 requests in a minute, which failed its whole run.
"""

import logging
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "personal-vacancy-finder/0.1 (personal job search)"


def retry_wait(response: httpx.Response, attempt: int, backoff: float, cap: float) -> float:
    """Seconds to wait before retry number attempt + 1 after a 429."""
    wait = backoff * 2 ** attempt
    header = (response.headers.get("Retry-After") or "").strip()
    if header:
        try:
            wait = float(header)
        except ValueError:
            try:
                wait = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError):
                pass
    return min(max(wait, 1.0), cap)


class RateLimitRetry(httpx.BaseTransport):
    """Wraps a transport so that a 429 is waited out and the request sent again."""

    def __init__(self, transport: httpx.BaseTransport | None = None, max_retries: int = 4,
                 backoff_seconds: float = 30, max_wait_seconds: float = 300) -> None:
        self.transport = transport or httpx.HTTPTransport()
        self.max_retries = max_retries
        self.backoff = backoff_seconds
        self.cap = max_wait_seconds

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            response = self.transport.handle_request(request)
            if response.status_code != 429 or attempt == self.max_retries:
                return response
            wait = retry_wait(response, attempt, self.backoff, self.cap)
            response.close()
            log.warning("%s rate limited (429), waiting %.0fs before retry %d of %d",
                        request.url.host, wait, attempt + 1, self.max_retries)
            time.sleep(wait)
        raise AssertionError("unreachable")

    def close(self) -> None:
        self.transport.close()


def polite_client(timeout: float = 30, transport: httpx.BaseTransport | None = None,
                  **kwargs) -> httpx.Client:
    """A client with USER_AGENT, redirects followed and 429s retried after a wait."""
    return httpx.Client(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
        transport=RateLimitRetry(transport), **kwargs,
    )
