import json
import subprocess
import sys
from os import environ

import dns.message
import dns.rrset

from tests.protocol_fixtures import dns_fixture, fixed_http, tcp_fixture


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "netcraft", *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=10,
        env=dict(environ) | {"PYTHONUTF8": "1"},
    )


def test_dns_cli_explicit_resolver_and_json() -> None:
    def reply(query: dns.message.QueryMessage, tcp: bool) -> bytes:
        response = dns.message.make_response(query)
        response.answer.append(dns.rrset.from_text("lab.example.test.", 60, "IN", "A", "192.0.2.1"))
        return response.to_wire()

    with dns_fixture(reply) as peer:
        result = _run(
            "dns",
            "lab.example.test",
            "--type",
            "a",
            "--resolver",
            peer.address,
            "--resolver-port",
            str(peer.port),
            "--json",
        )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["status"] == "answer" and data["records"][0]["ttl"] == 60
    assert data["resolver"]["port"] == peer.port


def test_http_cli_keeps_404_a_success() -> None:
    with tcp_fixture(fixed_http(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")) as peer:
        result = _run("http", f"http://127.0.0.1:{peer.port}/", "--method", "head", "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["response"]["status"] == 404 and data["method"] == "HEAD"


def test_protocol_help_and_argument_errors() -> None:
    assert "--resolver" in _run("dns", "--help").stdout
    assert "--overall-timeout" in _run("http", "--help").stdout
    invalid = _run("http", "file:///a", "--json")
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["error"]["category"] == "invalid_url"


def test_dns_cli_failure_and_human_output() -> None:
    with dns_fixture(lambda _query, _tcp: b"incomplete message") as peer:
        result = _run(
            "dns",
            "lab.example.test",
            "--resolver",
            peer.address,
            "--resolver-port",
            str(peer.port),
        )
    assert result.returncode == 1
    assert "dns_malformed" in result.stdout
