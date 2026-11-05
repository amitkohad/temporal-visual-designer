# Email activity authoring contract increment

Branch: `codex/063-email-activity-contracts`, based on main `8d1b251` (PR #60).

## Frozen interfaces and parallel plan

Add `email_types.EmailTextActivityInput` with connection_id/revision, required
execution_mode live/mock, optional fixture_digest (required only for mock),
to/cc/bcc recipient lists, subject and text_body. At least one To recipient and
at most 50 recipients total. Addresses use a bounded ASCII dot-atom mailbox and
multi-label DNS-style domain subset: no display names, quoted local parts,
comments, Unicode mailbox syntax or domain literals. Preserve local spelling,
lowercase domains and reject exact canonical duplicates across recipient lists.
Bounds: address254/local64/label63 characters; subject1..256 characters and at
most1024 UTF-8 bytes, nonblank with no C0/C1 controls; text_body1..1048576
characters and at most1MiB UTF-8 with no NUL (ordinary body newlines allowed).
No sender, credentials, transport host, arbitrary headers, scope or operation ID
are business inputs. This is an explicit supported subset, not full email syntax.

`EmailTextActivityOutput` contains operation_id, execution_mode and
submission_status accepted. It is a future submission contract, not evidence of
delivery or a currently implemented provider. `PreparedEmailTextMessage` is a
frozen independent snapshot with private recipient/subject/body repr and tuple
recipient fields. `prepare_email_text_message(value)` strictly revalidates an
exact input model, including model_construct cases, and raises fixed private
`EmailAuthoringError` invalid_request on failure. Mailbox normalization is shared
through `canonical_email_address(value)`.

`email_descriptor.email_text_activity_artifact()` returns canonical contract
metadata bytes. `email_text_activity_descriptor()` returns fresh descriptor
`nexus.email.text@1.0.0` with digest of those bytes, email connection requirement,
unregistered capability `connector.email.text.v1`, timeout30/one attempt and
reference-free portable schemas. `EmailTextDescriptorCatalog.descriptor(ref)`
performs exact descriptor-only lookup, with no executable resolver or defaults.
Schemas describe the authoring subset; backend byte/aggregate/canonical rules
remain authoritative where portable schema is less expressive.

1. [P] Message lane owns typed validation, canonical addresses, snapshots and tests.
2. [P] Descriptor lane owns content identity, portable schemas, lookup and tests.
3. [P] Acceptance lane independently verifies offline authoring, HTTP/email
   independent selection and blocked executable publication without network.
4. Coordinator owns docs, verified checkboxes and regression checks.

## Verification

Focused verification passed: **83 message tests** (0.46 seconds), **47 descriptor
tests** (1.07 seconds), and **24 independent acceptance tests** (3.34 seconds).
The 154 cases cover canonical mailbox syntax and limits, injection/byte/mode
rejection, private independent snapshots, exact metadata content identity,
reference-free schemas and guarded lookup. Portable local-part and absolute-end
guards also reject overlong local parts and trailing header/pin newlines.

Independent acceptance composes the actual offline AuthoringValidator and
ProjectService/Python catalog. HTTP and email remain independently selectable
through an explicit test-only descriptor overlay; executable publication stays
blocked and runtime capability providers are not called. DNS/socket/SMTP
sentinels remain untouched. No provider, real mock, sending or delivery acceptance
is inferred. Read-only cross-review found no actionable blockers.

Ruff lint/format, configured mypy (132 source files), lock verification and
source/wheel build passed. Wheel parity passed for both final email modules.
Full default regression passed **3,854 tests**, with eight existing platform/
permission skips and 91 integration tests deselected (680.80 seconds). Final
independent review and diff checks passed. No dependencies, executable catalog,
API or runtime registrations changed. No new Temporal/provider acceptance is
claimed for this authoring-only increment.

## Remaining scope

Email connection administration, sender/recipient destination policy, SMTP/API
transport, secrets, email mocks, durable delivery ambiguity/retries, worker
dispatch, executable publication and Designer forms remain open. No email is sent
and no default runtime/API/catalog registration is added.
