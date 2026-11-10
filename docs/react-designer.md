# Run the React designer sample

From the repository root, using Node 24 LTS and npm:

```sh
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. The standalone React app needs no Python server,
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
