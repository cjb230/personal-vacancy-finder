from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from vacancy_finder.collectors import http_client
from vacancy_finder.collectors.http_client import USER_AGENT, RateLimitRetry, polite_client


@pytest.fixture
def sleeps(monkeypatch):
    waits = []
    monkeypatch.setattr(http_client.time, "sleep", waits.append)
    return waits


def client_answering(responses, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(request)
        return responses.pop(0)

    return polite_client(transport=httpx.MockTransport(handler))


def test_retries_a_429_after_its_retry_after(sleeps):
    calls = []
    client = client_answering(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, text="ok")], calls)
    response = client.get("https://example.com/jobs?page=2")
    assert (response.status_code, response.text) == (200, "ok")
    assert sleeps == [7.0]
    assert [str(c.url) for c in calls] == ["https://example.com/jobs?page=2"] * 2
    assert calls[0].headers["User-Agent"] == USER_AGENT


def test_retry_after_as_a_date(sleeps):
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=90), usegmt=True)
    client = client_answering(
        [httpx.Response(429, headers={"Retry-After": when}), httpx.Response(200)])
    client.get("https://example.com/")
    assert 85 <= sleeps[0] <= 90


def test_backs_off_doubling_without_retry_after(sleeps):
    client = client_answering([httpx.Response(429)] * 3 + [httpx.Response(200)])
    assert client.get("https://example.com/").status_code == 200
    assert sleeps == [30.0, 60.0, 120.0]


def test_gives_up_after_four_retries_and_caps_the_wait(sleeps):
    calls = []
    client = client_answering([httpx.Response(429, headers={"Retry-After": "9999"})] * 5, calls)
    response = client.get("https://example.com/")
    assert response.status_code == 429
    assert len(calls) == 5
    assert sleeps == [300.0] * 4
    with pytest.raises(httpx.HTTPStatusError):
        response.raise_for_status()


def test_other_errors_are_not_retried(sleeps):
    calls = []
    client = client_answering([httpx.Response(503), httpx.Response(200)], calls)
    assert client.get("https://example.com/").status_code == 503
    assert (len(calls), sleeps) == (1, [])


def test_wraps_the_default_transport():
    assert isinstance(polite_client()._transport, RateLimitRetry)
