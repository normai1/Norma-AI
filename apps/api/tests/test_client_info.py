import pytest
from fastapi import Request

from app.api.client_info import client_ip, client_user_agent
from app.core.config import settings

PEER = "10.0.0.9"


def _request(headers: dict[str, str] | None = None, peer: str | None = PEER) -> Request:
    encoded = [
        (key.lower().encode(), value.encode())
        for key, value in (headers or {}).items()
    ]

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": encoded,
            "client": (peer, 40000) if peer else None,
        }
    )


@pytest.fixture
def trusted_proxies(monkeypatch: pytest.MonkeyPatch):
    def _set(count: int) -> None:
        monkeypatch.setattr(settings, "trusted_proxy_count", count)

    return _set


def test_direct_deployment_ignores_forwarded_header(trusted_proxies) -> None:
    trusted_proxies(0)

    request = _request({"x-forwarded-for": "1.2.3.4"})

    assert client_ip(request) == PEER


def test_single_proxy_reads_the_forwarded_client(trusted_proxies) -> None:
    trusted_proxies(1)

    request = _request({"x-forwarded-for": "203.0.113.7"})

    assert client_ip(request) == "203.0.113.7"


def test_spoofed_prefix_cannot_change_the_result(trusted_proxies) -> None:
    trusted_proxies(1)

    # The caller sent "9.9.9.9"; the trusted proxy appended what it really saw.
    request = _request({"x-forwarded-for": "9.9.9.9, 203.0.113.7"})

    assert client_ip(request) == "203.0.113.7"


def test_two_proxies_skip_both_hops(trusted_proxies) -> None:
    trusted_proxies(2)

    request = _request({"x-forwarded-for": "203.0.113.7, 172.16.0.1"})

    assert client_ip(request) == "203.0.113.7"


def test_two_proxies_ignore_a_spoofed_prefix(trusted_proxies) -> None:
    trusted_proxies(2)

    request = _request({"x-forwarded-for": "9.9.9.9, 203.0.113.7, 172.16.0.1"})

    assert client_ip(request) == "203.0.113.7"


def test_missing_header_falls_back_to_the_peer(trusted_proxies) -> None:
    trusted_proxies(1)

    assert client_ip(_request()) == PEER


def test_too_few_hops_falls_back_to_the_peer(trusted_proxies) -> None:
    trusted_proxies(2)

    request = _request({"x-forwarded-for": "203.0.113.7"})

    assert client_ip(request) == PEER


def test_whitespace_and_empty_entries_are_ignored(trusted_proxies) -> None:
    trusted_proxies(1)

    request = _request({"x-forwarded-for": " 9.9.9.9 , , 203.0.113.7 "})

    assert client_ip(request) == "203.0.113.7"


def test_missing_peer_returns_none(trusted_proxies) -> None:
    trusted_proxies(0)

    assert client_ip(_request(peer=None)) is None


def test_user_agent_is_trimmed() -> None:
    request = _request({"user-agent": "a" * 900})

    assert len(client_user_agent(request)) == 512


def test_missing_user_agent_is_none() -> None:
    assert client_user_agent(_request()) is None
