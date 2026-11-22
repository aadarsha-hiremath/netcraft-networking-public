import csv
import io
import json
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from os import environ
from pathlib import Path

import dns.message
import dns.rcode
import dns.rrset
import pytest

from netcraft.cli import monitoring as cli_monitoring
from netcraft.cli.main import main
from netcraft.core.errors import NetworkFailure
from netcraft.diagnostic_models import RecordType
from netcraft.diagnostics.lab import echo_server
from netcraft.models import ErrorCategory, Peer, Protocol
from netcraft.monitoring.history import open_history
from netcraft.monitoring.probes import ProbeOptions, Target, socket_target
from netcraft.monitoring.scheduler import MonitorConfig, monitor
from netcraft.monitoring.telemetry import MonitorProtocol, RunStatus, Sample, SampleStatus
from netcraft.protocols.dns import inspect_dns
from tests import protocol_fixtures
from tests.protocol_fixtures import (
    TCPHandler,
    dns_fixture,
    fixed_http,
    http_request,
    server_tls,
    tcp_fixture,
)

_REPLY = (
    b"HTTP/1.1 404 Test\r\nSet-Cookie: session=volatile-cookie\r\n"
    b"Content-Type: text/plain\r\nContent-Length: 16\r\n\r\nvolatile-content"
)


def _dns_answer(query: dns.message.QueryMessage, _tcp: bool) -> bytes:
    reply = dns.message.make_response(query)
    reply.answer.append(dns.rrset.from_text("monitor.example.test.", 60, "IN", "A", "192.0.2.1"))
    return reply.to_wire()


def test_dns_fixture_retries_a_shared_port_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as competitor:
        competitor.bind(("127.0.0.1", 0))
        occupied_port = competitor.getsockname()[1]
        attempts = 0

        @contextmanager
        def competing_tcp(handler: TCPHandler, *, host: str) -> Iterator[Peer]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                yield Peer(host, occupied_port)
            else:
                with tcp_fixture(handler, host=host) as peer:
                    yield peer

        monkeypatch.setattr(protocol_fixtures, "tcp_fixture", competing_tcp)
        with dns_fixture(_dns_answer) as resolver:
            result = inspect_dns(
                "monitor.example.test",
                resolver=resolver.address,
                port=resolver.port,
            )
    assert attempts >= 2
    assert result.success and result.records[0].data == "192.0.2.1"


def test_all_monitoring_adapters_and_privacy(tmp_path: Path) -> None:
    context, ca = server_tls("127.0.0.1")
    ca_file = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(ca_file)
    requests: list[bytes] = []

    def endpoint(conn: socket.socket) -> None:
        requests.append(http_request(conn))
        conn.sendall(_REPLY)

    path = tmp_path / "adapters.sqlite3"
    with (
        echo_server(Protocol.TCP) as tcp,
        echo_server(Protocol.UDP) as udp,
        dns_fixture(_dns_answer) as dns,
        tcp_fixture(endpoint) as http,
        tcp_fixture(fixed_http(_REPLY), tls=context) as https,
        open_history(path) as history,
    ):
        targets = (
            Target(MonitorProtocol.TCP, tcp.address, tcp.port),
            Target(MonitorProtocol.UDP, udp.address, udp.port),
            Target(MonitorProtocol.DNS, "Monitor.Example.Test", record_type=RecordType.A),
            Target(
                MonitorProtocol.HTTP,
                f"http://{http.address}:{http.port}/volatile-path?auth=volatile-query#fragment",
            ),
            Target(MonitorProtocol.HTTPS, f"https://{https.address}:{https.port}/"),
        )
        assert "volatile-query" not in repr(targets)
        run = monitor(
            history,
            targets,
            MonitorConfig(samples=1, workers=5),
            options=ProbeOptions(resolver=dns.address, resolver_port=dns.port, ca_file=ca_file),
        )
        samples = {row.protocol: row for row in history.samples()}
    assert run.status is RunStatus.COMPLETED
    assert len(samples) == 5
    assert all(row.observation.status is SampleStatus.SUCCESS for row in samples.values())
    assert requests[0].startswith(b"HEAD /volatile-path?auth=volatile-query HTTP/1.1\r\n")
    assert samples[MonitorProtocol.TCP].observation.tcp_ns is not None
    assert samples[MonitorProtocol.UDP].observation.udp_exchange_ns is not None
    assert samples[MonitorProtocol.DNS].target == "A monitor.example.test."
    assert samples[MonitorProtocol.DNS].observation.dns_record_count == 1
    assert samples[MonitorProtocol.HTTP].target == f"http://{http.address}:{http.port}"
    assert samples[MonitorProtocol.HTTP].observation.http_status == 404
    assert samples[MonitorProtocol.HTTP].observation.body_read_ns is None
    assert samples[MonitorProtocol.HTTPS].observation.tls_verified is True
    assert samples[MonitorProtocol.HTTPS].observation.tls_ns is not None
    assert b"volatile" not in path.read_bytes()
    assert "volatile" not in json.dumps([item.to_dict() for item in samples.values()])


def test_real_timeout_and_dns_failure_reach_history(tmp_path: Path) -> None:
    def nxdomain(query: dns.message.QueryMessage, _tcp: bool) -> bytes:
        reply = dns.message.make_response(query)
        reply.set_rcode(dns.rcode.NXDOMAIN)
        return reply.to_wire()

    with (
        dns_fixture(nxdomain) as resolver,
        echo_server(Protocol.UDP, reply=False) as udp,
        open_history(tmp_path / "network.sqlite3") as history,
    ):
        monitor(
            history,
            (
                Target(MonitorProtocol.UDP, udp.address, udp.port),
                Target(MonitorProtocol.DNS, "missing.example.test"),
            ),
            MonitorConfig(samples=1),
            options=ProbeOptions(
                timeout=0.20, resolver=resolver.address, resolver_port=resolver.port
            ),
        )
        observations = {item.protocol: item.observation for item in history.samples()}
    resolution = observations[MonitorProtocol.DNS]
    timeout = observations[MonitorProtocol.UDP]
    assert resolution.status is SampleStatus.FAILURE
    assert resolution.error_category is ErrorCategory.DNS_NXDOMAIN
    assert (
        timeout.status is SampleStatus.TIMEOUT and timeout.error_category is ErrorCategory.TIMEOUT
    )
    assert timeout.latency_ns is not None and timeout.udp_exchange_ns is None


def test_validation_normalizes_origins_and_rejects_credentials(tmp_path: Path) -> None:
    first = Target(MonitorProtocol.HTTP, "http://EXAMPLE.test/path")
    second = Target(MonitorProtocol.HTTP, "http://example.test:80/another?query=value")
    assert first.label == second.label == "http://example.test:80"
    with pytest.raises(NetworkFailure, match="no credentials"):
        Target(MonitorProtocol.HTTP, "http://name:password@example.test/")
    with pytest.raises(ValueError, match="authority"):
        socket_target(MonitorProtocol.TCP, "::1:1000")
    assert socket_target(MonitorProtocol.TCP, "[0:0::1]:1000").label == "[::1]:1000"
    with open_history(tmp_path / "duplicate.db") as history:
        with pytest.raises(ValueError, match="only once"):
            monitor(history, (first, second), MonitorConfig(samples=1))
        assert list(history.runs(10)) == []


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "netcraft", *args],
        capture_output=True,
        check=False,
        encoding="utf-8",
        timeout=10,
        env=dict(environ) | {"PYTHONUTF8": "1"},
    )


def test_cli_monitor_history_summary_and_export_on_windows_path(tmp_path: Path) -> None:
    db = ("--db", str(tmp_path / "history with spaces δ.sqlite3"))
    with echo_server(Protocol.TCP) as tcp:
        monitored = _cli(
            "monitor",
            *db,
            "--tcp",
            f"{tcp.address}:{tcp.port}",
            "--samples",
            "2",
            "--interval",
            "0.1",
            "--json",
        )
    assert monitored.returncode == 0, monitored.stderr
    assert "Slot 1 tcp" in monitored.stderr and "Slot 2 tcp" in monitored.stderr
    finished = json.loads(monitored.stdout)
    run_id = finished["run"]["run_id"]
    assert finished["summary"]["successes"] == 2

    history = _cli("history", *db, "--run", run_id, "--limit", "1", "--json")
    assert history.returncode == 0
    assert json.loads(history.stdout)["rows"][0]["slot"] == 1
    runs = _cli("history", *db, "--runs", "--json")
    assert json.loads(runs.stdout)["rows"][0]["run_id"] == run_id
    summary = _cli("summary", *db, "--protocol", "tcp", "--group", "target", "--json")
    (row,) = json.loads(summary.stdout)["rows"]
    assert summary.returncode == 0 and row["attempts"] == 2 and row["timing_count"] == 2
    export = _cli("export", *db, "--report", "trend", "--format", "csv")
    trend = list(csv.DictReader(io.StringIO(export.stdout)))
    assert export.returncode == 0 and len(trend) == 2
    assert all(item["latency_ns"].isdigit() for item in trend)


def test_cli_input_errors_and_safe_deletion(tmp_path: Path) -> None:
    db = ("--db", str(tmp_path / "cli.sqlite3"))
    invalid = _cli(
        "monitor", *db, "--http", "https://name:private-word@127.0.0.1/", "--samples", "1"
    )
    assert invalid.returncode == 2 and "private-word" not in invalid.stderr + invalid.stdout
    window = _cli("export", *db, "--since", "2026-10-01")
    assert window.returncode == 2 and not window.stdout
    deleted = _cli("history", *db, "--delete-run", "missing", "--json")
    assert deleted.returncode == 1 and json.loads(deleted.stdout) == {"deleted": False}


def test_cli_keyboard_interrupt_keeps_completed_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupted(_sample: Sample) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_monitoring, "_progress", interrupted)
    with echo_server(Protocol.TCP) as tcp:
        result = main(
            [
                "monitor",
                "--db",
                str(tmp_path / "interrupt.sqlite3"),
                "--tcp",
                f"{tcp.address}:{tcp.port}",
                "--samples",
                "10",
                "--json",
            ]
        )
    document = json.loads(capsys.readouterr().out)
    assert result == 130
    assert document["run"]["status"] == "cancelled" and document["summary"]["successes"] == 1
