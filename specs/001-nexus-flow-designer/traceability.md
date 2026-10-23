# Requirement and verification map

This matrix points to feature requirements and their local acceptance/task lists. It is planning traceability, not execution evidence. Local acceptance IDs with ranges refer to every scenario in that range.

| Product requirement | Feature requirements | Acceptance | Planned task owner |
|---|---|---|---|
| NF-001 standalone experiences | F03-FR01/05, HT-FR07, F09-FR03 | F03-A01/04, HT-A1/A2, F09-A01/A04 | NF-T007/010/013, composition NF-T015 |
| NF-002 organization environments | F01-FR02/05, F10-FR01/02 | F01-A03/06, F10-A01/A02 | NF-T005/014 |
| NF-003 Entra and authorization | F01-FR01–07 | F01-A01–07 and each feature's negative scope case | NF-T005 and all provider integrations |
| NF-004 projects and revisions | F02-FR01–08, F03-FR01/07 | F02-A01–07, F03-A06 | NF-T006/007 |
| NF-005 packages and activation | F09-FR01–04/08 | F09-A01/A02/A05 | NF-T013 |
| NF-006 pinned releases and rollback | RT-FR01/07, F09-FR04/05, F04-FR07 | RT-A4, F09-A03/A06, F04-A06 | NF-T008/009/013 |
| NF-007 baseline catalog | F03-FR02–06, RT-FR02–06, HT-FR01–07, F08-FR01–07 | F03-A01–07, RT-A1–A7, HT-A1–A7, F08-A01–05 | NF-T007/009/010/012 |
| NF-008 Designer debug | DB-FR01–07 | DB-A1–A7 | NF-T011 |
| NF-009 Python import/publish/execute | F04-FR01–10 | F04-A01–08 | NF-T008, especially F04-T007 |
| NF-010 promotion roles and evidence | F01-FR03, F09-FR03/06/07 | F01-A04, F09-A02/A04 | NF-T005/013 |
| NF-011 portability/scaling | F10-FR01–07 | F10-A01–05 | NF-T014/017 |
| NF-012 isolated and integrated tests | Every local success criterion | All local scenarios and both reference processes | NF-T005–017 |

## Baseline capability coverage

| Capability | Definition/authoring | Execution/provider | Required verification |
|---|---|---|---|
| Manual/API/form/schedule/webhook starts | 03 | 05 | Start schema, authorization, replay protection, dedup, schedule overlap/timezone |
| Sequence/decision/switch/parallel/join/loop/reusable flow | 03 | 05 | Type/scope validation, branch/join semantics, bounds, cancellation |
| Variables/mapping/expressions | 03 | 05 | Same versioned semantics in authoring/runtime, invalid/secret inputs |
| Forms and attachments | 03 | 06 | Conditional validation, field permission, keyboard, quarantine |
| Approval rules and inbox | 03 configuration / 06 rules | 06 with 05 response port | Any/all/sequential/conditional, duplicate/stale decisions |
| Delegation/reassignment/reminders/escalation | 06 | 06 with 08 notification port | Revocation, deadlines, timer/decision races, recovery |
| Delay/wait/external response | 03 | 05 | Virtual time, restart, duplicate response |
| HTTP/email/notification | 04 descriptors / 08 configuration | 08 | Secret redaction, egress, timeout, ambiguous delivery |
| Custom Python | 04 | 04 worker with 05 dispatch | Import trust, locked dependencies, isolation, output validation |
| Retry/failure/compensation | 03 | 05 with 04/08 effects | Exhaustion, cancellation, only explicit compensation |
| Basic debug | 07 presentation | 05 durable gates | Dev-only, branch permits, redaction, restart |

Before implementation completion, test reports must name concrete test files/commands for each scenario. Documentation coverage alone never closes an implementation task.

## Experience extension

NF-013 maps to UX-001–014, F03-FR10/11, UX-A01–08 and F03-A08/09. Design delivery is NF-T018–020 / UX-T001–006. Implementation follows each affected feature's UI tasks plus the approved design gate. Screen owners: 02/03 projects and authoring, 04 activity import, 06 task portal, 07 debugging, 08 connections, 09 release administration. End-to-end and readiness suites verify interactions, accessibility and usability rather than screenshot similarity alone.
