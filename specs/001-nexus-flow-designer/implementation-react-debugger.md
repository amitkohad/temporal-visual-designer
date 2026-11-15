# Implementation evidence: React Dev debugger panel (Phase 3.8 / DB-T03)

## Scope delivered

- **DB-T03 (partial):** React debugger panel, canvas overlay adapter (node-set only; no Designer-core edits), breakpoint controls, and redacted variable inspector under `web/src/`, wired to existing signed Dev debug APIs.
- Prefill from App Designer publish gate (project / workflow / source revision) when the Projects session is present.
- Critical-path vitest coverage for client parse/control and panel start → step → inspect.

## Paths

| Path | Role |
|---|---|
| `web/src/debugger-api.ts` | Typed no-store client for sessions, reconnect, events, commands, breakpoints, inputs/step IO, rerun |
| `web/src/debugger-api.test.ts` | Parser + start/status/step critical path |
| `web/src/DebuggerPanel.tsx` | Dev Debugger surface + `mountDebuggerPanel` |
| `web/src/DebuggerCanvasOverlay.tsx` | Overlay adapter for current/breakpoint/completed node IDs |
| `web/src/debugger.css` | Panel styles |
| `web/tests/DebuggerPanel.test.tsx` | Mocked HTTP start/step/inspect critical path |
| `web/src/App.tsx`, `web/src/app-session.ts` | Thin `debugger` nav mount + gate prefill |

## Persistence (no new backend routes)

Reuses existing `…/debug-sessions` APIs (create, list, status, reconnect, events, breakpoints, commands, inputs, step input/output, reruns). Host must run `nexusflow-local enable-debugger`.

## Verification

```sh
cd web && npm test -- --run src/debugger-api.test.ts tests/DebuggerPanel.test.tsx tests/App.test.tsx
cd web && npm run build
```

## Non-claims / remaining gaps

- Mocked HTTP / local-demo token ≠ Entra or production-provider acceptance.
- Overlay adapter ≠ in-canvas Designer mutation; Canvas.tsx is untouched.
- Parallel frontier selection (DB-A4), streaming events, general variables / dynamic inputs, managed-task cancellation cleanup, idle expiry, and full DB-A1/A5/A7 browser acceptance remain open.
- Parent **DB-T03** stays open for historical `nexusflow/modules/07-designer-debugger/ui/` path and complete DB-A* UI acceptance; this is the React Dev-loop slice.
- Mode is always `real`; UI does not offer simulated connections.
