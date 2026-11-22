# DNS record inspection

`netcraft.protocols.dns.inspect_dns()` queries one absolute name in class IN. It supports A
and AAAA addresses, CNAME aliases, MX records with numeric preference, NS, TXT, SOA, CAA,
SRV, and PTR. Supply an IPv4 or IPv6 literal with PTR to derive the corresponding reverse name.
Service labels can include underscores. NetCraft submits absolute names without search suffix
expansion. The direct query path reads any CNAME chain from the reply without a second query.
Returned alias and answer TTLs can reflect time already spent in a cache.

```powershell
netcraft dns example.com --type AAAA --json
netcraft dns 192.0.2.17 --type PTR
netcraft dns lab.example.test --resolver 127.0.0.1 --resolver-port 5353 --timeout 1 --lifetime 2
```

The loopback example expects a DNS fixture on that port.

## Resolver selection

- **Windows, blank resolver.** NetCraft submits the absolute name and record type to the
  Windows DNS Client through `DnsQueryRaw`, with system interface selection and no custom
  upstreams. Windows applies interface eligibility and its configured resolver policy,
  including applicable NRPT rules and encrypted DNS. Windows also controls cache and retry
  behavior. NetCraft waits for at most the smaller of `--timeout` and the remaining
  `--lifetime`, and requests native cancellation when that deadline expires or the user
  cancels. The DNS Client may still be completing cancellation after NetCraft returns.
  Windows installs without this API report a DNS configuration error. Supply an explicit server
  on those installs. [Microsoft DnsQueryRaw reference](https://learn.microsoft.com/en-us/windows/win32/api/windns/nf-windns-dnsqueryraw)
- **Non-Windows, blank resolver.** dnspython reads the system's plain DNS configuration.
  NetCraft tries up to three configured addresses in order after transport failures, malformed
  replies, SERVFAIL, or REFUSED. Each resolver budget is capped by the remaining overall
  lifetime. Resolver configuration discovery is a local OS operation that can finish after
  the lifetime or cancellation request.
- **Explicit resolver.** NetCraft sends UDP to the specified numeric IPv4 or IPv6 endpoint,
  using port 53 by default. A matching truncated response switches to TCP at the same endpoint
  within the original attempt budget. The connected socket filters by peer and the parser
  checks the transaction and question. The direct DNS path is unencrypted and does no local
  DNSSEC validation. NetCraft never substitutes a public DNS service or another address for
  an explicit endpoint.

Results distinguish `answer` from `empty` and `failure`. A NOERROR response with no requested
record yields `empty`, which can also reflect a referral or a partial alias answer.
NXDOMAIN, SERVFAIL, REFUSED, and other error response codes retain their reported RCODE.
Malformed responses, absence of a reply, and socket errors have separate classifications.
Only configured resolvers are contacted. IP reachability errors depend on whether the OS
delivers a socket error or the attempt times out.

This is an excerpt of the output shape for an A answer. Records use DNS presentation format.

```json
{
  "operation": "dns",
  "query_name": "lab.example.test.",
  "query_type": "A",
  "response_code": "NOERROR",
  "status": "answer",
  "records": [{"name": "lab.example.test.", "type": "A", "ttl": 60, "data": "192.0.2.17", "preference": null}],
  "error": null
}
```

Full results add configured resolvers, attempts, the final resolver, canonical name and
aliases when available, authoritative flag when an answer was parsed, UTC timestamps, and
elapsed nanoseconds. `resolver_mode` is `explicit`, `windows_system`, or
`system_configured`. With Windows system resolution, `configured_resolvers` is empty and
one attempt represents the OS call. The result uses the upstream endpoint when the API
reports one, or **System resolver** with a null endpoint otherwise. A cache hit can have no
upstream. `fallback_occurred` is null when retries are internal to Windows, and is true on the
portable path only after another configured endpoint is tried. The UDP-to-TCP switch stays
visible in the attempt list. The response code is null if the final attempt provides neither a
matching DNS reply nor a Windows DNS response status.
[dnspython message and resolver APIs](https://dnspython.readthedocs.io/en/latest/)
