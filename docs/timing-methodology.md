# Phase 2 timing and reproduction

Durations are integer nanoseconds from `time.perf_counter_ns()`. UTC timestamps provide wall-clock
context. Total duration is measured directly from entry through cleanup, including validation
and local setup. It is independent of individual phase values.

| Field | Measurement boundary |
| --- | --- |
| DNS inspection `duration_ns` | Whole explicit record diagnostic, including resolver configuration, attempts, and wire parsing. |
| HTTP `dns_ns` | One successful system `getaddrinfo()` lookup for the URL host. It can include local configuration, cache, hosts file, or DNS behavior. It is null for IP literals or failed lookup. |
| `tcp_connect_ns` | From immediately before the successful address's socket connect to confirmed handshake completion. |
| `attempts[].duration_ns` | Elapsed time of that candidate attempt, including socket setup and failed socket cleanup where applicable. |
| `tls_handshake_ns` | From the first handshake call through completed and verified negotiation. Trust-store setup and metadata extraction are outside this field. |
| `request_send_ns` | From immediately before writing the prepared HTTP request until the last byte is accepted by the socket or TLS layer. |
| `ttfb_ns` | From the end of the request write to the first response byte observed by the reader, including an informational response if received first. |
| `headers_read_ns` | From the same end-of-write boundary through parsing the final response headers, including the first-byte wait. |
| `body_read_ns` | Time spent consuming the permitted body after headers. Near-zero values are possible for bytes already buffered. |

Stage times are null when skipped or when the corresponding boundary was never reached. Thus
a failed TLS handshake has no handshake duration, while a later HTTP parse failure can still
have a first-byte measurement. Timings are observations at the client and do not measure
ICMP round-trip latency. TTFB and headers time overlap.

HTTP uses one operating-system hostname lookup and attempts up to eight distinct IPv4 or IPv6
endpoints in resolver order. It tries addresses sequentially under the same connect deadline
and retains the original host in the HTTP authority and TLS identity. The overall timeout
defaults to 10 seconds. TCP connect, TLS setup and handshake, HTTP write, and HTTP read each
have a separate stage budget clipped by the overall deadline. The HTTP read budget includes
first-byte wait, headers, and the permitted body. The write and read budgets both use the
read-timeout setting. A pending socket wait or TLS operation wakes on cancellation.

The operating system's blocking hostname lookup remains a cancellation boundary. There is
no second resolution call and no detached resolver worker. If the lookup returns after the
overall deadline, the inspector reports the timeout before opening a connection. Local trust
loading also runs synchronously, with the socket deadline checked before the first handshake
call. Timer granularity and thread scheduling can make observed elapsed time exceed a deadline.

From the repository root after installing the development extras, run the offline test and
benchmark commands below.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m benchmarks.protocol_diagnostics --iterations 100
```

The benchmark generates its own ephemeral test CA and runs sequential DNS, HTTP, and HTTPS
requests against loopback fixtures. After five warm-ups per protocol it reports sample count,
median, and nearest-rank 95th percentile. The HTTPS total includes fresh TLS context and trust
setup. TCP and TLS handshake fields isolate their respective phases. Ten silent DNS requests
exercise a configured 20 ms timeout. The script checks that all fixture workers are gone at
exit. Results are useful for local execution and reproducibility, with timing affected by
host load, OS scheduling, and Python/OpenSSL versions.
