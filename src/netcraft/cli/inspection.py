"""Argument and result rendering for Phase 2 commands."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from netcraft.core.config import DEFAULT_TIMEOUT
from netcraft.diagnostic_models import DNSResult, HTTPResult, RecordType


def configure_dns(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", help="Absolute DNS name, or an IP address for PTR.")
    parser.add_argument("--type", type=str.upper, choices=list(RecordType), default=RecordType.A)
    parser.add_argument("--resolver", help="Explicit DNS resolver IPv4 or IPv6 address.")
    parser.add_argument(
        "--resolver-port", type=int, help="Port for the explicit resolver (default 53)."
    )
    parser.add_argument("--timeout", type=float, default=2.0, help="Seconds per DNS resolver.")
    parser.add_argument(
        "--lifetime", type=float, default=5.0, help="Overall DNS lifetime in seconds."
    )
    parser.add_argument("--json", action="store_true", help="Emit a structured result as JSON.")


def configure_http(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("url", help="One absolute HTTP(S) URL.")
    parser.add_argument("--method", type=str.upper, choices=["GET", "HEAD"], default="GET")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="Default seconds per socket stage."
    )
    parser.add_argument("--connect-timeout", type=float, help="Override the TCP timeout.")
    parser.add_argument("--tls-timeout", type=float, help="Override the TLS timeout.")
    parser.add_argument(
        "--read-timeout", type=float, help="Override the HTTP send and read timeouts."
    )
    parser.add_argument(
        "--overall-timeout", type=float, default=10.0, help="Overall budget in seconds."
    )
    parser.add_argument(
        "--body-limit", type=int, help="Maximum body bytes to read (default 16384)."
    )
    parser.add_argument("--ca-file", type=Path, help="Add a trusted PEM CA file for HTTPS.")
    parser.add_argument("--json", action="store_true", help="Emit a structured result as JSON.")


def _render_dns(result: DNSResult) -> None:
    print(f"DNS {result.query_type} {result.query_name or '(invalid name)'} — {result.status}")
    if result.resolver_label is not None:
        print(f"Resolver {result.resolver_label}")
    if result.fallback_occurred:
        print("Resolver fallback occurred.")
    if result.response_code is not None:
        print(f"Response code {result.response_code}")
    for record in (*result.aliases, *result.records):
        print(f"{record.name} {record.ttl} IN {record.type} {record.data}")
    print(f"Elapsed {result.duration_ns / 1_000_000:.3f} ms")


def _render_http(result: HTTPResult) -> None:
    print(f"{result.method} {result.requested_url or '(invalid URL)'}")
    if result.response is not None:
        response = result.response
        print(f"HTTP/{response.version} {response.status} {response.reason!r}")
        for name, value in response.headers:
            print(f"{name} {value!r}")
        print(f"Read {response.body_bytes_read} body bytes (limit {response.body_limit})")
    if result.tls is not None:
        print(f"Verified {result.tls.version} using {result.tls.cipher}")
    for name, value in asdict(result.timings).items():
        if value is not None:
            print(f"{name.removesuffix('_ns').replace('_', ' ')} {value / 1_000_000:.3f} ms")
    print(f"Elapsed {result.duration_ns / 1_000_000:.3f} ms")


def inspect_command(args: argparse.Namespace) -> int:
    result: DNSResult | HTTPResult
    if args.command == "dns":
        from netcraft.protocols.dns import inspect_dns

        result = inspect_dns(
            args.name,
            args.type,
            resolver=args.resolver,
            port=args.resolver_port,
            timeout=args.timeout,
            lifetime=args.lifetime,
        )
    else:
        from netcraft.protocols.http import DEFAULT_BODY_LIMIT, HTTPTimeouts, inspect_http

        timeouts = HTTPTimeouts(
            connect=args.connect_timeout if args.connect_timeout is not None else args.timeout,
            tls=args.tls_timeout if args.tls_timeout is not None else args.timeout,
            read=args.read_timeout if args.read_timeout is not None else args.timeout,
            overall=args.overall_timeout,
        )
        result = inspect_http(
            args.url,
            method=args.method,
            timeouts=timeouts,
            ca_file=args.ca_file,
            body_limit=args.body_limit if args.body_limit is not None else DEFAULT_BODY_LIMIT,
        )
    if args.json:
        print(json.dumps(result.to_dict()))
    else:
        if isinstance(result, DNSResult):
            _render_dns(result)
        else:
            _render_http(result)
        if result.error is not None:
            print(
                f"Failed ({result.error.category}, {result.error.stage}) — {result.error.message}"
            )
    if result.error is None:
        return 0
    return 2 if result.error.stage == "validation" else 1
