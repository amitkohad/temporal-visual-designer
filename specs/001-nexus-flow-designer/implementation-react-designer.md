# First React business-user workspace

Branch `codex/067-react-designer`, from merged main `17676db` (PR #64).
The user's explicit request to start React authoring authorizes this bounded
implementation despite the older blanket NF-T020 prerequisite. Preserve approved
frame 5:14; complete UX/usability gates remain open and are not inferred here.

## Frozen parallel interfaces

Standalone `web/`: React + TypeScript + Vite, plain CSS semantic tokens, Vitest
and Testing Library for components, Playwright for actual Chromium acceptance.
No Python changes or backend requests. Visible sample/local-only status; no
claim of saving, deployment, execution or production identity. No browser storage
of workflow/credential data. Reload resets the sample; beforeunload guards edits.

`src/model.ts` exports NodeKind union start/approval/decision/reference/update/
subworkflow/end/timer, DesignerNode {id,kind,label,description,x,y}, DesignerEdge
{id,source,target,label?}, DesignerDocument {nodes,edges}, EditorState
{document,past:DesignerDocument[],future:DesignerDocument[],selectedId:string},
initialState():EditorState, editorReducer(state,action):EditorState.
Actions: select {id}, rename {id,label}, describe {id,description}, atomic edit
{id,label,description}, move
{id,dx,dy}, insert {kind:approval|timer,afterId}, undo, redo. Max50 nodes,
label1..80 trimmed nonblank, description<=240, coordinate clamp x80..1090/y40..740.
Invalid actions no-op. Insert only after a node with exactly one outgoing edge;
preserve that edge's label/source and retarget to new node, append new->old target.
No graph semantics change from rename/move; no temporal/runtime contract implied.
Stable initial ids start,approval,decision,reference,status,subworkflow,end.
Initial positions match Figma centers (570,94),(570,208),(570,363),(288,363),
(852,363),(288,538),(570,726). Decision initially selected. History bounded50.

Canvas.tsx default WorkflowCanvas takes {document,selectedId,onSelect(id),
onMove(id,dx,dy)}. Local zoom/fit, keyboard node buttons (arrow movement with
modifiers or explicit inspector move buttons), dynamic edges and minimap, named
Approved/Rejected branch labels, data-driven semantic outline in shell. Canvas
owns canvas.css only; app owns app.css/tokens.css and assets. Canvas uses asset
paths /src/assets/approval.svg,decision.svg,reference.svg,update.svg,subworkflow.svg,
start.svg. Exported Figma assets copied intact. No shared writes across lanes.

1. State lane owns model.ts and model.test.ts.
2. Canvas lane owns Canvas.tsx and canvas.css; consumes frozen model types/assets.
3. Acceptance lane owns tests/ and e2e/ only; independent rendered interaction,
   keyboard, malicious labels, local-only/disabled execution, Chromium screenshots.
4. Coordinator owns package/lock/config/dependencies, App/main, tokens/app CSS,
   design assets, docs, checklist evidence and integration.

## Verification

State lane: 42 tests cover immutable edits, exact branch preservation, safe
insertion, bounds/history, atomic property apply and invalid-action rejection.
Independent UI lane: 10 component cases cover names, disabled execution/export,
literal malicious labels, blank-name rejection, pending edits across selection,
insertion eligibility, undo/redo, outline and dirty leave protection.

Five actual Chromium scenarios cover edit/undo/redo/insert/reload without API
requests, focused-node keyboard movement, safe literal text and responsive
screenshots at 1600, 1280 and 800 pixels. Durable checks verify all six Figma
assets load and their rendered geometry matches native dimensions after zoom.
Screenshots are generated under ignored artifacts/react-designer/ and visually
reviewed against frame 5:14. Figma context retrieval used the root and child
nodes; the sparse root metadata was not used alone as implementation context.

Cross-review found and fixed duplicate captions, accessible button names and
non-atomic property history. The independent state/canvas/acceptance lanes use
disjoint ownership. Coordinator owns the package lock and dependency changes.

Final local verification: **52 Vitest tests passed** (42 model + 10 UI) in
8.89 seconds; **5 Chromium tests passed** in 6.7 seconds. Production build and
TypeScript checks, Prettier format check, dependency audit and git diff check
passed. All six SVG assets were inspected as nonempty local files and verified
in their live rendered slots. Final screenshots were inspected after fitting
the desktop workspace to the viewport and making the inspector scroll internally;
the 800-pixel layout reflows the inspector below the canvas. Bundled DM Sans
fonts retain their OFL notice under public/licenses/ in build output.

A dedicated React CI workflow runs these checks with Chromium on Linux;
local Windows execution does not claim hosted CI or cross-platform acceptance.
No Python production source or dependency changed. Existing Python Ruff and mypy
checks pass; the prior full backend baseline was 4,324 passed/8 host skips.

## Remaining scope

This closes NF-T007b and bounded F03-T004a/T007c only. Full F03/UI/UX readiness,
NF-T018–020, representative-user research and assistive-technology verification
remain open. There is no backend save/revision conflict/recovery, Entra browser
login, project/form/catalog API integration, executable activity configuration,
branch/edge editing, general graph routing/collision prevention, drag/drop,
mapping, validation, deployment/package export or debugger/execution integration.
The sample model must not be treated as a portable backend workflow contract.
