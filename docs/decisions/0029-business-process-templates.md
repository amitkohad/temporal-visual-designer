# ADR 0029: Business-process starter templates

Status: accepted for the backend starter-template increment.

Provide two versioned, tenant-neutral templates through a pure factory and authenticated APIs. Employee onboarding uses manager, HR and IT approvals in order. Purchase requisition uses manager and finance approvals in order. These deliberately use the existing linear proof runtime, managed tasks and input-bound forms. They perform no employee provisioning, purchases, notifications or other external actions.

Metadata declares stable template/workflow identifiers, template version `1`, assignment slots and the required workflow input schema. Instantiation requires an explicit user/group assignment for every slot; it embeds no tenant or principal IDs. Each slot supports the existing any-one or all-approver rule. Assignment declarations do not grant roles or prove directory membership; the configured trusted task provider resolves eligible participants when tasks are created.

Each approval form displays immutable context bound from workflow inputs, requires an explicit true `confirmed` value before approval, and allows optional notes. Rejecting a task remains allowed without submission and terminates the proof workflow through its existing `ApprovalRejected` failure. Later approval tasks are not created. Confirmation and notes remain task-form data; this increment adds no workflow output mappings.

Instantiation produces an independent editable Dev project revision through the ordinary project service. It neither publishes, activates nor starts the project, and it adds no template-specific persistence. Duplicate project IDs conflict rather than creating idempotent template receipts. Later project edits and template catalog reads cannot mutate an already-created project. Template version is catalog metadata; there is no automatic template upgrade or synchronization.

List/detail require fresh scoped `project.read`. Creation requires fresh `project.write` and is restricted to Dev before template materialization. Strict JSON bodies accept only project ID, name and declared assignments. Existing server-owned actor, tenant isolation, sanitized errors and revision history apply. No identity role changes or deployment defaults are introduced.

The onboarding input requires employee name, department and start-date text. Purchase input requires requester, item, a positive numeric amount and a three-character currency string. These are starter schema checks, not date parsing, currency validation, monetary accounting or policy evaluation. Threshold routing, connectors, published custom descriptors, reusable project forms and React template selection remain separate work. Parent F03-T006 stays open for the complete descriptor-backed authoring-template scope.
