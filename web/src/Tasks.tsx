import { createElement, useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { createTaskClient, TaskApiError } from "./task-api";
import type {
  JsonObject,
  JsonValue,
  TaskClient,
  TaskConnection,
  TaskFormField,
  TaskFormView,
  TaskView,
} from "./task-api";
import {
  acquireEntraAccessToken,
  fetchBrowserAuthConfig,
  type BrowserAuthConfig,
} from "./entra-auth";
import "./tasks.css";
import "./tokens.css";

type Session = {
  client: TaskClient;
  organizationId: string;
  environmentId: string;
};

export type TasksProps = {
  onClose?: () => void;
  /** Deep-link run filter from publish/start controls. */
  initialRunId?: string;
  /** Optional shared Connect session from App composition. */
  connection?: TaskConnection;
};

const failureMessages: Record<string, string> = {
  invalid_request:
    "Check the organization, Dev environment, access token and task filters, then try again.",
  unauthorized:
    "Your access token is missing or expired. Reconnect with a valid token.",
  forbidden: "You do not have permission for these tasks.",
  not_found: "That task could not be found in the selected environment.",
  conflict:
    "This task changed. Reload the inbox or task detail before trying again.",
  unavailable:
    "The task service is unavailable. Try again when it is available.",
  invalid_response:
    "The task service returned an unsupported response. Try again later.",
};

function failure(error: unknown) {
  return (
    failureMessages[
      error instanceof TaskApiError ? error.code : "unavailable"
    ] ?? failureMessages.unavailable
  );
}

function commandId(prefix: string) {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function fieldSchema(field: TaskFormField): JsonObject {
  try {
    const parsed = JSON.parse(field.schema_json) as unknown;
    return typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed)
      ? (parsed as JsonObject)
      : {};
  } catch {
    return {};
  }
}

function fieldType(field: TaskFormField): string {
  const schema = fieldSchema(field);
  return typeof schema.type === "string" ? schema.type : "string";
}

function displayValue(value: JsonValue | undefined): string {
  if (value === undefined) return "—";
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number" || typeof value === "string")
    return String(value);
  return JSON.stringify(value);
}

function draftFromForm(form: TaskFormView): JsonObject {
  const draft: JsonObject = {};
  for (const field of form.fields) {
    if (!field.editable) continue;
    if (Object.hasOwn(form.values, field.field_id)) {
      draft[field.field_id] = form.values[field.field_id];
      continue;
    }
    if (fieldType(field) === "boolean") draft[field.field_id] = false;
    else if (fieldType(field) === "number") draft[field.field_id] = 0;
    else draft[field.field_id] = "";
  }
  return draft;
}

export default function Tasks({
  onClose,
  initialRunId = "",
  connection,
}: TasksProps) {
  const [organizationId, setOrganizationId] = useState(
    connection?.organizationId ?? "",
  );
  const [environmentId, setEnvironmentId] = useState(
    connection?.environmentId ?? "dev",
  );
  const [token, setToken] = useState("");
  const [browserAuth, setBrowserAuth] = useState<BrowserAuthConfig>({
    enabled: false,
  });
  const [session, setSession] = useState<Session | null>(null);
  const [runId, setRunId] = useState(initialRunId);
  const [tasks, setTasks] = useState<TaskView[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [selected, setSelected] = useState<TaskView | null>(null);
  const [form, setForm] = useState<TaskFormView | null>(null);
  const [formBound, setFormBound] = useState(false);
  const [draft, setDraft] = useState<JsonObject>({});
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const generation = useRef(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

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
    if (!connection) return;
    const ticket = begin();
    if (ticket === null) return;
    try {
      const client = createTaskClient(connection);
      setSession({
        client,
        organizationId: connection.organizationId,
        environmentId: connection.environmentId,
      });
      setOrganizationId(connection.organizationId);
      setEnvironmentId(connection.environmentId);
      setNotice("Connected with the shared Dev session.");
      void refreshInbox(client, ticket, initialRunId);
    } catch (caught) {
      if (ticket === generation.current) setError(failure(caught));
      end(ticket);
    }
  }, [connection, initialRunId]);

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
  function clearDetail() {
    setSelectedId("");
    setSelected(null);
    setForm(null);
    setFormBound(false);
    setDraft({});
  }
  function disconnect() {
    generation.current += 1;
    setSession(null);
    setTasks([]);
    setToken("");
    clearDetail();
    setError("");
    setNotice("");
    busyRef.current = false;
    setBusy(false);
  }

  async function refreshInbox(
    client: TaskClient,
    ticket: number,
    filterRunId: string,
    options?: { preserveNotice?: boolean },
  ) {
    try {
      const page = await client.listTasks({
        status: "pending",
        ...(filterRunId.trim() ? { runId: filterRunId.trim() } : {}),
      });
      if (ticket !== generation.current) return;
      setTasks(page.items);
      if (!options?.preserveNotice) {
        setNotice(
          page.items.length
            ? `Showing ${page.items.length} pending task${page.items.length === 1 ? "" : "s"}.`
            : "No pending tasks match this filter.",
        );
      }
      if (
        selectedId &&
        !page.items.some((item) => item.task_id === selectedId)
      ) {
        clearDetail();
      }
    } catch (caught) {
      if (ticket === generation.current) setError(failure(caught));
    } finally {
      end(ticket);
    }
  }

  async function connectWith(
    connection: TaskConnection,
    ticket: number,
  ) {
    if (connection.environmentId !== "dev") {
      setError("Only the dev environment can be used in this increment.");
      end(ticket);
      return;
    }
    const client = createTaskClient(connection);
    setSession({
      client,
      organizationId: connection.organizationId,
      environmentId: connection.environmentId,
    });
    setOrganizationId(connection.organizationId);
    setEnvironmentId(connection.environmentId);
    setToken("");
    await refreshInbox(client, ticket, runId);
  }

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    const ticket = begin();
    if (ticket === null) return;
    try {
      await connectWith({ organizationId, environmentId, token }, ticket);
    } catch (caught) {
      if (ticket === generation.current) setError(failure(caught));
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
      );
    } catch (caught) {
      if (ticket === generation.current) {
        setError(
          caught instanceof Error && caught.message.startsWith("Entra")
            ? caught.message
            : "Microsoft sign-in did not complete. You can paste a token instead.",
        );
      }
      end(ticket);
    }
  }

  async function loadTask(taskId: string) {
    if (!session) return;
    const ticket = begin();
    if (ticket === null) return;
    try {
      const task = await session.client.getTask(taskId);
      if (ticket !== generation.current) return;
      setSelectedId(task.task_id);
      setSelected(task);
      const preview = await session.client.getForm(task.task_id);
      if (ticket !== generation.current) return;
      if (preview) {
        setFormBound(true);
        setForm(preview);
        setDraft(draftFromForm(preview));
        setNotice(
          "Task loaded. Submit the form before approving when required.",
        );
      } else {
        setFormBound(false);
        setForm(null);
        setDraft({});
        setNotice("Task loaded. No managed form is bound.");
      }
    } catch (caught) {
      if (ticket === generation.current) setError(failure(caught));
    } finally {
      end(ticket);
    }
  }

  async function submitForm() {
    if (!session || !selected || !form) return;
    const ticket = begin();
    if (ticket === null) return;
    try {
      const revision = await session.client.submitForm(
        selected.task_id,
        form.revision,
        draft,
      );
      if (ticket !== generation.current) return;
      const task = await session.client.getTask(selected.task_id);
      const preview = await session.client.getForm(selected.task_id);
      if (ticket !== generation.current) return;
      setSelected(task);
      if (preview) {
        setForm(preview);
        setDraft(draftFromForm(preview));
      }
      setNotice(`Form saved at revision ${revision}. You can approve now.`);
    } catch (caught) {
      if (ticket === generation.current) setError(failure(caught));
    } finally {
      end(ticket);
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (!session || !selected) return;
    const ticket = begin();
    if (ticket === null) return;
    try {
      const expectedRevision =
        formBound && form ? form.revision : selected.revision;
      const result = await session.client.decide(
        selected.task_id,
        commandId(decision),
        expectedRevision,
        decision,
      );
      if (ticket !== generation.current) return;
      setSelected(result);
      setNotice(
        result.status === "pending"
          ? "Decision recorded. This approval advanced to the next stage."
          : `Task ${result.status}.`,
      );
      if (result.status === "pending") {
        const preview = await session.client.getForm(result.task_id);
        if (ticket !== generation.current) return;
        if (preview) {
          setFormBound(true);
          setForm(preview);
          setDraft(draftFromForm(preview));
        } else {
          setFormBound(false);
          setForm(null);
          setDraft({});
        }
      } else clearDetail();
      await refreshInbox(session.client, ticket, runId, {
        preserveNotice: true,
      });
    } catch (caught) {
      if (ticket === generation.current) {
        if (
          caught instanceof TaskApiError &&
          caught.code === "conflict" &&
          decision === "approve" &&
          formBound
        ) {
          setError(
            "Approve is blocked until the managed form is submitted, or the task revision changed. Submit the form or reload the task.",
          );
        } else setError(failure(caught));
      }
      end(ticket);
    }
  }

  function setField(fieldId: string, value: JsonValue) {
    setDraft((previous) => ({ ...previous, [fieldId]: value }));
  }

  return (
    <div className="task-portal">
      <header className="task-header">
        <div>
          <p className="task-eyebrow">Nexus Flow Designer</p>
          <h1>Task portal</h1>
        </div>
        <div className="task-header-actions">
          {session && (
            <button disabled={busy} onClick={disconnect} type="button">
              Disconnect
            </button>
          )}
          {onClose && (
            <button disabled={busy} onClick={onClose} type="button">
              Close
            </button>
          )}
        </div>
      </header>
      <p className="task-boundary">
        Pending inbox for Dev with local-demo tokens, shared Connect session, or
        Microsoft Entra when the host exposes browser auth. Tokens stay in
        memory only.
      </p>
      {!session && (
        <form className="task-connection-form" onSubmit={connect}>
          <h2>Connect to Dev tasks</h2>
          <p>
            Paste a bearer token from <code>uv run nexusflow-local token</code>{" "}
            (org <code>local-demo</code>, env <code>dev</code>), or sign in with
            Microsoft when Entra is configured.
          </p>
          <fieldset disabled={busy}>
            <label htmlFor="task-organization">Organization ID</label>
            <input
              id="task-organization"
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <label htmlFor="task-environment">Environment ID</label>
            <input
              id="task-environment"
              value={environmentId}
              onChange={(event) => setEnvironmentId(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <label htmlFor="task-token">Access token</label>
            <input
              id="task-token"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <button className="task-primary" type="submit">
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
      {error && (
        <p className="task-error" role="alert">
          {error}
        </p>
      )}
      <p className="task-notice" role="status" aria-live="polite">
        {busy ? "Working…" : notice}
      </p>
      {session && (
        <div className="task-content">
          <aside className="task-inbox" aria-label="Pending task inbox">
            <h2>Pending tasks</h2>
            <p>
              {session.organizationId} / {session.environmentId}
            </p>
            <div className="task-filter">
              <label htmlFor="task-run-filter">Filter by run ID</label>
              <input
                id="task-run-filter"
                value={runId}
                onChange={(event) => setRunId(event.target.value)}
                disabled={busy}
                autoComplete="off"
                spellCheck={false}
                placeholder="Optional run_id"
              />
              <div className="task-filter-actions">
                <button
                  className="task-primary"
                  disabled={busy}
                  type="button"
                  onClick={() => {
                    const ticket = begin();
                    if (ticket === null || !session) return;
                    void refreshInbox(session.client, ticket, runId);
                  }}
                >
                  Refresh inbox
                </button>
              </div>
            </div>
            {!tasks.length && (
              <div className="task-empty">
                No pending tasks. Start a managed approval run, then refresh.
              </div>
            )}
            <ul>
              {tasks.map((task) => (
                <li
                  key={task.task_id}
                  aria-current={
                    task.task_id === selectedId ? "true" : undefined
                  }
                >
                  <strong>{task.node_invocation_id}</strong>
                  <span>
                    {task.task_id} · run {task.run_id}
                  </span>
                  <span>
                    Revision {task.revision} · {task.rule}
                  </span>
                  <button
                    aria-label={`Open task ${task.task_id}`}
                    disabled={busy}
                    type="button"
                    onClick={() => {
                      void loadTask(task.task_id);
                    }}
                  >
                    Open task
                  </button>
                </li>
              ))}
            </ul>
          </aside>
          <section className="task-detail" aria-label="Task detail">
            {!selected && (
              <div className="task-empty">
                <h2>Choose a task</h2>
                <p>
                  Open a pending assignment to review its managed form and
                  approve or reject.
                </p>
              </div>
            )}
            {selected && (
              <>
                <h2>{selected.node_invocation_id}</h2>
                <p className="task-meta">
                  Task {selected.task_id} · run {selected.run_id} · revision{" "}
                  {selected.revision}
                </p>
                <span className={`task-status task-status-${selected.status}`}>
                  {selected.status}
                </span>
                {formBound && form && (
                  <form
                    className="task-form-fields"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void submitForm();
                    }}
                  >
                    <h3>Managed form</h3>
                    <fieldset disabled={busy || selected.status !== "pending"}>
                      {form.fields.map((field) => {
                        const schema = fieldSchema(field);
                        const type = fieldType(field);
                        const value = field.editable
                          ? draft[field.field_id]
                          : form.values[field.field_id];
                        if (!field.editable) {
                          return (
                            <div key={field.field_id}>
                              <label htmlFor={`task-field-${field.field_id}`}>
                                {field.label}
                              </label>
                              <div
                                id={`task-field-${field.field_id}`}
                                className="task-readonly-value"
                              >
                                {displayValue(value)}
                              </div>
                            </div>
                          );
                        }
                        if (type === "boolean") {
                          return (
                            <div className="task-checkbox" key={field.field_id}>
                              <input
                                id={`task-field-${field.field_id}`}
                                type="checkbox"
                                checked={value === true}
                                required={
                                  field.required || schema.const === true
                                }
                                onChange={(event) =>
                                  setField(field.field_id, event.target.checked)
                                }
                              />
                              <label htmlFor={`task-field-${field.field_id}`}>
                                {field.label}
                                {field.required ? " (required)" : ""}
                              </label>
                            </div>
                          );
                        }
                        return (
                          <div key={field.field_id}>
                            <label htmlFor={`task-field-${field.field_id}`}>
                              {field.label}
                              {field.required ? " (required)" : ""}
                            </label>
                            <input
                              id={`task-field-${field.field_id}`}
                              type={type === "number" ? "number" : "text"}
                              value={
                                value === undefined || value === null
                                  ? ""
                                  : String(value)
                              }
                              required={field.required}
                              onChange={(event) => {
                                const next =
                                  type === "number"
                                    ? event.target.value === ""
                                      ? ""
                                      : Number(event.target.value)
                                    : event.target.value;
                                setField(field.field_id, next);
                              }}
                            />
                          </div>
                        );
                      })}
                    </fieldset>
                    <div className="task-actions">
                      <button
                        className="task-primary"
                        disabled={busy || selected.status !== "pending"}
                        type="submit"
                      >
                        Submit form
                      </button>
                    </div>
                  </form>
                )}
                {!formBound && (
                  <p className="task-meta">
                    No managed form is bound to this task.
                  </p>
                )}
                <div className="task-actions">
                  <button
                    className="task-primary"
                    disabled={busy || selected.status !== "pending"}
                    type="button"
                    onClick={() => {
                      void decide("approve");
                    }}
                  >
                    Approve
                  </button>
                  <button
                    className="task-danger"
                    disabled={busy || selected.status !== "pending"}
                    type="button"
                    onClick={() => {
                      void decide("reject");
                    }}
                  >
                    Reject
                  </button>
                  <button
                    disabled={busy}
                    type="button"
                    onClick={() => {
                      void loadTask(selected.task_id);
                    }}
                  >
                    Reload task
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

/** Browser/e2e mount helper for coordinator App wiring and local harnesses. */
export async function mountTasks(
  target: string | HTMLElement,
  props: TasksProps = {},
): Promise<Root> {
  const element =
    typeof target === "string" ? document.getElementById(target) : target;
  if (!element) throw new Error("Task portal mount target was not found");
  const root = createRoot(element);
  root.render(createElement(Tasks, props));
  return root;
}
