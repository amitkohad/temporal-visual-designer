# ADR 0064: Deterministic bounded email MIME encoding

Status: accepted for offline message preparation.

## Decision

Keep serialization separate from authorized configuration lookup and future
delivery. Revalidate exact prepared envelope types, canonical fields, live mode,
trusted context and native whole-second UTC time before encoding. Copy validated
snapshots; encoding does not consult storage or renew an authorization grant.

Use the standard-library EmailMessage with explicit SMTP policy: CRLF, ASCII
headers, 78-column folding and UTF-8 plain text encoded as base64. Construct
mailbox headers with Address objects. Include Bcc only in the private transport
recipient tuple. Preserve business text with canonical CRLF line endings in the
decoded SMTP text and a final newline. Bound complete serialized output and
reject malformed wire framing.
Encode the validated Subject as RFC 2047 base64 words, each containing at most
39 UTF-8 bytes without splitting a codepoint. Fold generated words with CRLF and
one space, retaining these folds. This preserves whitespace and valid Unicode
line separators that stdlib header refolding/splitlines can change. Only this
generated ASCII value enters the raw-header setter. Client-supplied header names
or raw header strings are never accepted.
The 64-character maximum encoded word keeps the initial Subject line within
the 76-character encoded-word header limit in
[RFC 2047 section 2](https://www.rfc-editor.org/rfc/rfc2047#section-2).
See Python's [message API](https://docs.python.org/3.12/library/email.message.html),
[policy API](https://docs.python.org/3.12/library/email.policy.html) and
[content manager](https://docs.python.org/3.12/library/email.contentmanager.html).

Caller-supplied persisted time and a context-derived Message-ID avoid ambient
time, randomness and hostname dependencies. Message identity excludes private
recipient/content/reference data. A separate SHA256 digest covers exact wire
bytes. Stable Message-ID supplies neither payload binding nor exactly-once
delivery; future recovery must bind operation, envelope and content explicitly.

## Boundaries

Private frozen result fields and fixed closed errors avoid accidental message
exposure in representations. Deliberate access to private bytes remains sensitive.
No SMTP transport, DNS/TLS/credential lookup, activity registration, public API or
UI is introduced. Future send boundaries must reauthorize and enforce destination
policy; possession of prepared bytes is insufficient permission.

Independent acceptance composes signed administration, real SQLite and preflight
with synthetic worker grants. It establishes offline encoding behavior only.
See [increment evidence](../../specs/001-nexus-flow-designer/implementation-email-mime.md).
