# Feature 01 plan

Owner root: `nexusflow/modules/01-identity-environments/`. Shared C01/C10 edits go to the contract owner. NF-T001–003 and identity tenancy/toolchain decisions precede implementation.

## Components and ownership

- `backend/`: token verification adapter, membership/grants, authorization policies, environment registry and scoped placement ports.
- `ui/`: organization/environment selector and membership/role forms; never acts as authorization authority.
- `tests/`: fake issuer/directory/store, contract fixtures, API policy matrix and isolated UI tests.
- `integration/`: provider tests with actual Entra adapter and application resource routes after isolated lanes finish.

Backend/UI/tests may proceed in parallel only against frozen request/result/permission fixtures. Integration owner registers module routes and root dependencies; feature agents do not edit root app manifests or router files.

## Data and API contracts

Own organization bindings, environment and role-grant operations. Validate Entra tokens before creating C01 RequestContext. Return scoped context, not arbitrary namespace credentials. Use stable IDs and optimistic concurrency for grant changes. Environment creation has a stable idempotency key. Placement port abstracts shared/dedicated execution locations without claiming active-run migration.

## Test strategy

Use deterministic signed test-token corpus for invalid lifetime/issuer/audience/key-rotation cases. Fake directory supplies overage, unavailable and revoked-membership results. Table-driven permission tests enumerate all default roles and scopes. Check server authorization independently of UI, including streams and attachments. Actual Entra consent and token lifecycle smoke tests use a dedicated integration organization later.

## Dependencies and risks

Isolated suite has no other feature provider dependency. Integration consumers must use C01 across every API. Entra single/multiple-directory mode and cache-revocation SLA need decisions; do not hardcode email-based tenancy. Integration cannot be declared complete with fake authorization enabled. Scope mutations and provisioning need transactions/reconciliation but no cloud deployment code in this module.
