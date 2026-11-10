import { useEffect, useMemo, useRef, useState } from "react";
import WorkflowCanvas from "./Canvas";
import { editorReducer } from "./model";
import type { DesignerDocument, EditorAction, EditorState } from "./model";
import { createProjectClient, ProjectApiError } from "./project-api";
import type { ProjectRevision, ProjectSummary } from "./project-api";
import {
  listWorkflows,
  projectDocument,
  withDesignerLayout,
} from "./project-projection";
import "./projects.css";

type Client = ReturnType<typeof createProjectClient>;
type Draft = { label: string; description: string };
type Session = {
  client: Client;
  organizationId: string;
  environmentId: string;
};

const failureMessages: Record<string, string> = {
  invalid_request:
    "Check the organization, Dev environment, access token and project size limits, then try again. Your edits are still here.",
  unauthorized:
    "Your access token is missing or expired. Reconnect with a valid token. Your edits are still here.",
  forbidden:
    "You do not have permission for this project. Your edits are still here.",
  not_found:
    "This project could not be found in the selected environment. Your edits are still here.",
  conflict:
    "Save conflict. Your edits are still here. Reload latest to discard local changes.",
  unavailable:
    "The project service is unavailable. Your edits are still here. Try again when it is available.",
  invalid_response:
    "The project service returned an unsupported response. Your edits are still here.",
};

function failure(error: unknown) {
  return (
    failureMessages[
      error instanceof ProjectApiError ? error.code : "unavailable"
    ] ?? failureMessages.unavailable
  );
}

function editorFor(document: DesignerDocument): EditorState {
  return { document, past: [], future: [], selectedId: document.nodes[0].id };
}

function own<T>(
  record: Record<string, T> | undefined,
  key: string,
): T | undefined {
  return record && Object.hasOwn(record, key) ? record[key] : undefined;
}

export default function Projects({ onClose }: { onClose: () => void }) {
  const [organizationId, setOrganizationId] = useState("");
  const [environmentId, setEnvironmentId] = useState("dev");
  const [token, setToken] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [snapshot, setSnapshot] = useState<ProjectRevision | null>(null);
  const [workflowId, setWorkflowId] = useState("");
  const [editors, setEditors] = useState<Record<string, EditorState>>({});
  const [baselines, setBaselines] = useState<Record<string, DesignerDocument>>(
    {},
  );
  const [drafts, setDrafts] = useState<Record<string, Record<string, Draft>>>(
    {},
  );
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const generation = useRef(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [confirmation, setConfirmation] = useState<(() => void) | null>(null);
  const editor = own(editors, workflowId);
  const selected = editor?.document.nodes.find(
    (node) => node.id === editor.selectedId,
  );
  const draft = selected
    ? (own(own(drafts, workflowId), selected.id) ?? selected)
    : null;
  const pending = useMemo(
    () =>
      Object.entries(drafts).some(([workflow, nodes]) =>
        Object.entries(nodes).some(([id, value]) => {
          const node = own(editors, workflow)?.document.nodes.find(
            (item) => item.id === id,
          );
          return (
            node &&
            (node.label !== value.label ||
              node.description !== value.description)
          );
        }),
      ),
    [drafts, editors],
  );
  const appliedChanges = useMemo(
    () =>
      Object.entries(editors).some(
        ([id, state]) =>
          JSON.stringify(state.document) !== JSON.stringify(own(baselines, id)),
      ),
    [editors, baselines],
  );
  const dirty = pending || appliedChanges;

  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  useEffect(() => {
    if (!dirty) return;
    const protect = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty]);

  function begin() {
    if (busyRef.current) return null;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    return ++generation.current;
  }
  function end(ticket: number) {
    if (ticket !== generation.current) return;
    busyRef.current = false;
    setBusy(false);
  }
  function guard(action: () => void) {
    if (busyRef.current) return;
    if (dirty) setConfirmation(() => action);
    else action();
  }
  function resetProject() {
    setSnapshot(null);
    setWorkflowId("");
    setEditors({});
    setBaselines({});
    setDrafts({});
    setConflict(false);
    setError("");
    setNotice("");
  }
  function disconnect() {
    generation.current += 1;
    setSession(null);
    setProjects([]);
    setToken("");
    resetProject();
  }
  async function connect(event: React.FormEvent) {
    event.preventDefault();
    const ticket = begin();
    if (ticket === null) return;
    try {
      if (environmentId !== "dev") {
        setError("Only the dev environment can be edited in this increment.");
        return;
      }
      const client = createProjectClient({
        organizationId,
        environmentId,
        token,
      });
      const result = await client.listProjects();
      if (ticket !== generation.current) return;
      setSession({ client, organizationId, environmentId });
      setProjects(result);
      setToken("");
      setNotice("Connected to Dev. Open a project to edit its presentation.");
    } catch (caught) {
      if (ticket === generation.current) setError(failure(caught));
    } finally {
      end(ticket);
    }
  }
  function adopt(
    revision: ProjectRevision,
    selectedWorkflow?: string,
    selectedNode?: string,
  ) {
    const workflows = listWorkflows(revision);
    const id = selectedWorkflow ?? workflows[0]?.id;
    if (!id) throw new Error("Unsupported project");
    const document = projectDocument(revision, id);
    setSnapshot(revision);
    setWorkflowId(id);
    const nextEditor = editorFor(document);
    if (selectedNode && document.nodes.some((node) => node.id === selectedNode))
      nextEditor.selectedId = selectedNode;
    setEditors({ [id]: nextEditor });
    setBaselines({ [id]: document });
    setDrafts({});
    setConflict(false);
  }
  async function openProject(id: string, retainWorkflow = false) {
    if (!session) return;
    const ticket = begin();
    if (ticket === null) return;
    try {
      const revision = await session.client.readProject(id);
      if (ticket !== generation.current) return;
      try {
        adopt(
          revision,
          retainWorkflow ? workflowId : undefined,
          retainWorkflow ? editor?.selectedId : undefined,
        );
      } catch {
        setError(
          "This workflow is unsupported and cannot be shown safely in the connected editor. Supported views contain up to 50 start, end, approval, decision, wait, set-data or subworkflow steps and no error routes. Your current edits are still here.",
        );
        return;
      }
      setNotice(`Loaded project revision ${revision.revision}.`);
    } catch (caught) {
      if (ticket === generation.current) setError(failure(caught));
    } finally {
      end(ticket);
    }
  }
  function switchWorkflow(id: string) {
    if (busyRef.current || !snapshot || id === workflowId) return;
    if (pending) {
      setError("Apply pending property edits before switching workflows.");
      return;
    }
    try {
      if (!own(editors, id)) {
        const document = projectDocument(snapshot, id);
        setEditors((previous) => ({ ...previous, [id]: editorFor(document) }));
        setBaselines((previous) => ({ ...previous, [id]: document }));
      }
      setWorkflowId(id);
      setError("");
    } catch {
      setError(
        "This workflow is unsupported and cannot be shown safely in the connected editor. Your current edits are still here.",
      );
    }
  }
  function edit(action: EditorAction) {
    if (busyRef.current || !editor) return;
    if ((action.type === "undo" || action.type === "redo") && pending) {
      setError("Apply pending property edits before using undo or redo.");
      return;
    }
    setEditors((previous) => {
      const current = own(previous, workflowId);
      return current
        ? { ...previous, [workflowId]: editorReducer(current, action) }
        : previous;
    });
  }
  function property(field: keyof Draft, value: string) {
    if (!selected || !draft || busyRef.current) return;
    setDrafts((previous) => ({
      ...previous,
      [workflowId]: {
        ...own(previous, workflowId),
        [selected.id]: {
          label: draft.label,
          description: draft.description,
          [field]: value,
        },
      },
    }));
    setError("");
  }
  function apply(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !draft || busyRef.current) return;
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
    edit({
      type: "edit",
      id: selected.id,
      label: draft.label,
      description: draft.description,
    });
    setDrafts((previous) => {
      const nodes = { ...own(previous, workflowId) };
      delete nodes[selected.id];
      return { ...previous, [workflowId]: nodes };
    });
    setError("");
    setNotice(
      "Presentation updated locally. Save project to create a revision.",
    );
  }
  async function save() {
    if (!session || !snapshot || pending || !appliedChanges || conflict) return;
    const ticket = begin();
    if (ticket === null) return;
    try {
      let staged = snapshot;
      for (const [id, state] of Object.entries(editors)) {
        if (
          JSON.stringify(state.document) !== JSON.stringify(own(baselines, id))
        ) {
          staged = {
            ...staged,
            content: withDesignerLayout(staged, id, state.document),
          };
        }
      }
      const saved = await session.client.saveProject(
        snapshot.project_id,
        snapshot.revision,
        staged.content,
        "Update designer layout",
      );
      if (ticket !== generation.current) return;
      adopt(saved, workflowId, editor?.selectedId);
      setNotice(`Saved project revision ${saved.revision}.`);
      setProjects((previous) =>
        previous.map((project) =>
          project.project_id === saved.project_id
            ? { ...project, head_revision: saved.revision }
            : project,
        ),
      );
    } catch (caught) {
      if (ticket !== generation.current) return;
      if (caught instanceof ProjectApiError && caught.code === "conflict")
        setConflict(true);
      setError(failure(caught));
    } finally {
      end(ticket);
    }
  }

  return (
    <div className="connected-projects">
      <header className="project-header">
        <div>
          <span className="project-eyebrow">NEXUS FLOW DESIGNER</span>
          <h1>Projects</h1>
        </div>
        <div className="project-header-actions">
          {session && (
            <button disabled={busy} onClick={() => guard(disconnect)}>
              Disconnect
            </button>
          )}
          <button disabled={busy} onClick={() => guard(onClose)}>
            Close projects
          </button>
        </div>
      </header>
      <p className="project-boundary">
        Connected Dev authoring · labels, descriptions and layout only. Workflow
        execution, assignments and mappings are unchanged.
      </p>
      {!session && (
        <form className="connection-form" onSubmit={connect}>
          <h2>Connect to your Dev projects</h2>
          <p>
            Use a token from your configured host. It stays in memory and is
            cleared when you disconnect. Browser sign-in is not connected yet.
          </p>
          <fieldset disabled={busy}>
            <label htmlFor="project-org">Organization ID</label>
            <input
              id="project-org"
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
              autoComplete="off"
              maxLength={128}
            />
            <label htmlFor="project-environment">Environment ID</label>
            <input
              id="project-environment"
              value={environmentId}
              onChange={(event) => setEnvironmentId(event.target.value)}
              autoComplete="off"
              maxLength={128}
            />
            <label htmlFor="project-token">Access token</label>
            <input
              id="project-token"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <button className="project-primary" type="submit">
              Connect
            </button>
          </fieldset>
        </form>
      )}
      {(error || conflict) && (
        <p className="project-error" role="alert">
          {conflict ? failureMessages.conflict : error}
        </p>
      )}
      <p className="project-notice" role="status" aria-live="polite">
        {busy ? "Working…" : notice}
      </p>
      {session && (
        <div className="project-content">
          <aside className="project-list" aria-label="Project list">
            <h2>Your projects</h2>
            <p>
              {session.organizationId} / {session.environmentId}
            </p>
            <p>Showing the first 100 projects.</p>
            {!projects.length && (
              <p>No projects are available in this environment.</p>
            )}
            <ul>
              {projects.map((project) => (
                <li key={project.project_id}>
                  <strong>{project.name}</strong>
                  <span>
                    {project.project_id} · revision {project.head_revision}
                  </span>
                  <button
                    aria-label={`Open ${project.project_id}`}
                    disabled={busy}
                    onClick={() =>
                      guard(() => {
                        void openProject(project.project_id);
                      })
                    }
                  >
                    Open project
                  </button>
                </li>
              ))}
            </ul>
          </aside>
          <section
            className="project-editor"
            aria-label="Connected project editor"
          >
            {!snapshot && (
              <div className="project-empty">
                <h2>Choose a project</h2>
                <p>
                  Open a Dev project to adjust step labels, descriptions and
                  positions. Unsupported workflows stay unchanged.
                </p>
              </div>
            )}
            {snapshot && editor && selected && draft && (
              <>
                <div className="project-editor-toolbar">
                  <div>
                    <h2>
                      {projects.find(
                        (item) => item.project_id === snapshot.project_id,
                      )?.name ?? snapshot.project_id}
                    </h2>
                    <p>
                      <span>Revision {snapshot.revision}</span> ·{" "}
                      <span>
                        {dirty ? "Unsaved changes" : "All changes saved"}
                      </span>
                    </p>
                  </div>
                  <div className="project-editor-actions">
                    <button
                      disabled={busy || !editor.past.length || pending}
                      onClick={() => edit({ type: "undo" })}
                    >
                      Undo
                    </button>
                    <button
                      disabled={busy || !editor.future.length || pending}
                      onClick={() => edit({ type: "redo" })}
                    >
                      Redo
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        guard(() => {
                          void openProject(snapshot.project_id, true);
                        })
                      }
                    >
                      Reload latest
                    </button>
                    <button
                      className="project-primary"
                      disabled={busy || !appliedChanges || pending || conflict}
                      onClick={() => {
                        void save();
                      }}
                    >
                      Save project
                    </button>
                  </div>
                </div>
                <div className="project-workflow-select">
                  <label htmlFor="project-workflow">Workflow</label>
                  <select
                    id="project-workflow"
                    disabled={busy || pending}
                    value={workflowId}
                    onChange={(event) => switchWorkflow(event.target.value)}
                  >
                    {listWorkflows(snapshot).map((workflow) => (
                      <option key={workflow.id} value={workflow.id}>
                        {workflow.id}
                      </option>
                    ))}
                  </select>
                  {pending && (
                    <span>
                      Apply pending property edits before saving or switching
                      workflows.
                    </span>
                  )}
                </div>
                <div className="connected-workspace">
                  <div className="connected-canvas" inert={busy}>
                    <WorkflowCanvas
                      title={workflowId}
                      contextLabel="Presentation only"
                      document={editor.document}
                      selectedId={editor.selectedId}
                      onSelect={(id) => edit({ type: "select", id })}
                      onMove={(id, dx, dy) =>
                        edit({ type: "move", id, dx, dy })
                      }
                    />
                  </div>
                  <aside
                    className="connected-properties"
                    aria-label="Step properties"
                  >
                    <h3>{selected.label}</h3>
                    <p>Presentation only · {selected.id}</p>
                    <form onSubmit={apply}>
                      <fieldset disabled={busy}>
                        <label htmlFor="connected-step-name">Step name</label>
                        <input
                          id="connected-step-name"
                          value={draft.label}
                          maxLength={80}
                          onChange={(event) =>
                            property("label", event.target.value)
                          }
                        />
                        <label htmlFor="connected-description">
                          Description
                        </label>
                        <textarea
                          id="connected-description"
                          value={draft.description}
                          maxLength={240}
                          onChange={(event) =>
                            property("description", event.target.value)
                          }
                        />
                        <button type="submit">Apply changes</button>
                      </fieldset>
                    </form>
                    <fieldset disabled={busy} className="connected-move">
                      <legend>Move selected step</legend>
                      {[
                        { label: "left", dx: -20, dy: 0, icon: "←" },
                        { label: "up", dx: 0, dy: -20, icon: "↑" },
                        { label: "down", dx: 0, dy: 20, icon: "↓" },
                        { label: "right", dx: 20, dy: 0, icon: "→" },
                      ].map((direction) => (
                        <button
                          key={direction.label}
                          aria-label={`Move step ${direction.label}`}
                          onClick={() =>
                            edit({
                              type: "move",
                              id: selected.id,
                              dx: direction.dx,
                              dy: direction.dy,
                            })
                          }
                        >
                          {direction.icon}
                        </button>
                      ))}
                    </fieldset>
                    <nav
                      className="connected-outline"
                      aria-label="Workflow outline"
                    >
                      <h3>Workflow outline</h3>
                      <ol>
                        {editor.document.nodes.map((node) => (
                          <li key={node.id}>
                            <button
                              disabled={busy}
                              aria-label={`Inspect ${node.label}`}
                              aria-current={
                                node.id === selected.id ? "step" : undefined
                              }
                              onClick={() =>
                                edit({ type: "select", id: node.id })
                              }
                            >
                              {node.label}
                            </button>
                          </li>
                        ))}
                      </ol>
                    </nav>
                  </aside>
                </div>
              </>
            )}
          </section>
        </div>
      )}
      {confirmation && (
        <section
          className="discard-confirmation"
          role="alertdialog"
          aria-modal="false"
          aria-label="Discard unsaved changes?"
        >
          <h2>Discard unsaved changes?</h2>
          <p>
            Your applied edits and pending property values will be replaced.
            Keep editing to retain them.
          </p>
          <div>
            <button disabled={busy} onClick={() => setConfirmation(null)}>
              Keep editing
            </button>
            <button
              disabled={busy}
              className="project-discard"
              onClick={() => {
                const action = confirmation;
                setConfirmation(null);
                action();
              }}
            >
              Discard changes
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
