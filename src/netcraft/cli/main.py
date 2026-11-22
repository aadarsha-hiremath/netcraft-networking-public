"""Command-line entry point for NetCraft diagnostics."""

import argparse
import json
import sys
from dataclasses import replace
from typing import cast

from netcraft.cli.inspection import configure_dns, configure_http, inspect_command
from netcraft.cli.monitoring import (
    configure_export,
    configure_history,
    configure_monitor,
    configure_summary,
    monitoring_command,
)
from netcraft.core.config import DEFAULT_TIMEOUT, MAX_ECHO_PAYLOAD, validate_timeout
from netcraft.core.errors import NetworkFailure, classify_socket_error
from netcraft.diagnostics.echo import echo_tcp
from netcraft.diagnostics.lab import echo_server
from netcraft.models import ErrorCategory, NetworkError, NetworkResult, Protocol
from netcraft.protocols.tcp import connect_tcp
from netcraft.protocols.udp import exchange_datagram


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="netcraft", description="Networking diagnostics and lab")
    commands = parser.add_subparsers(dest="command", required=True)
    tcp = commands.add_parser("tcp-connect", help="Try a TCP handshake with one host and port.")
    tcp.add_argument("host")
    tcp.add_argument("port", type=int)
    tcp_echo = commands.add_parser("tcp-echo", help="Run a localhost TCP echo demo.")
    udp_echo = commands.add_parser("udp-echo", help="Run a localhost UDP echo demo.")
    configure_dns(commands.add_parser("dns", help="Query one DNS record type."))
    configure_http(commands.add_parser("http", help="Inspect one HTTP or HTTPS response."))
    configure_monitor(
        commands.add_parser("monitor", help="Run finite monitoring against explicit targets.")
    )
    configure_history(
        commands.add_parser("history", help="Read or maintain local monitoring history.")
    )
    configure_summary(
        commands.add_parser("summary", help="Report reliability and latency statistics.")
    )
    configure_export(
        commands.add_parser("export", help="Stream monitoring reports as CSV or JSON.")
    )
    for command in (tcp_echo, udp_echo):
        command.add_argument("message", nargs="?", default="NetCraft", help="UTF-8 message")
        command.add_argument("--ipv6", action="store_true", help="Use the IPv6 loopback address.")
    for command in (tcp, tcp_echo, udp_echo):
        command.add_argument(
            "--timeout", type=float, default=DEFAULT_TIMEOUT, help="Socket timeout in seconds."
        )
        command.add_argument(
            "--json", action="store_true", help="Emit a structured result as JSON."
        )
    return parser


def _echo(protocol: Protocol, host: str, payload: bytes, timeout: float) -> NetworkResult:
    with echo_server(protocol, host=host) as peer:
        if protocol is Protocol.TCP:
            return echo_tcp(peer.address, peer.port, payload, timeout=timeout)
        result = exchange_datagram(peer.address, peer.port, payload, timeout=timeout)
        if result.details is not None and result.details.response != payload:
            return replace(
                result,
                details=None,
                error=NetworkError(
                    ErrorCategory.UNEXPECTED_RESPONSE, "receive", "Echo differed from the request."
                ),
            )
        return result


def _report(result: NetworkResult, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result.to_dict()))
    else:
        print(f"{result.operation} {result.target} port {result.port}")
        if result.error is not None:
            error = result.error
            print(f"Failed ({error.category}, {error.stage}) — {error.message}")
        elif result.details is not None:
            details = result.details
            print(f"Success in {result.duration_ns / 1_000_000:.3f} ms")
            if details.connect_ns is not None:
                print(f"TCP connect: {details.connect_ns / 1_000_000:.3f} ms")
            if details.exchange_ns is not None:
                print(f"Echo exchange: {details.exchange_ns / 1_000_000:.3f} ms")
            if details.response is not None:
                response = details.response.decode("utf-8", errors="replace")
                print(f"Received {details.bytes_received} bytes: {response!r}")
    if result.error is None:
        return 0
    return 2 if result.error.stage == "validation" else 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"monitor", "history", "summary", "export"}:
            return monitoring_command(args)
        timeout = cast(float, args.timeout)
        if args.command in {"dns", "http"}:
            return inspect_command(args)
        if args.command == "tcp-connect":
            result = connect_tcp(args.host, args.port, timeout=timeout)
        else:
            validate_timeout(timeout)
            payload = cast(str, args.message).encode("utf-8")
            if len(payload) > MAX_ECHO_PAYLOAD:
                parser.error(f"Echo messages are limited to {MAX_ECHO_PAYLOAD} UTF-8 bytes.")
            host = "::1" if args.ipv6 else "127.0.0.1"
            protocol = Protocol.TCP if args.command == "tcp-echo" else Protocol.UDP
            result = _echo(protocol, host, payload, timeout)
    except (NetworkFailure, UnicodeError) as exc:
        parser.error(str(exc))
    except OSError as exc:
        error = classify_socket_error(exc, "laboratory")
        print(f"Laboratory failed ({error.category}) — {error.message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    return _report(result, args.json)
