# Feature 01: Identity and organization environments

Priority: P1 | Status: proposed | Contracts: [C01/C10](../../contracts.md).

## User stories and independent tests

US01 (P1): As an organization administrator, I bind Entra identities and assign environment-scoped permissions so users can only perform authorized work. Independent test: fake token issuer, directory and scoped repository; no live Entra tenant.

US02 (P1): As a platform administrator, I provision logical Dev/UAT/Prod environments and placement so one organization cannot read or invoke another's resources. Independent test: in-memory environment registry and worker-routing adapter.

## Requirements

- F01-FR01: Verify Entra signature, issuer, audience and token lifetime; map stable issuer/subject/directory identifiers to explicit organization membership.
- F01-FR02: Authorize all APIs, downloads and event subscriptions using organization/environment/project scope and server-side grants. UI hiding is insufficient.
- F01-FR03: Provide business author, participant, activity publisher, organization administrator and production support capabilities as in C01. Business authors may import/activate Dev/UAT but not Prod or publish arbitrary Python code.
- F01-FR04: Support Entra users/groups, including overage resolution and revocation/cache behavior; directory failure must not grant new access.
- F01-FR05: Create Dev/UAT/Prod logical environments idempotently, with explicit namespace/worker routing and no arbitrary client-supplied placement.
- F01-FR06: Audit role/membership/placement changes without tokens or secret payloads; deny cross-organization enumeration.
- F01-FR07: Scope workload identities independently of interactive users. Rate-limit authentication and directory lookups.

## Acceptance scenarios

- F01-A01: Given a valid token and Dev author grant, when authoring is requested, then access is allowed only in the granted scope. Covers FR01–03.
- F01-A02: Given expired/wrong-audience/unknown-issuer tokens, when any API is called, then authentication fails without trusting browser organization claims. Covers FR01.
- F01-A03: Given an author in organization A, when an artifact, task, stream, API or run in B is requested, then access is denied without leaking existence or data. Covers FR02/06.
- F01-A04: Given only business-author grants, when Prod import/activation or Python publication is requested, then it is denied; scoped support/publisher grants allow their respective action. Covers FR03.
- F01-A05: Given a group-overage token, when membership is resolved, then stable group IDs determine access; if lookup fails or membership is revoked, then bounded-cache policy applies and no unverified grant occurs. Covers FR04.
- F01-A06: Given environment creation interrupted after Dev creation, when retried, then exactly one Dev/UAT/Prod set exists with stable placement and no cross-org routing. Covers FR05.
- F01-A07: Given a scoped worker credential, when another environment is targeted, then access fails and audit contains correlation without credentials. Covers FR06/07.

## Success and exclusions

All seven cases pass against local fakes and identity adapter conformance fixtures. Every resource access surface has a negative scope test. Real Entra consent/tenant configuration is a later integration test and pending tenancy decision; no infrastructure is provisioned by this feature. Manager hierarchy discovery and automatic cross-tenant federation are not assumed.
