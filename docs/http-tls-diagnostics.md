# HTTP and TLS diagnostics

`netcraft.protocols.http.inspect_http()` sends one origin-form HTTP/1.1 GET or HEAD request
over a fresh TCP connection. It accepts HTTP/1.0 and HTTP/1.1 responses and skips up to five
informational responses. Protocol upgrades, HTTP/2, HTTP/3, proxies, authentication, and
cookie handling are outside this implementation.

```powershell
netcraft http https://example.com --method HEAD --json
netcraft http http://127.0.0.1:8000/status --connect-timeout 3 --read-timeout 2
netcraft http https://127.0.0.1:8443 --ca-file .\test-ca.pem --overall-timeout 8
```

The loopback examples expect existing listeners. The HTTPS example also requires a server
certificate valid for that IP and a PEM trust anchor. The inspector rejects URL credentials.
It sends the supplied query string as part of the request and omits it, the fragment, and any
response body content from results. Response inspection exposes a selected set of response
headers for content, server, connection, and cache behavior. It omits cookies. Redirects
have a follow limit of zero. A reportable HTTP(S) or relative Location is shown without query
or fragment. Unsupported schemes and credential-bearing locations have no reported target.
No redirect can initiate a request or TLS downgrade.

HTTP status describes the observed application response. A completed 404 or 500 is a
successful diagnostic. DNS, TCP, TLS, parser, and deadline failures report their own stages.
If body reading fails after the status and headers arrived, the result retains those
observations and the body bytes counted so far, with a separate read or framing error.

GET defaults to reading at most 16,384 body bytes, with a configurable maximum of 1 MiB.
The limit counts body bytes after removing chunk framing. Payload is discarded rather than
decompressed or returned. Kept metadata is bounded to 32 KiB of response framing and headers
with an 8 KiB line limit. Socket and TLS buffering can consume some bytes ahead of the parser.
Content-Length is advertised metadata, and `body_bytes_read` counts actual consumption. The
reader supports fixed-length, chunked, and close-delimited responses and rejects ambiguous
framing. If a chunked or close-delimited read hits its cap before proving completion,
`body_complete` is false. A zero cap skips the body. HEAD and responses without content
can complete with zero bytes. [RFC 9112 HTTP/1.1 message framing](https://www.rfc-editor.org/rfc/rfc9112.html)

HTTPS uses Python's default trust, with an optional additional CA PEM file, certificate
validation, and hostname or IP verification. Hostname URLs send SNI using the hostname from the
URL, independent of the numeric connection address. The minimum TLS version is 1.2. If TLS
negotiates ALPN, the client offers HTTP/1.1. Verified sessions expose the observed TLS version,
cipher, certificate subject and issuer, validity interval, identity and chain verification
flags, and ALPN. Untrusted chains and hostname/IP mismatches fail before HTTP transmission.
Python/OpenSSL verification does not establish revocation or Certificate Transparency here.
[Python TLS verification and client contexts](https://docs.python.org/3.12/library/ssl.html#ssl.create_default_context)

This excerpt shows a completed HEAD observation with an HTTP error status.

```json
{
  "operation": "http",
  "requested_url": "http://127.0.0.1:8000/status",
  "method": "HEAD",
  "response": {"status": 404, "reason": "Not Found", "version": "1.1", "body_bytes_read": 0},
  "tls": null,
  "success": true,
  "error": null
}
```

The CLI assumes a trusted local operator who can choose local or private destinations. A hosted
service accepting untrusted callers would require its own destination policy, enforcement on
resolved IPs, and egress controls. The inspector itself provides no hosted service.
