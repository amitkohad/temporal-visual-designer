# Run the React designer sample

From the repository root, using Node 24 LTS and npm:

```sh
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. The standalone sample needs no Python server,
Temporal service, Entra credentials or connector configuration. Fonts and Figma
icons are bundled locally; the browser does not load them from a third party.

Select a step on the canvas or open the workflow outline. Edit its name and
description, then Apply changes. One undo restores both fields. Unapplied property
edits stay with their step when selection changes. Apply pending edits before
undo/redo. Move steps using focused-node arrow keys (Shift moves farther) or the
inspector's labeled buttons. Fit, zoom and the minimap operate on the graph view.

Open Activities, search for approval or timer, and insert after a selected step
with exactly one outgoing path. A decision or end cannot be used as that insertion
point. The new step preserves the existing edge and its branch label. The local
history retains the last 50 edits; the diagram is limited to 50 steps.

This is an in-memory authoring sample based on approved Figma frame 5:14. Reload
resets it, and edited content triggers a browser leave warning where supported.
It does not persist a project, resolve activity settings, validate deployability,
execute a workflow or export a package. Those controls are explicitly disabled.
The sample model is separate from backend workflow/package contracts.

Verification:

```sh
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

The Chromium suite exercises pointer/keyboard authoring and screenshots at 1600,
1280 and 800 pixels. This is bounded browser evidence; full WCAG/assistive
technology, usability, arbitrary graph layout and production browser conformance
remain open. See [increment evidence](../specs/001-nexus-flow-designer/implementation-react-designer.md).

## Connect to Dev projects

The Projects button opens the connected workspace. Prefer the pilot Hosted Dev
host: build `web/` then `uv run nexusflow-local serve --ui` so the UI and API share
one loopback origin ([pilot runbook](pilot-runbook.md)). Engineers can still run
the API alone and use `npm run dev`; Vite proxies same-origin `/api` to
`http://127.0.0.1:8000` (override with `NEXUSFLOW_API_TARGET` in the Vite process
only). The browser cannot choose a proxy destination. Production hosting requires
its own same-origin reverse proxy and identity setup.

Enter the organization ID, `dev` and a bearer token from that host, then Connect.
For the local launcher use organization `local-demo` and obtain a token with
`uv run nexusflow-local token`. Token issuance/sign-in is outside the browser app.
The token field clears after connecting and the session remains in memory only;
Disconnect clears it. Projects are not placed in URLs or browser storage.

Open a project from the first 100 results, select a workflow, then adjust step
labels, descriptions or positions. Apply property changes before saving. Save
project appends a revision using the last acknowledged revision number. All
workflow semantics and other resources are preserved; presentation metadata is
stored in `content.layout.nexus_flow_designer`. Editing several workflows before
one save preserves their individual layouts. A successful acknowledgement resets
the local history for that revision.

On failure, keep the tab open: edits stay in memory. An ambiguous failed save may
already have committed; a retry uses the same expected revision, so a conflict
cannot silently overwrite it. On conflict, saving is blocked until Reload latest.
Reload, opening another project, disconnecting and closing the workspace require
explicit confirmation before discarding unsaved work. There is no automatic merge
or force-save. Closing/reloading the browser after accepting its leave warning
still discards unsaved work; disk recovery is not implemented.

The connected view supports up to 50 start/end/approval/decision/wait/set-data/
subworkflow nodes, at most 200 edges and no error routes. More than 16 nodes need
complete existing designer positions. Other activity/control-flow shapes and
malformed presentation metadata are blocked from editing. Numeric JSON that
JavaScript would round or re-encode differently (including Python `1.0`) is also
blocked to preserve executable hashes. This is a conservative lossless subset,
not the full portable workflow format. Dot-only `.` and `..` resource identifiers
are also unavailable through this client because browsers normalize URL paths.
New node insertion, activity settings,
assignments, mappings, publication and execution remain unavailable here.

Phase 1 executable authoring, template create, publish/activate/start and the
task portal must follow the frozen [MVP Dev-loop contracts](mvp-dev-loop.md):
supported kinds, layout-versus-executable mutation rules, golden-path APIs,
local-demo token identity for the first pilot, and disjoint writable paths for
Designer (1A), task portal (1B) and run controls (1C). Do not invent parallel
payload shapes.

Actual browser/API/storage compatibility can be checked from the repository root:

```sh
uv run pytest -q -m integration web/integration/test_project_api.py
```

This explicit suite requires installed web dependencies and Chromium. It starts
temporary loopback Vite/FastAPI servers, uses a synthetic signed identity and real
SQLite, verifies a real save/conflict/reload, and checks that executable digests
remain unchanged. It requires no Temporal or live Entra connection.
