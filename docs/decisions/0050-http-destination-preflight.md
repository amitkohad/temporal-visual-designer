# ADR 0050: Worker-authorized HTTP destination preflight

Status: accepted for the isolated public-IPv4 routing subset of feature 08.

Add internal `HTTPDestinationPlanner.prepare` and `prepare_redirect` ports.
Requests identify a scoped immutable connection revision, stable operation ID and
origin-relative path/query. No method, body, headers or dedicated credential-value
field is provided by this routing boundary. It creates a plan, not an HTTP effect or a
browser dispatch endpoint.

Each hop rechecks `worker.execute` through a host-supplied trusted authorization
port before private registry access or DNS. Validate the returned actor ID,
scope, connection record, definition digest and reference-presence metadata.
Secret references remain unresolved and are absent from approved plans.

Initial targets are single-slash origin-relative paths. Raw controls, whitespace,
backslashes, malformed escapes, fragments, user information and authority
overrides are rejected. Redirect locations are checked before URL joining and
then against the pinned connection's exact canonical HTTPS origin allowlist.
Scheme-relative redirects are deliberately unsupported. Every allowed hop gets
fresh DNS approval; redirects retain the logical operation ID and increment a
bounded hop counter (default five, configurable zero–ten).

The injected resolver returns only canonical IPv4 A answers, at most 16 unique
addresses. All answers must pass public-unicast checks; do not silently filter a
mixed public/private answer set. Use explicit special-use exclusions in addition
to Python's `ipaddress` classification, including shared, loopback, link-local,
documentation, benchmark, multicast and reserved ranges. Python documents that
shared address space is not global, and leading-zero IPv4 notation is rejected
([Python 3.12 reference](https://docs.python.org/3.12/library/ipaddress.html)).

Explicitly exclude Azure's `168.63.129.16` from connector HTTP destinations:
Microsoft identifies it as a special virtual address for platform services
([Microsoft documentation](https://learn.microsoft.com/en-us/azure/virtual-network/what-is-ip-address-168-63-129-16)).
This application-level rule does not change VM firewall, platform-agent or system
DNS traffic. IPv6, private destinations and proxy routing are unsupported here.

The immutable result carries exact approved addresses, original TLS host/port,
canonical origin/URL, scoped revision/digest, operation ID and redirect count.
Request/plan representations hide the path/full URL; errors contain only closed
codes and a constant message, not provider details. DNS awaits are bounded
(default five seconds, configurable up to 30); cooperative caller cancellation
propagates. Only unavailable provider/deadline outcomes are marked retryable;
this component does not perform retries.

Approval also checks monotonic elapsed time after the resolver returns, so a
late answer is rejected even if a blocking provider delayed the timeout callback.
Resolver implementations must still cooperate with the event loop; preflight
cannot preempt arbitrary synchronous code.

A future transport must connect only to the approved addresses, preserve TLS
hostname/certificate verification, disable independent DNS/proxy/redirect
fallbacks and repeat preflight per redirect. This increment does not establish
network enforcement without that adapter. Real DNS/HTTP, secret resolution,
payload validation, retries/idempotent effects, mocks, worker/Designer wiring and
full F08 acceptance remain open.

Origin approval alone does not grant permission to forward credentials between
origins. Future credential bindings must separately define that behavior.
