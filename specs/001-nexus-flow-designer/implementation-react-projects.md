# React connected project layouts

Branch codex/068-react-projects from main7cb8bec (merged PR65).
Explicit user authorization continues bounded React work; full UX gates remain
open. Preserve existing sample and canvas visual direction. No new Figma screen
claim or production sign-in. No Python production change.

## Frozen interfaces and ownership

project-api.ts exports recursive JsonValue/JsonObject; ProjectConnection
{organizationId,environmentId,token}; ProjectSummary {project_id,name,head_revision};
ProjectRevision {project_id,revision,parent_revision,summary,author_id,created_at,
content:JsonObject}; ProjectApiError with fixed private codes invalid_request,
unauthorized,forbidden,not_found,conflict,unavailable,invalid_response.
createProjectClient(connection,fetcher=fetch) returns listProjects(),
readProject(id), saveProject(id,expectedRevision,content,summary).
All URLs same-origin /api/v1/organizations/{org}/environments/{env}/projects.
List uses limit100/offset0; UI explicitly describes first100, no full discovery.
Native safe identifiers/positive safe-int revisions, bounded token, fixed errors;
Dot-only path identifiers `.`/`..` are rejected before URL normalization.
10sec timeout; redirect:error,cache:no-store,credentials:omit,referrerPolicy:no-referrer.
Bound JSON wire reads/writes6MiB, no raw errors/payloads/tokens in messages.
Validate selected project identity, revision, content shape and exact save ack
revision=expected+1/content parity. Preserve object fields and detached snapshots.
No automatic retries or response-body diagnostics. Token only in memory.

project-projection.ts exports listWorkflows(revision)->{id:string}[],
projectDocument(revision,workflowId)->DesignerDocument,
withDesignerLayout(revision,workflowId,document)->JsonObject.
Accept only schema0.1 workflows with2..50nodes and<=200edges, no error_routes;
supported node kinds start,end,approval,decision,wait,set_data,child_workflow map
to existing visual equivalents. Other kinds/oversize/invalid topology references
fail closed with ProjectApiError invalid_response, UI explains unsupported view.
Preserve every executable field and unselected workflow/resource byte-semantics.
Presentation-only writes at content.layout.nexus_flow_designer =
{schema_version:'0.1',workflows:{[workflowId]:{nodes:{[nodeId]:{label,description,x,y}}}}}.
Preserve unknown keys in layout/namespaced objects/workflows/node metadata.
Existing malformed namespaced metadata fails closed. Only identifiers,label1..80,
description<=240,finite coordinates x80..1090/y40..740; no insert/delete/edge changes
in connected mode. Defaults stable spaced positions, node IDs as labels, empty
descriptions. Up to five default nodes use a vertical sequence; six to sixteen
use four columns. More than sixteen require complete existing node metadata.
JSON numeric tokens must round-trip exactly in JavaScript and use safe integers
when integral; reject float type/token drift such as Python1.0 and unsafe integers
rather than silently changing executable hashes. Full lossless JSON remains open.
Edge IDs derived deterministic index, labels from decision source_port; rendering
does not interpret conditions. Workflow switching must preserve local layouts.

Projects.tsx default component props {onClose:()=>void}. Connection form labels
Organization ID, Environment ID (Dev only; input must equal dev case-sensitive),
Access token (password), button Connect. Clear token field once connected; do not
persist/browser URL/query/log. Connection session stays mounted for one scope;
Disconnect clears client/project/editor. Guard dirty close/disconnect/reload/open
using inline explicit confirmation, never silently discard. No pending switch.
Project list first100, Open {project_id}, selectedworkflow label Workflow.
Read then projectDocument, initialEditorState fromprojection (no sample insert).
Loaded view uses existing Canvas, Step name/Description, Apply changes, Undo/Redo,
Save project (summary fixed 'Update designer layout'), Reload latest.
Only Dev editable; no roles/grants from browser. While read/save active disable
editing/navigation/session changes and generation-guard async completions.
Save only applied edits, disable when pending properties. On409 preserve local
edits and baseline, show conflict, require explicit reload/discard (no force-save).
Other errors preserve state/retry expected_revision unchanged. Read/write failures
show fixed actionable text, no provider/body output. Beforeunload guard dirty.
Use latest acknowledged full content to preserve multiworkflow edits; reset history
after ack/reload. Cannot select unsupported workflow into a partial editable state.

1. API/projection lane owns src/project-api.ts,project-projection.ts and
   src/project-api.test.ts,project-projection.test.ts. No installs.
2. UI lane owns src/Projects.tsx and projects.css; consumes API/projection/model
   frozen exports. Own component-state orchestration, no App/model changes.
3. Acceptance lane owns tests/Projects.test.tsx,e2e/projects.spec.ts plus fixture
   helpers under tests/project-fixtures.ts; mocked HTTP browser coverage only,
   independent malformed/privacy/conflict review. No shared setup changes.
4. Coordinator owns App navigation, Vite same-origin loopback proxy, config,
   Python real-ASGI/SQLite compatibility test, docs/checklists and final checks.

## Verification and limits

Final local verification: **155 Vitest tests passed** in 38.68 seconds (89
API/projection, 14 connected UI, 42 existing model and 10 existing sample UI).
**Seven Chromium scenarios passed** in 8.8 seconds. Existing sample scenarios
remain covered alongside mocked-HTTP connected load/save/conflict tests. Connected
screenshots at 1280 and 800 pixels were visually reviewed with no page-width
overflow. Actual signed API behavior is established separately below.

**One real browser/API integration passed** in 17.73 seconds: Chromium through
Vite's same-origin proxy, signed FastAPI requests, real SQLite revisions, two
workflows, a concurrent committed revision and explicit conflict/discard/reload.
It verifies exact preservation of executable fields, unrelated layout and the
backend executable digest, with no runtime starts. Synthetic signed identity and
capability fixtures do not establish live Entra or production capability providers.

Production build/TypeScript, Prettier, dependency audit, Python Ruff formatting/
lint, mypy141 and git diff checks passed. No dependency/lock change or Python
production source changed. The full backend/Temporal suites were not rerun; this
increment's actual API/storage/browser path is covered by the explicit integration
test. CI adds that path but no hosted CI result is claimed. Browser sources use
LF via .gitattributes for consistent Prettier checks on Windows/Linux checkouts.

Cross-review fixed reserved identifier/prototype lookups, dot-path normalization,
numeric preservation, incomplete response acknowledgements, response-stream
cancellation and context labels. API/projection, UI and acceptance lanes owned
disjoint files; coordinator handled navigation, proxy and real-provider checks.

Confirmed subtasks: NF-T007c/NF-T015i, F02-T004a, F03-T004b/T007d. Parent feature
tasks and NF-T018–020 remain open. This is presentation-only authoring with a
limited supported workflow/number subset, not portable full graph editing.

No Entra login/token issuance or default role grants. Use an existing
trusted host token and loopback API via dev proxy. No API target chosen by browser,
no CORS widening, no package import/publication/execution, no editing workflow
semantics or claim of full authoring. Fake-HTTP browser tests are separate from
actual signed ASGI/SQLite compatibility evidence and live production identity.
