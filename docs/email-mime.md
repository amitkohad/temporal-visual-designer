# Encode a prepared email message

Explicit hosts can turn a preflight envelope into deterministic message bytes:

```python
from nexusflow.modules.connectors.email_mime import encode_email_message

message = encode_email_message(envelope, message_time=persisted_message_time)
```

Supply a trusted native UTC datetime with whole seconds and year at least 1900.
Persist and reuse this time, the pinned context and message contents for identical
retry bytes. Encoding performs no clock, hostname, DNS, secret or SMTP lookup.

The result holds private ASCII MIME bytes, envelope sender and ordered To/Cc/Bcc
recipients. Headers include From, To, optional Cc, Subject, Date and Message-ID;
Bcc is omitted from headers. User body content is preserved, with line endings
normalized to CRLF by SMTP text serialization and a final newline supplied when
absent. UTF-8 text uses base64.
The default wire-size limit is 2 MiB, configurable from one byte to 8 MiB.

Message-ID hashes scope, operation, pinned connection revision and definition
digest. It excludes recipients, body, credentials and time. It identifies logical
context; it does not bind message contents or guarantee provider deduplication.
The separate content digest hashes exact serialized bytes.

Representations hide sender, recipients and bytes. Encoding errors expose only
fixed messages and closed codes, with no possible delivery or retry reported.
These bytes have no SMTP commands or DATA terminator. Fresh send authorization,
recipient/egress policy, TLS, credentials, transport and durable effect recovery
remain required future work. An envelope or encoded message is not authority to
send. See [ADR 0064](decisions/0064-email-mime.md).
