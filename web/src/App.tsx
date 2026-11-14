import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import WorkflowCanvas from "./Canvas";
import Projects, { type DesignerPublishGate } from "./Projects";
import ReleaseControls from "./ReleaseControls";
import Tasks from "./Tasks";
import {
  emptyPublishGate,
  releaseTargetsFromGate,
  type AppView,
} from "./app-session";
import { editorReducer, initialState } from "./model";
import type { DesignerNode } from "./model";
import type { ProjectConnection } from "./project-api";

const baseline = JSON.stringify(initialState().document);
const activityKinds = [
  {
    kind: "approval" as const,
    label: "Approval",
    detail: "Ask someone to review a request",
    asset: "approval",
  },
  {
    kind: "timer" as const,
    label: "Timer",
    detail: "Add a waiting step to the diagram",
    asset: null,
  },
];
type Draft = { label: string; description: string };
type LeaveTarget = Exclude<AppView, "sample">;

export default function App() {
  const [view, setView] = useState<AppView>("sample");
  const [gate, setGate] = useState<DesignerPublishGate>(emptyPublishGate);
  const [session, setSession] = useState<ProjectConnection | null>(null);
  const [taskRunId, setTaskRunId] = useState("");

  const onPublishGateChange = useCallback((next: DesignerPublishGate) => {
    setGate(next);
    setSession(next.connection);
  }, []);

  const openTasks = useCallback((runId: string) => {
    setTaskRunId(runId);
    setView("tasks");
  }, []);

  const targets = releaseTargetsFromGate(gate);
  const releaseControls = targets ? (
    <ReleaseControls
      connection={targets.connection}
      projectId={targets.projectId}
      headRevision={targets.headRevision}
      workflowId={targets.workflowId}
      validationOk={!gate.publishBlocked}
      onOpenTasks={openTasks}
    />
  ) : null;

  return (
    <>
      {view !== "sample" && (
        <DevLoopNav view={view} onNavigate={setView} />
      )}
      {view === "sample" && (
        <SampleWorkspace onNavigate={setView} />
      )}
      {view === "projects" && (
        <Projects
          onClose={() => setView("sample")}
          releaseControls={releaseControls}
          onPublishGateChange={onPublishGateChange}
        />
      )}
      {view === "release" && (
        <ReleaseSurface
          controls={releaseControls}
          onOpenProjects={() => setView("projects")}
        />
      )}
      {view === "tasks" && (
        <Tasks
          onClose={() => setView(session ? "projects" : "sample")}
          initialRunId={taskRunId}
          connection={session ?? undefined}
        />
      )}
    </>
  );
}

function DevLoopNav({
  view,
  onNavigate,
}: {
  view: AppView;
  onNavigate: (view: AppView) => void;
}) {
  const items: { id: AppView; label: string }[] = [
    { id: "sample", label: "Sample" },
    { id: "projects", label: "Projects" },
    { id: "release", label: "Release" },
    { id: "tasks", label: "Tasks" },
  ];
  return (
    <nav className="dev-loop-nav" aria-label="Dev loop">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          aria-current={view === item.id ? "page" : undefined}
          onClick={() => onNavigate(item.id)}
        >
          {item.label}
        </button>
      ))}
    </nav>
  );
}

function ReleaseSurface({
  controls,
  onOpenProjects,
}: {
  controls: React.ReactNode;
  onOpenProjects: () => void;
}) {
  return (
    <div className="release-surface" aria-label="Release controls">
      <header className="release-surface-header">
        <div>
          <span className="project-eyebrow">NEXUS FLOW DESIGNER</span>
          <h1>Release</h1>
        </div>
        <button type="button" onClick={onOpenProjects}>
          Open projects
        </button>
      </header>
      <p className="release-surface-boundary">
        Publish, activate, and start from the shared Dev session. Validate in
        Projects first; publish stays blocked while authoring issues remain.
      </p>
      {controls ?? (
        <div className="release-surface-empty" role="status">
          <h2>Connect a project first</h2>
          <p>
            Open Projects, connect with org <code>local-demo</code>, environment{" "}
            <code>dev</code>, and a bearer token, then open a workflow. Release
            controls appear here once the Designer publish gate is ready.
          </p>
          <button type="button" className="primary" onClick={onOpenProjects}>
            Go to Projects
          </button>
        </div>
      )}
    </div>
  );
}

function SampleWorkspace({
  onNavigate,
}: {
  onNavigate: (view: LeaveTarget) => void;
}) {
  const [pendingLeave, setPendingLeave] = useState<LeaveTarget | null>(null);
  const [state, dispatch] = useReducer(editorReducer, undefined, initialState);
  const [palette, setPalette] = useState(false);
  const [search, setSearch] = useState("");
  const [outline, setOutline] = useState(false);
  const [properties, setProperties] = useState(true);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const searchInput = useRef<HTMLInputElement>(null);
  const selected = state.document.nodes.find(
    (node) => node.id === state.selectedId,
  )!;
  const draft = drafts[selected.id] ?? selected;
  const pending = Object.entries(drafts).some(([id, value]) => {
    const node = state.document.nodes.find((item) => item.id === id);
    return (
      node &&
      (node.label !== value.label || node.description !== value.description)
    );
  });
  const dirty = pending || JSON.stringify(state.document) !== baseline;
  const outgoing = state.document.edges.filter(
    (edge) => edge.source === selected.id,
  );
  const canInsert = outgoing.length === 1 && state.document.nodes.length < 50;

  useEffect(() => {
    if (!dirty) return;
    const protect = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty]);
  useEffect(() => {
    if (palette) searchInput.current?.focus();
  }, [palette]);

  function select(id: string) {
    dispatch({ type: "select", id });
    setProperties(true);
    setError("");
  }
  function changeDraft(field: keyof Draft, value: string) {
    setDrafts((previous) => ({
      ...previous,
      [selected.id]: {
        label: draft.label,
        description: draft.description,
        [field]: value,
      },
    }));
    setError("");
  }
  function apply(event: React.FormEvent) {
    event.preventDefault();
    if (
      !draft.label.trim() ||
      draft.label.trim().length > 80 ||
      draft.description.length > 240
    ) {
      setError(
        "Enter a step name of 1–80 characters and a description of up to 240 characters.",
      );
      return;
    }
    dispatch({
      type: "edit",
      id: selected.id,
      label: draft.label,
      description: draft.description,
    });
    setDrafts((previous) => {
      const next = { ...previous };
      delete next[selected.id];
      return next;
    });
    setNotice("Step updated in this tab.");
  }
  function history(type: "undo" | "redo") {
    if (pending) {
      setNotice("Apply your pending property edits before using undo or redo.");
      return;
    }
    setDrafts({});
    dispatch({ type });
    setNotice(type === "undo" ? "Change undone." : "Change restored.");
  }
  function move(id: string, dx: number, dy: number) {
    dispatch({ type: "move", id, dx, dy });
  }
  const nodeLabel = (id: string) =>
    state.document.nodes.find((node) => node.id === id)?.label ?? id;
  const displayKind = (node: DesignerNode) =>
    ({
      approval: "Human task",
      decision: "Decision",
      reference: "Create reference",
      update: "Set data",
      subworkflow: "Subworkflow",
      start: "Start event",
      end: "End event",
      timer: "Timer",
    })[node.kind];

  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace">
        Skip to workflow
      </a>
      <header className="app-header">
        <span className="brand-mark" aria-hidden="true">
          n /
        </span>
        <strong>Nexus Flow Designer</strong>
        <span className="project-context">
          Sample operations <span>/</span> Purchasing
        </span>
        <span className="environment">DEV</span>
        <span className="sample-context">Sample workspace</span>
        <div className="sample-nav" aria-label="Dev loop">
          <button
            type="button"
            onClick={() =>
              dirty ? setPendingLeave("projects") : onNavigate("projects")
            }
          >
            Projects
          </button>
          <button
            type="button"
            onClick={() =>
              dirty ? setPendingLeave("release") : onNavigate("release")
            }
          >
            Release
          </button>
          <button
            type="button"
            onClick={() =>
              dirty ? setPendingLeave("tasks") : onNavigate("tasks")
            }
          >
            Tasks
          </button>
        </div>
        <span className="avatar" aria-label="Local sample user">
          SU
        </span>
      </header>
      {pendingLeave && (
        <section
          role="alertdialog"
          aria-label="Leave sample?"
          className="sample-banner"
        >
          <p>Leave the sample and discard changes in this tab?</p>
          <button onClick={() => onNavigate(pendingLeave)}>
            Discard sample changes
          </button>
          <button onClick={() => setPendingLeave(null)}>
            Keep sample editing
          </button>
        </section>
      )}
      <section className="workflow-toolbar" aria-label="Workflow tools">
        <div className="workflow-title">
          <h1>Purchase requisition</h1>
          <p>Workflow / Local sample</p>
        </div>
        <span className={`save-state ${dirty ? "dirty" : ""}`} role="status">
          {dirty ? "Unsaved changes" : "Sample loaded"}
        </span>
        <div className="toolbar-actions">
          <button
            onClick={() => history("undo")}
            disabled={!state.past.length}
            aria-label="Undo"
            title="Undo last applied change"
          >
            ↶
          </button>
          <button
            onClick={() => history("redo")}
            disabled={!state.future.length}
            aria-label="Redo"
            title="Redo last undone change"
          >
            ↷
          </button>
          <button disabled title="Backend validation is not connected">
            Validate
          </button>
          <button
            aria-label="Test workflow"
            disabled
            title="Workflow execution is not connected"
          >
            ▷ Test workflow
          </button>
          <button
            className="primary"
            disabled
            title="Deployable package export is not connected"
          >
            Export project
          </button>
        </div>
      </section>
      <div className="sample-banner">
        Sample project · changes stay in this tab{" "}
        <span>Reload resets the sample. No workflow is executed.</span>
      </div>
      <main
        id="workspace"
        tabIndex={-1}
        className={`workspace ${!properties ? "inspector-closed" : ""}`}
      >
        <aside className="activity-rail" aria-label="Activity tools">
          <button
            aria-label="Activities"
            aria-expanded={palette}
            onClick={() => setPalette(!palette)}
            title="Activities"
          >
            <span aria-hidden="true">⌕</span>
          </button>
          <button
            aria-label="Browse approvals"
            onClick={() => {
              setPalette(true);
              setSearch("approval");
            }}
            title="Approval"
          >
            <img
              src={new URL("./assets/approval.svg", import.meta.url).href}
              alt=""
            />
          </button>
          <button
            aria-label="Browse timers"
            onClick={() => {
              setPalette(true);
              setSearch("timer");
            }}
            title="Timer"
          >
            <span aria-hidden="true">◷</span>
          </button>
          <button
            aria-label="Workflow outline"
            aria-expanded={outline}
            onClick={() => setOutline(!outline)}
            title="Workflow outline"
          >
            <span aria-hidden="true">☷</span>
          </button>
        </aside>
        <div className="canvas-area">
          <WorkflowCanvas
            document={state.document}
            selectedId={state.selectedId}
            onSelect={select}
            onMove={move}
          />
          {palette && (
            <section className="activity-panel" aria-label="Activity catalog">
              <div className="panel-heading">
                <h2>Activities</h2>
                <button
                  aria-label="Close activities"
                  onClick={() => setPalette(false)}
                >
                  ×
                </button>
              </div>
              <label htmlFor="activity-search">Search activities</label>
              <input
                id="activity-search"
                ref={searchInput}
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <p className="support-copy">
                Select a step with one outgoing path to insert after it.
              </p>
              {activityKinds
                .filter((item) =>
                  item.label
                    .toLowerCase()
                    .includes(search.trim().toLowerCase()),
                )
                .map((item) => (
                  <button
                    className="activity-card"
                    aria-label={`Add ${item.kind}`}
                    key={item.kind}
                    disabled={!canInsert || pending}
                    onClick={() => {
                      dispatch({
                        type: "insert",
                        kind: item.kind,
                        afterId: selected.id,
                      });
                      setProperties(true);
                      setPalette(false);
                      setNotice(`${item.label} added in this tab.`);
                    }}
                  >
                    <strong>Add {item.kind}</strong>
                    <span>{item.detail}</span>
                  </button>
                ))}
              {!activityKinds.some((item) =>
                item.label.toLowerCase().includes(search.trim().toLowerCase()),
              ) && <p>No activities match your search.</p>}
              {!canInsert && (
                <p className="support-copy">
                  Choose a step with one outgoing path in the outline. Limit: 50
                  steps.
                </p>
              )}
              <p className="support-copy">
                Diagram blocks only. Activity configuration and execution will
                follow.
              </p>
            </section>
          )}
          {outline && (
            <nav className="outline-panel" aria-label="Workflow outline">
              <div className="panel-heading">
                <h2>Workflow outline</h2>
                <button
                  aria-label="Close outline"
                  onClick={() => setOutline(false)}
                >
                  ×
                </button>
              </div>
              <ol>
                {state.document.nodes.map((node) => (
                  <li key={node.id}>
                    <button
                      aria-label={`Select ${node.label}`}
                      aria-current={
                        selected.id === node.id ? "step" : undefined
                      }
                      onClick={() => select(node.id)}
                    >
                      {node.label}
                      <small>{displayKind(node)}</small>
                    </button>
                  </li>
                ))}
              </ol>
            </nav>
          )}
        </div>
        {properties && (
          <aside className="properties-panel" aria-label="Step properties">
            <div className="panel-heading">
              <span className="eyebrow">STEP PROPERTIES</span>
              <button
                aria-label="Close properties"
                onClick={() => setProperties(false)}
              >
                ×
              </button>
            </div>
            <h2>{selected.label}</h2>
            <p className="kind-label">{displayKind(selected)}</p>
            <div className="property-tabs">
              <strong>Setup</strong>
              <span>Inputs later</span>
              <span>Outputs later</span>
            </div>
            {selected.kind === "decision" && (
              <div className="decision-intro">
                <h3>Route the approval result</h3>
                <p>Choose what happens after the approver responds.</p>
                <label>Condition</label>
                <div className="condition-value">Approval task › Outcome</div>
              </div>
            )}
            <form onSubmit={apply} noValidate>
              <label htmlFor="step-name">Step name</label>
              <input
                id="step-name"
                value={draft.label}
                maxLength={80}
                onChange={(event) => changeDraft("label", event.target.value)}
                aria-invalid={!!error}
                aria-describedby={error ? "property-error" : undefined}
              />
              <label htmlFor="step-description">Description</label>
              <textarea
                id="step-description"
                value={draft.description}
                maxLength={240}
                onChange={(event) =>
                  changeDraft("description", event.target.value)
                }
              />
              {error && (
                <p id="property-error" className="error" role="alert">
                  {error}
                </p>
              )}
              <button className="apply-button" type="submit">
                Apply changes
              </button>
            </form>
            {!!outgoing.length && (
              <section className="outgoing-paths">
                <h3 className="eyebrow">OUTGOING PATHS</h3>
                {outgoing.map((edge) => (
                  <div
                    className={`route-card ${edge.label === "Rejected" ? "rejected" : ""}`}
                    key={edge.id}
                  >
                    <strong>{edge.label ?? "Next step"}</strong>
                    <span>{nodeLabel(edge.target)}</span>
                  </div>
                ))}
              </section>
            )}
            <fieldset className="move-controls">
              <legend>Move selected step</legend>
              <div>
                {[
                  { title: "left", dx: -20, dy: 0, symbol: "←" },
                  { title: "up", dx: 0, dy: -20, symbol: "↑" },
                  { title: "down", dx: 0, dy: 20, symbol: "↓" },
                  { title: "right", dx: 20, dy: 0, symbol: "→" },
                ].map((item) => (
                  <button
                    type="button"
                    key={item.title}
                    aria-label={`Move step ${item.title}`}
                    onClick={() => move(selected.id, item.dx, item.dy)}
                  >
                    {item.symbol}
                  </button>
                ))}
              </div>
            </fieldset>
            <p className="support-copy">
              Labels and layout only. Rules, assignments and activity settings
              are not connected.
            </p>
          </aside>
        )}
        <aside className="execution-rail" aria-label="Execution tools">
          <button
            disabled
            title="Workflow execution is not connected"
            aria-label="Run workflow"
          >
            ▷
          </button>
          <button
            disabled
            title="Debugger is not connected"
            aria-label="Open debugger"
          >
            ▣
          </button>
        </aside>
      </main>
      <footer className="editor-status">
        <span>Local authoring · backend not connected</span>
        <span className="live-notice" role="status" aria-live="polite">
          {notice}
        </span>
        <span>Dev sample</span>
      </footer>
    </div>
  );
}
