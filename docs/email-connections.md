# Administer email connection definitions

Explicit hosts can inject `SQLiteEmailConnectionRepository` through
`ApiBackend.email_connections` and own its lifecycle. Signed routes under
`/api/v1/organizations/{organization_id}/environments/{environment_id}` require
fresh scoped administrator `connection.manage` permission:

| Method | Path | Result |
| --- | --- | --- |
| PUT | `/email-connections/{connection_id}/revisions/{connection_revision}` | 201 immutable metadata |
| GET | `/email-connections/{connection_id}/revisions/{connection_revision}` | 200 metadata |
| GET | `/email-connections/{connection_id}/revisions` | 200 bounded revision history |

Example private write:

```json
{
  "transport": "smtp_tls",
  "smtp_host": "mail.example.test",
  "port": 465,
  "sender": "hr@example.test",
  "allowed_recipient_domains": ["example.test", "partner.example.test"],
  "secret_reference": "hr-mail-credentials-v1"
}
```

This configures metadata only. No SMTP connection or secret lookup occurs.
Hosts/domains use bounded ASCII qualified DNS-style names and cannot contain IP
literals, URLs, embedded ports, trailing dots or wildcards. Names are lowercased;
the sender uses the existing [email mailbox subset](email-activity-contracts.md).
Recipient domains are sorted into canonical form; canonical duplicates
are rejected. Matching is intended to be exact, with no subdomain inheritance.
The server does not yet enforce this policy against an executing message.

Public reads contain identity/revision, email connector/transport, digest,
`has_secret_reference`, actor and UTC time. Private values in the example never
appear in metadata/history/errors. Private host lookup can retrieve and validate
the stored definition; public metadata reads do not load it or prove readiness.
Store actual credentials in a future secret provider, never in this definition.

Identical canonical writes return original metadata; different content at the same
revision returns 409. Use another revision to change a definition. History accepts
`limit` (default 25, maximum 50) and exclusive numeric `before_revision`.
Unknown/duplicate query parameters are rejected. Missing definitions return 404,
unconfigured authorized providers 503, forbidden access 403 and invalid input 422.
Default registry quotas are 100 connections per scope and 100 revisions per
connection. Cross-scope lookups cannot return another environment's metadata.

Connection storage does not grant worker access or make email authoring metadata
executable. Transport, secrets, worker/recipient policy, delivery recovery,
browser forms and production deployment remain separate work. See
[ADR 0062](decisions/0062-email-connections.md).
