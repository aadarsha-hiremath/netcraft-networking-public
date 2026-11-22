import json
import socket
import subprocess
import sys
from os import environ
from pathlib import Path

import pytest

from netcraft.diagnostics.lab import echo_server
from netcraft.models import Protocol


def cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "netcraft", *arguments],
        capture_output=True,
        encoding="utf-8",
        timeout=10,
        check=False,
        env=dict(environ) | {"PYTHONUTF8": "1"},
    )


def test_help_and_unknown_command() -> None:
    help_result = cli("--help")
    assert help_result.returncode == 0
    assert all(command in help_result.stdout for command in ("tcp-connect", "tcp-echo", "udp-echo"))
    unknown = cli("unknown")
    assert unknown.returncode == 2
    assert "invalid choice" in unknown.stderr


@pytest.mark.parametrize("command", ["tcp-echo", "udp-echo"])
def test_echo_json(command: str) -> None:
    result = cli(command, "hello", "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["success"] is True
    assert data["details"]["response_base64"] == "aGVsbG8="
    assert data["details"]["bytes_received"] == 5


def test_tcp_connect_human_output() -> None:
    with echo_server(Protocol.TCP) as peer:
        result = cli("tcp-connect", peer.address, str(peer.port))
    assert result.returncode == 0, result.stderr
    assert "TCP connect:" in result.stdout
    assert "Echo exchange" not in result.stdout


def test_invalid_port_json_and_bad_timeout() -> None:
    result = cli("tcp-connect", "127.0.0.1", "0", "--json")
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["category"] == "invalid_port"
    result = cli("udp-echo", "--timeout", "nan")
    assert result.returncode == 2
    assert "Timeout must be" in result.stderr


def test_tcp_connect_failure_exit_code_and_json() -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        result = cli("tcp-connect", "127.0.0.1", str(port), "--timeout", "0.1", "--json")
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert data["success"] is False
    assert data["error"]["category"] in {"connection_refused", "timeout"}
    assert data["error"]["stage"] == "connect"


def test_import_from_outside_checkout_without_opening_sockets_or_threads(tmp_path: Path) -> None:
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import socket
import threading

def forbidden(*args, **kwargs):
    raise AssertionError("Import started a socket, lookup, or thread")

socket.socket = socket.getaddrinfo = threading.Thread.start = forbidden
import netcraft
import netcraft.cli.main
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert not process.stdout
