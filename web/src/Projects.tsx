import { useEffect, useMemo, useRef, useState } from "react";
import WorkflowCanvas from "./Canvas";
import { editorReducer } from "./model";
import type { DesignerDocument, EditorAction, EditorState } from "./model";
import { createProjectClient, ProjectApiError } from "./project-api";
import type {
  ProjectConnection,
  ProjectRevision,
  ProjectSummary,
} from "./project-api";
import {
  listWorkflows,
  projectContentForSave,
  projectDocument,
  withDesignerLayout,
} from "./project-projection";
import {
  createTemplateClient,
  publishBlockedByValidation,
  type TemplateClient,
  type TemplateInfo,
  type ValidationIssue,
} from "./template-api";
import {
  canDeleteStep,
  canInsertAfter,
  canReorderStep,
  commitDocument,
  deleteDesignerStep,
  reorderDesignerStep,
  sameTopology,
} from "./workflow-edit";
import {
  acquireEntraAccessToken,
  fetchBrowserAuthConfig,
  type BrowserAuthConfig,
} from "./entra-auth";
import "./projects.css";

type Client = ReturnType<typeof createProjectClient>;
type Draft = { label: string; description: string };
type Session = {
  client: Client;
  templates: TemplateClient;
  organizationId: string;
  environmentId: string;
  token: string;
};

export type DesignerPublishGate = {
  connection: ProjectConnection | null;
  projectId: string | null;
  headRevision: number | null;
  workflowId: string | null;
  validationIssues: ValidationIssue[];
  publishBlocked: boolean;
};

export type ProjectsProps = {
  onClose: () => void;
  /** Optional 1C release controls mount point (publish/activate/start). */
  releaseControls?: React.ReactNode;
  onPublishGateChange?: (gate: DesignerPublishGate) => void;
};

export { publishBlockedByValidation };
export type { ValidationIssue };

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

const emptyAssignments = (slots: string[]) =>
  Object.fromEntries(slots.map((slot) => [slot, ""]));

export default function Projects({
  onClose,
  releaseControls,
  onPublishGateChange,
}: ProjectsProps) {
  const [organizationId, setOrganizationId] = useState("");
  const [environmentId, setEnvironmentId] = useState("dev");
  const [token, setToken] = useState("");
  const [browserAuth, setBrowserAuth] = useState<BrowserAuthConfig>({
    enabled: false,
  });
  const [session, setSession] = useState<Session | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [templateId, setTemplateId] = useState("employee-onboarding");
  const [draftProjectId, setDraftProjectId] = useState("");
  const [draftName, setDraftName] = useState("");
  const [assignments, setAssignments] = useState<Record<string, string>>({});
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
  const [validationIssues, setValidationIssues] = useState<ValidationIssue[]>(
    [],
  );
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
  const executableDirty = useMemo(
    () =>
      Object.entries(editors).some(([id, state]) => {
        const original = own(baselines, id);
        return original ? !sameTopology(original, state.document) : false;
      }),
    [editors, baselines],
  );
  const selectedTemplate =
    templates.find((item) => item.template_id === templateId) ?? null;
  const canInsert = selected
    ? canInsertAfter(editor!.document, selected.id)
    : false;
  const canDelete = selected
    ? canDeleteStep(editor!.document, selected.id)
    : false;
  const canMoveEarlier = selected
    ? canReorderStep(editor!.document, selected.id, "earlier")
    : false;
  const canMoveLater = selected
    ? canReorderStep(editor!.document, selected.id, "later")
    : false;
  const publishBlocked = publishBlockedByValidation(validationIssues);

  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  useEffect(() => {
    let cancelled = false;
    void fetchBrowserAuthConfig().then((config) => {
      if (!cancelled) setBrowserAuth(config);
    });
    return () => {
      cancelled = true;
    };
  }, []);
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
    if (!onPublishGateChange) return;
    onPublishGateChange({
      connection: session
        ? {
            organizationId: session.organizationId,
            environmentId: session.environmentId,
            token: session.token,
          }
        : null,
      projectId: snapshot?.project_id ?? null,
      headRevision: snapshot?.revision ?? null,
      workflowId: workflowId || null,
      validationIssues,
      publishBlocked,
    });
  }, [
    onPublishGateChange,
    session,
    snapshot,
    workflowId,
    validationIssues,
    publishBlocked,
  ]);

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
    setValidationIssues([]);
    setError("");
    setNotice("");
  }
  function disconnect() {
    generation.current += 1;
    setSession(null);
    setProjects([]);
    setTemplates([]);
    setToken("");
    resetProject();
  }
  async function connectWith(
    connection: ProjectConnection,
    ticket: number,
    noticeText: string,
  ) {
    if (connection.environmentId !== "dev") {
      setError("Only the dev environment can be edited in this increment.");
      return;
    }
    const client = createProjectClient(connection);
    const templateClient = createTemplateClient(connection);
    const [result, listed] = await Promise.all([
      client.listProjects(),
      templateClient.listTemplates(),
    ]);
    if (ticket !== generation.current) return;
    setSession({
      client,
      templates: templateClient,
      organizationId: connection.organizationId,
      environmentId: connection.environmentId,
      token: connection.token,
    });
    setOrganizationId(connection.organizationId);
    setEnvironmentId(connection.environmentId);
    setProjects(result);
    setTemplates(listed);
    const first = listed[0];
    if (first) {
      setTemplateId(first.template_id);
      setAssignments(emptyAssignments(first.assignment_slots));
    }
    setToken("");
    setNotice(noticeText);
  }
  async function connect(event: React.FormEvent) {
    event.preventDefault();
    const ticket = begin();
    if (ticket === null) return;
    try {
      await connectWith(
        { organizationId, environmentId, token },
        ticket,
        "Connected to Dev. Create a starter or open a project to edit the supported workflow.",
      );
    } catch (caught) {
      if (ticket === generation.current) setError(failure(caught));
    } finally {
      end(ticket);
    }
  }
  async function signInWithMicrosoft() {
    if (!browserAuth.enabled) return;
    const ticket = begin();
    if (ticket === null) return;
    try {
      const acquired = await acquireEntraAccessToken(browserAuth);
      if (ticket !== generation.current) return;
      await connectWith(
        {
          organizationId: acquired.organizationId,
          environmentId: acquired.environmentId,
          token: acquired.accessToken,
        },
        ticket,
        "Connected with Microsoft Entra. Create a starter or open a project to edit the supported workflow.",
      );
    } catch (caught) {
      if (ticket === generation.current) {
        setError(
          caught instanceof Error && caught.message.startsWith("Entra")
            ? caught.message
            : "Microsoft sign-in did not complete. You can paste a token instead.",
        );
      }
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
    setValidationIssues([]);
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
  async function createFromTemplate(event: React.FormEvent) {
    event.preventDefault();
    if (!session || !selectedTemplate) return;
    const ticket = begin();
    if (ticket === null) return;
    try {
      const payloadAssignments = Object.fromEntries(
        selectedTemplate.assignment_slots.map((slot) => [
          slot,
          {
            rule: "any_one" as const,
            user_ids: [assignments[slot]?.trim()].filter(Boolean) as string[],
            group_ids: [] as string[],
          },
        ]),
      );
      const created = await session.templates.createProject(templateId, {
        project_id: draftProjectId.trim(),
        name: draftName.trim(),
        assignments: payloadAssignments,
      });
      if (ticket !== generation.current) return;
      setProjects((previous) => {
        if (previous.some((item) => item.project_id === created.project_id))
          return previous.map((item) =>
            item.project_id === created.project_id
              ? { ...item, head_revision: created.revision, name: draftName }
              : item,
          );
        return [
          {
            project_id: created.project_id,
            name: draftName.trim(),
            head_revision: created.revision,
          },
          ...previous,
        ];
      });
      adopt(created);
      setNotice(
        `Created ${selectedTemplate.name} draft at revision ${created.revision}.`,
      );
      setDraftProjectId("");
      setDraftName("");
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
    setValidationIssues([]);
  }
  function replaceDocument(next: DesignerDocument, selectedId: string) {
    if (busyRef.current || !editor) return;
    setEditors((previous) => {
      const current = own(previous, workflowId);
      if (!current) return previous;
      return {
        ...previous,
        [workflowId]: commitDocument(
          current.past,
          current.document,
          next,
          selectedId,
        ),
      };
    });
    setValidationIssues([]);
    setError("");
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
      executableDirty
        ? "Step updated locally. Save project to create a revision."
        : "Presentation updated locally. Save project to create a revision.",
    );
  }
  function insertStep(kind: "approval" | "timer") {
    if (!selected || !canInsertAfter(editor!.document, selected.id)) {
      setError(
        "Select a step with exactly one outgoing path to insert after it. Decision and end steps cannot be insertion points.",
      );
      return;
    }
    edit({ type: "insert", kind, afterId: selected.id });
    setNotice(
      kind === "approval"
        ? "Approval step inserted locally. Save project to update the executable workflow."
        : "Wait step inserted locally. Save project to update the executable workflow.",
    );
  }
  function removeStep() {
    if (!editor || !selected) return;
    const next = deleteDesignerStep(editor.document, selected.id);
    if (!next) {
      setError(
        "Only approval, wait, and other mid-chain steps with one incoming and one outgoing path can be deleted. Start and end are required.",
      );
      return;
    }
    replaceDocument(next, selected.id);
    setNotice(
      "Step removed locally. Save project to update the executable workflow.",
    );
  }
  function moveStep(direction: "earlier" | "later") {
    if (!editor || !selected) return;
    const next = reorderDesignerStep(
      editor.document,
      selected.id,
      direction,
    );
    if (!next) {
      setError(
        "This step cannot be reordered. Move only mid-chain steps along a single path.",
      );
      return;
    }
    replaceDocument(next, selected.id);
    setNotice(
      "Step order updated locally. Save project to update the executable workflow.",
    );
  }
  async function save() {
    if (!session || !snapshot || pending || !appliedChanges || conflict) return;
    const ticket = begin();
    if (ticket === null) return;
    try {
      let staged = snapshot;
      let executable = false;
      for (const [id, state] of Object.entries(editors)) {
        const original = own(baselines, id);
        if (
          !original ||
          JSON.stringify(state.document) === JSON.stringify(original)
        )
          continue;
        if (sameTopology(original, state.document)) {
          staged = {
            ...staged,
            content: withDesignerLayout(staged, id, state.document),
          };
        } else {
          const result = projectContentForSave(
            staged,
            id,
            state.document,
            original,
          );
          staged = { ...staged, content: result.content };
          executable = executable || result.executable;
        }
      }
      const saved = await session.client.saveProject(
        snapshot.project_id,
        snapshot.revision,
        staged.content,
        executable ? "Update workflow steps" : "Update designer layout",
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
  async function validate() {
    if (!session || !snapshot || pending) return;
    const ticket = begin();
    if (ticket === null) return;
    try {
      let staged = snapshot.content;
      for (const [id, state] of Object.entries(editors)) {
        const original = own(baselines, id);
        if (
          !original ||
          JSON.stringify(state.document) === JSON.stringify(original)
        )
          continue;
        const result = projectContentForSave(
          { ...snapshot, content: staged },
          id,
          state.document,
          original,
        );
        staged = result.content;
      }
      const issues = await session.templates.validateProject(
        snapshot.project_id,
        staged,
      );
      if (ticket !== generation.current) return;
      setValidationIssues(issues);
      setNotice(
        issues.length
          ? `Validation found ${issues.length} issue${issues.length === 1 ? "" : "s"}. Publish stays blocked until they are resolved.`
          : "Validation passed. Publish can proceed when release controls are available.",
      );
    } catch (caught) {
      if (ticket === generation.current) setError(failure(caught));
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
        Connected Dev authoring · create starters, edit supported approval/wait
        steps, validate, and save executable revisions. Publish stays blocked
        while validation reports issues.
      </p>
      {!session && (
        <form className="connection-form" onSubmit={connect}>
          <h2>Connect to your Dev projects</h2>
          <p>
            Paste a host token (local-demo) or, when Entra is configured on the
            host, sign in with Microsoft. Tokens stay in memory and clear on
            disconnect.
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
            {browserAuth.enabled && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void signInWithMicrosoft()}
              >
                Sign in with Microsoft
              </button>
            )}
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
            <form className="template-create" onSubmit={createFromTemplate}>
              <h3>Create from template</h3>
              <fieldset disabled={busy || dirty}>
                <label htmlFor="project-template">Starter</label>
                <select
                  id="project-template"
                  value={templateId}
                  onChange={(event) => {
                    const next = templates.find(
                      (item) => item.template_id === event.target.value,
                    );
                    setTemplateId(event.target.value);
                    if (next)
                      setAssignments(emptyAssignments(next.assignment_slots));
                  }}
                >
                  {templates.map((template) => (
                    <option
                      key={template.template_id}
                      value={template.template_id}
                    >
                      {template.name}
                    </option>
                  ))}
                </select>
                {selectedTemplate && <p>{selectedTemplate.description}</p>}
                <label htmlFor="template-project-id">Project ID</label>
                <input
                  id="template-project-id"
                  value={draftProjectId}
                  onChange={(event) => setDraftProjectId(event.target.value)}
                  autoComplete="off"
                  maxLength={128}
                  required
                />
                <label htmlFor="template-project-name">Project name</label>
                <input
                  id="template-project-name"
                  value={draftName}
                  onChange={(event) => setDraftName(event.target.value)}
                  autoComplete="off"
                  maxLength={200}
                  required
                />
                {selectedTemplate?.assignment_slots.map((slot) => (
                  <div key={slot}>
                    <label htmlFor={`template-slot-${slot}`}>
                      {slot} assignee user ID
                    </label>
                    <input
                      id={`template-slot-${slot}`}
                      value={assignments[slot] ?? ""}
                      onChange={(event) =>
                        setAssignments((previous) => ({
                          ...previous,
                          [slot]: event.target.value,
                        }))
                      }
                      autoComplete="off"
                      maxLength={128}
                      required
                    />
                  </div>
                ))}
                <button className="project-primary" type="submit">
                  Create draft
                </button>
              </fieldset>
              {dirty && (
                <p>
                  Save or discard project edits before creating another draft.
                </p>
              )}
            </form>
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
                  Create an onboarding or purchase starter, or open a Dev
                  project to edit supported steps, labels and layout.
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
                      {executableDirty ? " · Executable edits pending" : ""}
                      {publishBlocked ? " · Publish blocked" : ""}
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
                      disabled={busy || pending}
                      onClick={() => {
                        void validate();
                      }}
                    >
                      Validate
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
                {releaseControls && (
                  <div
                    className="project-release-slot"
                    data-publish-blocked={publishBlocked ? "true" : "false"}
                    aria-label="Release controls"
                  >
                    {releaseControls}
                  </div>
                )}
                {validationIssues.length > 0 && (
                  <div
                    className="project-validation"
                    role="status"
                    aria-label="Validation issues"
                  >
                    <h3>Validation issues</h3>
                    <ul>
                      {validationIssues.map((issue) => (
                        <li key={`${issue.path}:${issue.code}:${issue.message}`}>
                          <code>{issue.path || "/"}</code> · {issue.code}:{" "}
                          {issue.message}
                        </li>
                      ))}
                    </ul>
                    <p>
                      Publish is blocked while these authoring issues remain.
                    </p>
                  </div>
                )}
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
                      contextLabel={
                        executableDirty
                          ? "Executable edits"
                          : "Connected authoring"
                      }
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
                    <p>
                      {selected.kind} · {selected.id}
                    </p>
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
                    <fieldset disabled={busy} className="connected-steps">
                      <legend>Supported steps</legend>
                      <button
                        type="button"
                        disabled={!canInsert}
                        onClick={() => insertStep("approval")}
                      >
                        Insert approval
                      </button>
                      <button
                        type="button"
                        disabled={!canInsert}
                        onClick={() => insertStep("timer")}
                      >
                        Insert wait
                      </button>
                      <button
                        type="button"
                        disabled={!canDelete}
                        onClick={removeStep}
                      >
                        Delete step
                      </button>
                      <button
                        type="button"
                        disabled={!canMoveEarlier}
                        onClick={() => moveStep("earlier")}
                      >
                        Move earlier
                      </button>
                      <button
                        type="button"
                        disabled={!canMoveLater}
                        onClick={() => moveStep("later")}
                      >
                        Move later
                      </button>
                    </fieldset>
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
