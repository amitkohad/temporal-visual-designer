# Feature 02: Project revisions and immutable releases

## Outcome and scope

Business users organize multiple workflows, forms, custom activity references, connection declarations, configuration, and test scenarios in one project. Built-in version history replaces a requirement for end-user Git access. This feature owns authoring history and release snapshots; feature 09 owns package transport and environment activation. No application code is part of this specification.

## Requirements

- **F02-FR01:** Scope every project and operation to an organization and authorized environment/project principal; reject cross-organization access without exposing content.
- **F02-FR02:** Save immutable revisions with author, server timestamp, parent revision, change summary, and complete resource references. Use an expected revision token to prevent lost updates.
- **F02-FR03:** List history, compare structural business changes, and restore a revision by creating a new revision; never rewrite history.
- **F02-FR04:** Publish an immutable, whole-project release only after validating every workflow, form, activity dependency, connection declaration, and configuration schema.
- **F02-FR05:** Pin exact resource versions and artifact digests. Exclude secrets, environment addresses, and mutable dependency aliases from release content.
- **F02-FR06:** Generate deterministic canonical content and a release digest. Repeating a publish request with the same idempotency key returns its original result; changed content with that key is rejected.
- **F02-FR07:** Preserve releases referenced by deployments or executions; reject destructive removal while references exist.
- **F02-FR08:** Save project-owned custom activity artifact references even when authored outside the Designer. Dependency validation does not execute Python.

## Contracts and data

Use the normative [shared contracts](../../contracts.md). ProjectRevision contains project identity, parent revision, resource map, author, summary, and concurrency token. ProjectRelease contains release identity, source revision, manifest version, resource digests, pinned dependency closure, and content digest. Revision storage, validator, artifact lookup, release-reference lookup, identity, and audit ports have fake implementations for independent testing. The release snapshot is consumed by packaging without mutation.

## Acceptance scenarios

- **F02-A01 (positive):** Given a project with two valid workflows and a form, when its revision is published, then one immutable release includes both workflows, the form, and exact dependencies.
- **F02-A02 (negative):** Given two editors on revision R, when one saves R+1 and the second saves against R, then the second receives a conflict and neither editor's content is silently overwritten.
- **F02-A03 (recovery):** Given a publish response is lost after commit, when the client retries the same request, then it receives the same release identity and digest with no duplicate release.
- **F02-A04 (positive):** Given historical revision R, when a user restores it, then a new revision matches R's business content while retaining intervening history.
- **F02-A05 (negative):** Given an unresolved activity or embedded secret, when publishing, then validation names the affected resource and no release is created.
- **F02-A06 (negative):** Given a release referenced by an execution, when deletion is requested, then deletion is rejected and the release remains readable by authorized callers.
- **F02-A07 (isolation):** Given another organization's project identifier, when read, save, or publish is attempted, then the operation is denied and no content is returned.

## Success criteria

All seven scenarios pass with local fake dependencies. Canonical serialization produces identical digests for semantically identical fixtures. No failure leaves a partially visible release. UI tests demonstrate history, conflict recovery, restore, and publication without requiring Temporal or Azure.

## Exclusions

Git synchronization, collaborative real-time editing, deployment activation, package signing policy, and migration of running executions are outside this feature.
