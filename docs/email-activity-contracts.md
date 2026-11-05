# Author an email text activity contract

Explicit offline hosts can use `email_text_activity_descriptor()` and
`EmailTextDescriptorCatalog` to validate a literal email step. Its exact metadata
reference identifies `nexus.email.text@1.0.0`; it is separate from the HTTP JSON
descriptor and is not executable code.

Example business input:

```json
{
  "connection_id": "hr-mail",
  "connection_revision": "v1",
  "execution_mode": "live",
  "to": ["employee@example.test"],
  "cc": ["manager@example.test"],
  "subject": "Onboarding checklist",
  "text_body": "Please complete your onboarding checklist."
}
```

This example validates authoring data only and sends no email. Mock selection
requires a `fixture_digest`; an email mock provider is still unimplemented. Sender
identity, transport host, credentials, arbitrary headers, scope and operation ID
cannot be supplied as business fields.

The initial supported address subset uses an ASCII dot-atom mailbox and a domain
with multiple DNS-style labels. Addresses are bounded to 254 characters, local
parts to 64 and domain labels to 63. Display names, comments, quoted local parts,
Unicode mailbox syntax and domain literals are unsupported. Domains are lowercased;
local spelling is preserved. Exact canonical duplicates across To/Cc/Bcc are
rejected. At least one To recipient is required and the total limit is 50.

Subjects are nonblank and bounded to 256 characters/1,024 UTF-8 bytes with no
control characters. Plain text bodies are nonempty, at most 1 MiB UTF-8, and may
contain newlines but no NUL. Backend validation enforces byte/aggregate rules in
addition to the portable schema. Use `prepare_email_text_message` to revalidate
and obtain an independent private snapshot before any future provider mapping.

The declared output is a future submission receipt with operation ID, mode and
`submission_status: accepted`. It is not a delivery confirmation. No executor,
mail connection registry, transport, secret resolver, real mock provider or
default worker is added. Current executable publication remains blocked. See
[ADR 0061](decisions/0061-email-activity-contracts.md).
