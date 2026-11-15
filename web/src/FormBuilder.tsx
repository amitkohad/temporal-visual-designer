import { createElement, useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  blankFieldDraft,
  bindFormToApproval,
  catalogProjectForms,
  createFormAuthoringClient,
  definitionFromDrafts,
  draftsFromDefinition,
  emptyFormDefinition,
  nextFieldSequence,
  previewFormDefinition,
  readInlineApprovalForm,
  upsertProjectForm,
  validateFormDefinition,
  type ApprovalFormBinding,
  type FormAuthoringClient,
  type FormDefinition,
  type FormFieldDraft,
  type FormIssue,
  type JsonObject,
  type JsonValue,
  type ProjectConnection,
  type ProjectRevision,
} from "./form-api";
import {
  ProjectApiError,
  isIdentifier,
  type ProjectSummary,
} from "./project-api";
import FormRenderer, { draftValuesFromForm } from "./FormRenderer";
import "./forms.css";
import "./tokens.css";

export interface FormBuilderProps {
  onClose: () => void;
  connection?: ProjectConnection;
}

type Session = {
  connection: ProjectConnection;
  client: FormAuthoringClient;
};

const failureMessages: Record<string, string> = {
  invalid_request: "Check the organization, environment, and token values.",
  unauthorized: "The token was rejected. Mint a fresh local-demo token.",
  forbidden: "This identity cannot access forms in that environment.",
  not_found: "The project was not found.",
  conflict: "The project changed. Reload and try again.",
  unavailable: "The host is unavailable. Confirm serve --ui is running.",
  invalid_response: "The host returned an unexpected response.",
};

function messageFor(error: unknown): string {
  if (error instanceof ProjectApiError)
    return failureMessages[error.code] ?? failureMessages.unavailable;
  if (error instanceof Error && error.message) return error.message;
  return failureMessages.unavailable;
}

export default function FormBuilder({ onClose, connection }: FormBuilderProps) {
  const [organizationId, setOrganizationId] = useState(
    connection?.organizationId ?? "local-demo",
  );
  const [environmentId, setEnvironmentId] = useState(
    connection?.environmentId ?? "dev",
  );
  const [token, setToken] = useState(connection?.token ?? "");
  const [session, setSession] = useState<Session | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectId, setProjectId] = useState("");
  const [revision, setRevision] = useState<ProjectRevision | null>(null);
  const [bindings, setBindings] = useState<ApprovalFormBinding[]>([]);
  const [catalog, setCatalog] = useState<FormDefinition[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [formId, setFormId] = useState("new-form");
  const [drafts, setDrafts] = useState<FormFieldDraft[]>([blankFieldDraft(1)]);
  const [issues, setIssues] = useState<FormIssue[]>([]);
  const [bindWorkflowId, setBindWorkflowId] = useState("");
  const [bindNodeId, setBindNodeId] = useState("");
  const [previewValues, setPreviewValues] = useState<JsonObject>({});
  const [previewMode, setPreviewMode] = useState<"task" | "start">("task");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!connection) return;
    void connectWith(connection);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- cold mount from App session
  }, []);

  function begin(): boolean {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(true);
    setError("");
    return true;
  }

  function end(): void {
    busyRef.current = false;
    setBusy(false);
  }

  async function connectWith(next: ProjectConnection) {
    if (!begin()) return;
    try {
      const client = createFormAuthoringClient(next);
      const listed = await client.listProjects();
      setSession({ connection: next, client });
      setProjects(listed);
      setNotice(
        listed.length === 0
          ? "Connected. Create a project in Projects first."
          : `Connected. ${listed.length} project(s) available.`,
      );
      if (listed[0]) {
        await loadProject(client, listed[0].project_id);
      }
    } catch (err) {
      setSession(null);
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    await connectWith({
      organizationId: organizationId.trim(),
      environmentId: environmentId.trim(),
      token: token.trim(),
    });
  }

  function applyCatalog(content: JsonObject) {
    const next = catalogProjectForms(content);
    setCatalog(next.projectForms);
    setBindings(next.bindings);
    if (!bindWorkflowId && next.bindings[0]) {
      setBindWorkflowId(next.bindings[0].workflowId);
      setBindNodeId(next.bindings[0].nodeId);
    }
  }

  async function loadProject(client: FormAuthoringClient, id: string) {
    const snap = await client.readProject(id);
    setProjectId(id);
    setRevision(snap);
    applyCatalog(snap.content);
    const first = catalogProjectForms(snap.content).projectForms[0];
    if (first) {
      selectDefinition(first, `catalog:${first.form_id}`);
    } else {
      const binding = catalogProjectForms(snap.content).bindings.find(
        (item) => item.hasInlineForm,
      );
      if (binding) {
        const inline = readInlineApprovalForm(
          snap.content,
          binding.workflowId,
          binding.nodeId,
        );
        if (inline) {
          selectDefinition(
            inline.definition,
            `inline:${binding.workflowId}/${binding.nodeId}`,
          );
          setBindWorkflowId(binding.workflowId);
          setBindNodeId(binding.nodeId);
          return;
        }
      }
      const blank = emptyFormDefinition("new-form");
      selectDefinition(blank, "catalog:new-form");
    }
  }

  function selectDefinition(form: FormDefinition, key: string) {
    setSelectedKey(key);
    setFormId(form.form_id);
    const nextDrafts = draftsFromDefinition(form);
    setDrafts(nextDrafts.length > 0 ? nextDrafts : [blankFieldDraft(1)]);
    setIssues(validateFormDefinition(form));
    try {
      setPreviewValues(
        draftValuesFromForm(
          previewFormDefinition(form, {}, form.form_id === "new-form" ? 1 : 1),
        ),
      );
    } catch {
      setPreviewValues({});
    }
  }

  function currentDefinition(): FormDefinition {
    return definitionFromDrafts(
      formId.trim() || "new-form",
      drafts,
      selectedKey.startsWith("catalog:")
        ? catalog.find((item) => item.form_id === formId)
        : undefined,
    );
  }

  function updateDraft(index: number, patch: Partial<FormFieldDraft>) {
    setDrafts((current) =>
      current.map((draft, i) => (i === index ? { ...draft, ...patch } : draft)),
    );
  }

  function moveDraft(index: number, delta: number) {
    setDrafts((current) => {
      const target = index + delta;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      const [item] = next.splice(index, 1);
      next.splice(target, 0, item!);
      return next;
    });
  }

  async function saveForm(options?: { bind?: boolean; alsoCatalog?: boolean }) {
    if (!session || !revision || !projectId) return;
    if (!begin()) return;
    try {
      let definition = currentDefinition();
      if (!isIdentifier(definition.form_id)) {
        throw new Error("Form ID must be a stable identifier");
      }
      const found = validateFormDefinition(definition);
      setIssues(found);
      if (found.length > 0) {
        throw new Error(found[0]?.message ?? "Form definition is invalid");
      }
      let content = revision.content;
      if (options?.alsoCatalog !== false) {
        content = upsertProjectForm(content, definition);
      }
      if (options?.bind) {
        if (!bindWorkflowId || !bindNodeId) {
          throw new Error("Choose an approval step to bind");
        }
        content = bindFormToApproval(
          content,
          bindWorkflowId,
          bindNodeId,
          definition,
        );
      }
      const saved = await session.client.saveProject(
        projectId,
        revision.revision,
        content,
        options?.bind
          ? `Bind form ${definition.form_id} to ${bindNodeId}`
          : `Update form ${definition.form_id}`,
      );
      setRevision(saved);
      applyCatalog(saved.content);
      setSelectedKey(`catalog:${definition.form_id}`);
      setNotice(
        options?.bind
          ? `Saved and bound ${definition.form_id} to ${bindNodeId} at revision ${saved.revision}.`
          : `Saved form ${definition.form_id} at revision ${saved.revision}.`,
      );
      selectDefinition(definition, `catalog:${definition.form_id}`);
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  let preview;
  try {
    preview = previewFormDefinition(currentDefinition(), previewValues);
  } catch {
    preview = null;
  }

  return (
    <div className="nf-forms-surface" aria-label="Form builder">
      <header className="nf-forms-header">
        <div>
          <span className="nf-forms-eyebrow">NEXUS FLOW DESIGNER</span>
          <h1>Forms</h1>
        </div>
        <div className="nf-forms-header-actions">
          {session && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setSession(null);
                setRevision(null);
                setProjects([]);
                setNotice("Disconnected.");
              }}
            >
              Disconnect
            </button>
          )}
          <button type="button" disabled={busy} onClick={onClose}>
            Close forms
          </button>
        </div>
      </header>
      <p className="nf-forms-boundary">
        Author managed form schemas, preview the accessible renderer, and bind
        inline definitions onto approval steps. Runtime still uses pinned{" "}
        <code>human_task.form</code> snapshots — not Entra/production
        acceptance.
      </p>
      {!session && (
        <form className="nf-forms-connect" onSubmit={connect}>
          <h2>Connect to author forms</h2>
          <p>
            Use the same Dev host session as Projects. Tokens stay in memory for
            this tab.
          </p>
          <fieldset disabled={busy}>
            <label htmlFor="forms-org">Organization ID</label>
            <input
              id="forms-org"
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
              autoComplete="off"
              maxLength={128}
            />
            <label htmlFor="forms-environment">Environment ID</label>
            <input
              id="forms-environment"
              value={environmentId}
              onChange={(event) => setEnvironmentId(event.target.value)}
              autoComplete="off"
              maxLength={128}
            />
            <label htmlFor="forms-token">Access token</label>
            <input
              id="forms-token"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <button className="nf-form-primary" type="submit">
              Connect
            </button>
          </fieldset>
        </form>
      )}
      {error && (
        <p className="nf-forms-error" role="alert">
          {error}
        </p>
      )}
      <p className="nf-forms-notice" role="status" aria-live="polite">
        {busy ? "Working…" : notice}
      </p>
      {session && (
        <div className="nf-forms-layout">
          <aside className="nf-forms-panel" aria-label="Form catalog">
            <h2>Project forms</h2>
            <label htmlFor="forms-project">Project</label>
            <select
              id="forms-project"
              value={projectId}
              disabled={busy}
              onChange={(event) => {
                const id = event.target.value;
                void (async () => {
                  if (!begin()) return;
                  try {
                    await loadProject(session.client, id);
                    setNotice(`Loaded project ${id}.`);
                  } catch (err) {
                    setError(messageFor(err));
                  } finally {
                    end();
                  }
                })();
              }}
            >
              {projects.map((project) => (
                <option key={project.project_id} value={project.project_id}>
                  {project.name} ({project.project_id})
                </option>
              ))}
            </select>
            <ul className="nf-forms-list">
              {catalog.map((form) => (
                <li key={form.form_id}>
                  <button
                    type="button"
                    aria-current={
                      selectedKey === `catalog:${form.form_id}`
                        ? "true"
                        : undefined
                    }
                    onClick={() =>
                      selectDefinition(form, `catalog:${form.form_id}`)
                    }
                  >
                    {form.form_id}
                  </button>
                </li>
              ))}
            </ul>
            <h3>Approval bindings</h3>
            <ul className="nf-forms-list">
              {bindings.map((binding) => (
                <li key={`${binding.workflowId}/${binding.nodeId}`}>
                  <button
                    type="button"
                    aria-current={
                      selectedKey ===
                      `inline:${binding.workflowId}/${binding.nodeId}`
                        ? "true"
                        : undefined
                    }
                    onClick={() => {
                      if (!revision) return;
                      setBindWorkflowId(binding.workflowId);
                      setBindNodeId(binding.nodeId);
                      const inline = readInlineApprovalForm(
                        revision.content,
                        binding.workflowId,
                        binding.nodeId,
                      );
                      if (inline) {
                        selectDefinition(
                          inline.definition,
                          `inline:${binding.workflowId}/${binding.nodeId}`,
                        );
                      }
                    }}
                  >
                    {binding.nodeId}
                    {binding.hasInlineForm
                      ? ` · ${binding.formId ?? "form"}`
                      : " · no form"}
                  </button>
                </li>
              ))}
            </ul>
            <div className="nf-form-builder-toolbar">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  const blank = emptyFormDefinition(
                    `form_${Date.now().toString(36)}`,
                  );
                  selectDefinition(blank, `catalog:${blank.form_id}`);
                  setNotice("Started a new form draft.");
                }}
              >
                New form
              </button>
            </div>
          </aside>
          <section className="nf-forms-panel" aria-label="Form editor">
            <h2>Builder</h2>
            <label htmlFor="forms-form-id">Form ID</label>
            <input
              id="forms-form-id"
              value={formId}
              disabled={busy}
              onChange={(event) => setFormId(event.target.value)}
              maxLength={128}
              autoComplete="off"
            />
            <div className="nf-form-builder-fields">
              {drafts.map((draft, index) => (
                <div className="nf-form-field-card" key={`field-card-${index}`}>
                  <h4>Field {index + 1}</h4>
                  <div className="nf-form-field-grid">
                    <div>
                      <label htmlFor={`field-id-${index}`}>Field ID</label>
                      <input
                        id={`field-id-${index}`}
                        value={draft.field_id}
                        disabled={busy}
                        onChange={(event) =>
                          updateDraft(index, { field_id: event.target.value })
                        }
                      />
                    </div>
                    <div>
                      <label htmlFor={`field-title-${index}`}>Label</label>
                      <input
                        id={`field-title-${index}`}
                        value={draft.title}
                        disabled={busy}
                        onChange={(event) =>
                          updateDraft(index, { title: event.target.value })
                        }
                      />
                    </div>
                    <div>
                      <label htmlFor={`field-type-${index}`}>Type</label>
                      <select
                        id={`field-type-${index}`}
                        value={draft.type}
                        disabled={busy}
                        onChange={(event) =>
                          updateDraft(index, {
                            type: event.target.value as FormFieldDraft["type"],
                          })
                        }
                      >
                        {(
                          [
                            "string",
                            "number",
                            "integer",
                            "boolean",
                            "object",
                            "array",
                            "null",
                          ] as const
                        ).map((type) => (
                          <option key={type} value={type}>
                            {type}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label htmlFor={`field-enum-${index}`}>
                        Enum options (comma-separated)
                      </label>
                      <input
                        id={`field-enum-${index}`}
                        value={draft.enumOptions}
                        disabled={busy || draft.type !== "string"}
                        onChange={(event) =>
                          updateDraft(index, {
                            enumOptions: event.target.value,
                          })
                        }
                      />
                    </div>
                    <div className="nf-span-2">
                      <label htmlFor={`field-desc-${index}`}>Description</label>
                      <input
                        id={`field-desc-${index}`}
                        value={draft.description}
                        disabled={busy}
                        onChange={(event) =>
                          updateDraft(index, {
                            description: event.target.value,
                          })
                        }
                      />
                    </div>
                  </div>
                  <div className="nf-form-check-row">
                    <input
                      id={`field-required-${index}`}
                      type="checkbox"
                      checked={draft.required}
                      disabled={busy}
                      onChange={(event) =>
                        updateDraft(index, { required: event.target.checked })
                      }
                    />
                    <label htmlFor={`field-required-${index}`}>Required</label>
                    <input
                      id={`field-readonly-${index}`}
                      type="checkbox"
                      checked={draft.readOnly}
                      disabled={busy}
                      onChange={(event) =>
                        updateDraft(index, { readOnly: event.target.checked })
                      }
                    />
                    <label htmlFor={`field-readonly-${index}`}>Read-only</label>
                    <input
                      id={`field-writeonly-${index}`}
                      type="checkbox"
                      checked={draft.writeOnly}
                      disabled={busy}
                      onChange={(event) =>
                        updateDraft(index, { writeOnly: event.target.checked })
                      }
                    />
                    <label htmlFor={`field-writeonly-${index}`}>
                      Write-only
                    </label>
                  </div>
                  <div className="nf-form-field-row">
                    <button
                      type="button"
                      disabled={busy || index === 0}
                      onClick={() => moveDraft(index, -1)}
                    >
                      Move up
                    </button>
                    <button
                      type="button"
                      disabled={busy || index === drafts.length - 1}
                      onClick={() => moveDraft(index, 1)}
                    >
                      Move down
                    </button>
                    <button
                      type="button"
                      disabled={busy || drafts.length <= 1}
                      onClick={() =>
                        setDrafts((current) =>
                          current.filter((_, i) => i !== index),
                        )
                      }
                    >
                      Remove
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <div className="nf-form-builder-toolbar">
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  setDrafts((current) => [
                    ...current,
                    blankFieldDraft(nextFieldSequence(current)),
                  ])
                }
              >
                Add field
              </button>
              <button
                type="button"
                className="nf-form-primary"
                disabled={busy}
                onClick={() => void saveForm({ alsoCatalog: true })}
              >
                Save to project forms
              </button>
            </div>
            <div className="nf-forms-binding">
              <h3>Bind to approval step</h3>
              <p>
                Writes an inline <code>human_task.form.definition</code> used by
                the managed-approval runtime.
              </p>
              <label htmlFor="forms-bind-workflow">Workflow</label>
              <select
                id="forms-bind-workflow"
                value={bindWorkflowId}
                disabled={busy}
                onChange={(event) => {
                  const workflowId = event.target.value;
                  setBindWorkflowId(workflowId);
                  const match = bindings.find(
                    (item) => item.workflowId === workflowId,
                  );
                  if (match) setBindNodeId(match.nodeId);
                }}
              >
                {[...new Set(bindings.map((item) => item.workflowId))].map(
                  (id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ),
                )}
              </select>
              <label htmlFor="forms-bind-node">Approval node</label>
              <select
                id="forms-bind-node"
                value={bindNodeId}
                disabled={busy}
                onChange={(event) => setBindNodeId(event.target.value)}
              >
                {bindings
                  .filter((item) => item.workflowId === bindWorkflowId)
                  .map((item) => (
                    <option key={item.nodeId} value={item.nodeId}>
                      {item.nodeId}
                    </option>
                  ))}
              </select>
              <div className="nf-form-builder-toolbar">
                <button
                  type="button"
                  className="nf-form-primary"
                  disabled={busy || !bindNodeId}
                  onClick={() =>
                    void saveForm({ bind: true, alsoCatalog: true })
                  }
                >
                  Save and bind to approval
                </button>
              </div>
            </div>
            {issues.length > 0 && (
              <ul className="nf-forms-error" role="alert">
                {issues.map((issue) => (
                  <li key={`${issue.path}:${issue.code}`}>
                    {issue.path}: {issue.message}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <aside className="nf-forms-panel" aria-label="Form preview">
            <h2>Accessible preview</h2>
            <div className="nf-form-check-row">
              <input
                id="preview-mode-task"
                type="radio"
                name="preview-mode"
                checked={previewMode === "task"}
                onChange={() => setPreviewMode("task")}
              />
              <label htmlFor="preview-mode-task">Task form</label>
              <input
                id="preview-mode-start"
                type="radio"
                name="preview-mode"
                checked={previewMode === "start"}
                onChange={() => setPreviewMode("start")}
              />
              <label htmlFor="preview-mode-start">Start form</label>
            </div>
            {preview ? (
              <FormRenderer
                form={preview}
                values={previewValues}
                mode={previewMode === "start" ? "start" : "preview"}
                heading={
                  previewMode === "start"
                    ? "Start form preview"
                    : "Task form preview"
                }
                showSubmit={false}
                onChange={(fieldId, value: JsonValue) =>
                  setPreviewValues((current) => ({
                    ...current,
                    [fieldId]: value,
                  }))
                }
              />
            ) : (
              <p>Fix validation issues to preview this form.</p>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

/** Browser/e2e mount helper for coordinator App wiring and local harnesses. */
export async function mountFormBuilder(
  target: string | HTMLElement,
  props: FormBuilderProps,
): Promise<Root> {
  const element =
    typeof target === "string" ? document.getElementById(target) : target;
  if (!element) throw new Error("Form builder mount target was not found");
  const root = createRoot(element);
  root.render(createElement(FormBuilder, props));
  return root;
}
