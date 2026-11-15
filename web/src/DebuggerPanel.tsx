/**
 * Dev debugger panel (DB-T03 React slice).
 *
 * Composition contract:
 * - connection — optional App session reuse (org/env/token)
 * - initialProjectId / initialWorkflowId / initialSourceRevision — optional
 *   Designer gate prefill from App/Projects
 * - client / projectClient — injected for tests
 * - onClose — return to prior Dev-loop view
 *
 * Against existing signed debug APIs only. Requires enable-debugger on the host.
 * Does not claim Entra, parallel frontier, streaming, or production workers.
 */
import { createElement, useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  createDebuggerClient,
  newDebuggerOperationId,
  parseBreakpointText,
  type DebugControlAction,
  type DebuggerClient,
  type DebugEvent,
  type DebugInputInspection,
  type DebugSession,
  type DebugSnapshot,
  type DebugStepInspection,
  type JsonObject,
} from "./debugger-api";
import {
  createProjectClient,
  ProjectApiError,
  type ProjectClient,
  type ProjectConnection,
  type ProjectSummary,
} from "./project-api";
import DebuggerCanvasOverlay from "./DebuggerCanvasOverlay";
import "./debugger.css";
import "./tokens.css";

export interface DebuggerPanelProps {
  onClose: () => void;
  connection?: ProjectConnection;
  initialProjectId?: string;
  initialWorkflowId?: string;
  initialSourceRevision?: number;
  /** Injected debugger client for tests. */
  client?: DebuggerClient;
  /** Injected project list client for tests. */
  projectClient?: ProjectClient;
  /** Status poll interval ms (tests may lower). */
  pollIntervalMs?: number;
}

type Session = {
  connection: ProjectConnection;
  client: DebuggerClient;
  projects: ProjectClient;
};

const failureMessages: Record<string, string> = {
  invalid_request:
    "Check the project, revision, workflow, breakpoints, and token values.",
  unauthorized: "The token was rejected. Mint a fresh local-demo token.",
  forbidden:
    "Debug is Dev-only for the session owner. Enable the debugger and confirm debug.execute.",
  not_found: "That project or debug session was not found in this environment.",
  conflict:
    "A conflicting command or operation exists. Reload status and retry with a new command id.",
  unavailable:
    "The host or debugger provider is unavailable. Run enable-debugger, then serve --ui.",
  invalid_response: "The host returned an unexpected response.",
};

function messageFor(error: unknown): string {
  if (error instanceof ProjectApiError)
    return failureMessages[error.code] ?? failureMessages.unavailable;
  if (error instanceof Error && error.message) return error.message;
  return failureMessages.unavailable;
}

function formatValues(values: JsonObject): string {
  try {
    return JSON.stringify(values, null, 2);
  } catch {
    return "{}";
  }
}

export default function DebuggerPanel({
  onClose,
  connection,
  initialProjectId = "",
  initialWorkflowId = "",
  initialSourceRevision,
  client: injected,
  projectClient: injectedProjects,
  pollIntervalMs = 2000,
}: DebuggerPanelProps) {
  const [organizationId, setOrganizationId] = useState(
    connection?.organizationId ?? "local-demo",
  );
  const [environmentId, setEnvironmentId] = useState(
    connection?.environmentId ?? "dev",
  );
  const [token, setToken] = useState(connection?.token ?? "");
  const [session, setSession] = useState<Session | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectId, setProjectId] = useState(initialProjectId);
  const [workflowId, setWorkflowId] = useState(initialWorkflowId || "welcome");
  const [sourceRevision, setSourceRevision] = useState(
    String(initialSourceRevision && initialSourceRevision >= 1
      ? initialSourceRevision
      : 1),
  );
  const [configurationRevision, setConfigurationRevision] =
    useState("local-v1");
  const [breakpointText, setBreakpointText] = useState("");
  const [inputJson, setInputJson] = useState("{}");
  const [activeSession, setActiveSession] = useState<DebugSession | null>(null);
  const [snapshot, setSnapshot] = useState<DebugSnapshot | null>(null);
  const [breakpoints, setBreakpoints] = useState<string[]>([]);
  const [sessions, setSessions] = useState<DebugSession[]>([]);
  const [events, setEvents] = useState<DebugEvent[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [workflowInputs, setWorkflowInputs] =
    useState<DebugInputInspection | null>(null);
  const [stepInput, setStepInput] = useState<DebugStepInspection | null>(null);
  const [stepOutput, setStepOutput] = useState<DebugStepInspection | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const pollGen = useRef(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!connection) return;
    void connectWith(connection, injected, injectedProjects);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- cold mount from App session
  }, []);

  useEffect(() => {
    if (!session || !activeSession || !projectId) return;
    const ticket = ++pollGen.current;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const status = await session.client.getStatus(
            projectId,
            activeSession.session_id,
          );
          if (ticket !== pollGen.current) return;
          setActiveSession(status.session);
          setSnapshot(status.snapshot);
          if (!selectedNodeId) setSelectedNodeId(status.snapshot.node_id);
        } catch {
          /* keep last good snapshot; user can Refresh */
        }
      })();
    }, pollIntervalMs);
    return () => {
      window.clearInterval(timer);
      pollGen.current += 1;
    };
  }, [session, activeSession?.session_id, projectId, pollIntervalMs]);

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

  async function connectWith(
    next: ProjectConnection,
    clientOverride?: DebuggerClient,
    projectsOverride?: ProjectClient,
  ) {
    if (!begin()) return;
    try {
      const projectsClient =
        projectsOverride ?? injectedProjects ?? createProjectClient(next);
      const client = clientOverride ?? injected ?? createDebuggerClient(next);
      const listed = await projectsClient.listProjects();
      setSession({ connection: next, client, projects: projectsClient });
      setProjects(listed);
      if (!projectId && listed[0]) setProjectId(listed[0].project_id);
      setNotice(
        listed.length === 0
          ? "Connected. Create a Dev project in Projects, then return here."
          : `Connected. ${listed.length} project${listed.length === 1 ? "" : "s"} available. Host needs enable-debugger.`,
      );
    } catch (err) {
      setSession(null);
      setProjects([]);
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

  async function refreshSessionList(client = session?.client, id = projectId) {
    if (!client || !id) return [];
    const page = await client.listSessions(id, 50);
    setSessions(page.items);
    return page.items;
  }

  async function applyReconnect(
    client: DebuggerClient,
    id: string,
    sessionId: string,
  ) {
    const baseline = await client.reconnect(id, sessionId);
    setSnapshot(baseline.snapshot);
    setBreakpoints(baseline.breakpoints);
    setSelectedNodeId(baseline.snapshot.node_id);
    const page = await client.listEvents(
      id,
      sessionId,
      baseline.next_after > 0 ? baseline.next_after : 0,
      25,
    );
    if (page.gap) {
      setNotice(
        "Reconnect baseline ready. Event retention gap detected — use status plus this baseline before trusting event order.",
      );
    }
    setEvents(page.events);
    return baseline;
  }

  async function selectSession(next: DebugSession) {
    if (!session || !projectId) return;
    if (!begin()) return;
    try {
      const status = await session.client.getStatus(
        projectId,
        next.session_id,
      );
      setActiveSession(status.session);
      setSnapshot(status.snapshot);
      setSelectedNodeId(status.snapshot.node_id);
      await applyReconnect(session.client, projectId, next.session_id);
      const bp = await session.client.getBreakpoints(
        projectId,
        next.session_id,
      );
      setBreakpoints(bp.breakpoints);
      setBreakpointText(bp.breakpoints.join(", "));
      setNotice(`Loaded session ${next.session_id}.`);
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function startDebug() {
    if (!session || !projectId) return;
    if (!begin()) return;
    try {
      const revision = Number(sourceRevision);
      if (!Number.isSafeInteger(revision) || revision < 1) {
        throw new ProjectApiError("invalid_request");
      }
      let input: JsonObject = {};
      try {
        const parsed = JSON.parse(inputJson) as unknown;
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          Array.isArray(parsed)
        ) {
          throw new Error();
        }
        input = parsed as JsonObject;
      } catch {
        throw new Error("Workflow input must be a JSON object.");
      }
      const breakpointsList = breakpointText.trim()
        ? parseBreakpointText(breakpointText)
        : [];
      const created = await session.client.startSession(projectId, {
        operation_id: newDebuggerOperationId("debug"),
        source_revision: revision,
        workflow_id: workflowId.trim(),
        configuration_revision: configurationRevision.trim() || "local-v1",
        input,
        breakpoints: breakpointsList,
        mode: "real",
      });
      setActiveSession(created);
      setBreakpoints(breakpointsList);
      await refreshSessionList();
      const status = await session.client.getStatus(
        projectId,
        created.session_id,
      );
      setActiveSession(status.session);
      setSnapshot(status.snapshot);
      setSelectedNodeId(status.snapshot.node_id);
      await applyReconnect(session.client, projectId, created.session_id);
      setNotice(
        `Started debug session ${created.session_id} (mode=real). Real Dev activities may have external effects.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function sendAction(action: DebugControlAction) {
    if (!session || !projectId || !activeSession || !snapshot) return;
    if (!begin()) return;
    try {
      const receipt = await session.client.sendCommand(
        projectId,
        activeSession.session_id,
        {
          command_id: newDebuggerOperationId(action),
          node_id: snapshot.node_id,
          action,
          expected_revision: snapshot.revision,
        },
      );
      const status = await session.client.getStatus(
        projectId,
        activeSession.session_id,
      );
      setActiveSession(status.session);
      setSnapshot(status.snapshot);
      setNotice(
        `${action} accepted (command ${receipt.command_id}, revision ${receipt.revision}).`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function saveBreakpoints() {
    if (!session || !projectId || !activeSession || !snapshot) return;
    if (!begin()) return;
    try {
      const next = breakpointText.trim()
        ? parseBreakpointText(breakpointText)
        : [];
      const receipt = await session.client.replaceBreakpoints(
        projectId,
        activeSession.session_id,
        {
          command_id: newDebuggerOperationId("bp"),
          node_id: snapshot.node_id,
          breakpoints: next,
          expected_revision: snapshot.revision,
        },
      );
      setBreakpoints(next);
      const status = await session.client.getStatus(
        projectId,
        activeSession.session_id,
      );
      setSnapshot(status.snapshot);
      setNotice(
        `Breakpoints replaced without releasing the current gate (revision ${receipt.revision}).`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function loadInspection(kind: "workflow" | "input" | "output") {
    if (!session || !projectId || !activeSession) return;
    const nodeId = selectedNodeId || snapshot?.node_id;
    if (kind !== "workflow" && !nodeId) return;
    if (!begin()) return;
    try {
      if (kind === "workflow") {
        const value = await session.client.inspectInputs(
          projectId,
          activeSession.session_id,
        );
        setWorkflowInputs(value);
        setNotice(
          value.truncated
            ? "Workflow inputs loaded (truncated)."
            : "Workflow inputs loaded (redacted public fields only).",
        );
      } else if (kind === "input") {
        const value = await session.client.inspectStepInput(
          projectId,
          activeSession.session_id,
          nodeId!,
        );
        setStepInput(value);
        setNotice(`Step input for ${nodeId} loaded.`);
      } else {
        const value = await session.client.inspectStepOutput(
          projectId,
          activeSession.session_id,
          nodeId!,
        );
        setStepOutput(value);
        setNotice(`Step output for ${nodeId} loaded.`);
      }
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function refreshNow() {
    if (!session || !projectId || !activeSession) return;
    if (!begin()) return;
    try {
      await applyReconnect(session.client, projectId, activeSession.session_id);
      const status = await session.client.getStatus(
        projectId,
        activeSession.session_id,
      );
      setActiveSession(status.session);
      setSnapshot(status.snapshot);
      setNotice("Refreshed reconnect baseline and status.");
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function onProjectChange(nextId: string) {
    setProjectId(nextId);
    setActiveSession(null);
    setSnapshot(null);
    setSessions([]);
    setEvents([]);
    setWorkflowInputs(null);
    setStepInput(null);
    setStepOutput(null);
    if (!session || !nextId) return;
    if (!begin()) return;
    try {
      await refreshSessionList(session.client, nextId);
      setNotice(`Loaded sessions for ${nextId}.`);
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  const paused =
    snapshot?.state === "paused" ||
    snapshot?.state === "waiting_timer" ||
    snapshot?.state === "waiting_approval";

  return (
    <div className="nf-debugger-surface" aria-label="Debugger panel">
      <header className="nf-debugger-header">
        <div>
          <span className="nf-debugger-eyebrow">NEXUS FLOW DESIGNER</span>
          <h1>Debugger</h1>
        </div>
        <div className="nf-debugger-header-actions">
          {session && (
            <button
              type="button"
              disabled={busy || !projectId}
              onClick={() => {
                void (async () => {
                  if (!session || !projectId) return;
                  if (!begin()) return;
                  try {
                    await refreshSessionList();
                    setNotice("Session list refreshed.");
                  } catch (err) {
                    setError(messageFor(err));
                  } finally {
                    end();
                  }
                })();
              }}
            >
              Refresh sessions
            </button>
          )}
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </header>
      <p className="nf-debugger-boundary">
        Dev-only draft debugging against existing signed APIs. Run{" "}
        <code>nexusflow-local enable-debugger</code> before serve. Mode is always{" "}
        <strong>real</strong> — activities may have external effects. Parallel
        frontier stepping, streaming events, and Entra acceptance remain open.
      </p>

      {!session ? (
        <form className="nf-debugger-connect" onSubmit={connect}>
          <h2>Connect</h2>
          <p>
            Reuse the Projects session when available, or paste a local-demo
            bearer token. Tokens stay in memory for this tab only.
          </p>
          <fieldset disabled={busy}>
            <label htmlFor="debugger-org">Organization</label>
            <input
              id="debugger-org"
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
              autoComplete="off"
            />
            <label htmlFor="debugger-env">Environment</label>
            <input
              id="debugger-env"
              value={environmentId}
              onChange={(event) => setEnvironmentId(event.target.value)}
              autoComplete="off"
            />
            <label htmlFor="debugger-token">Access token</label>
            <input
              id="debugger-token"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
            />
            <button type="submit" className="nf-debugger-primary">
              Connect
            </button>
          </fieldset>
        </form>
      ) : (
        <div className="nf-debugger-layout">
          <section className="nf-debugger-panel" aria-label="Session controls">
            <h2>Start or resume</h2>
            <label htmlFor="debugger-project">Project</label>
            <select
              id="debugger-project"
              value={projectId}
              disabled={busy}
              onChange={(event) => {
                void onProjectChange(event.target.value);
              }}
            >
              <option value="">Select a project</option>
              {projects.map((project) => (
                <option key={project.project_id} value={project.project_id}>
                  {project.project_id} (rev {project.head_revision})
                </option>
              ))}
            </select>
            <label htmlFor="debugger-workflow">Workflow ID</label>
            <input
              id="debugger-workflow"
              value={workflowId}
              disabled={busy}
              onChange={(event) => setWorkflowId(event.target.value)}
            />
            <label htmlFor="debugger-revision">Source revision</label>
            <input
              id="debugger-revision"
              inputMode="numeric"
              value={sourceRevision}
              disabled={busy}
              onChange={(event) => setSourceRevision(event.target.value)}
            />
            <label htmlFor="debugger-config">Configuration revision</label>
            <input
              id="debugger-config"
              value={configurationRevision}
              disabled={busy}
              onChange={(event) => setConfigurationRevision(event.target.value)}
            />
            <label htmlFor="debugger-breakpoints">
              Breakpoints (comma-separated node IDs)
            </label>
            <input
              id="debugger-breakpoints"
              value={breakpointText}
              disabled={busy}
              onChange={(event) => setBreakpointText(event.target.value)}
              placeholder="wait, review"
            />
            <label htmlFor="debugger-input">Workflow input JSON</label>
            <textarea
              id="debugger-input"
              value={inputJson}
              disabled={busy}
              onChange={(event) => setInputJson(event.target.value)}
              rows={4}
            />
            <div className="nf-debugger-actions">
              <button
                type="button"
                className="nf-debugger-primary"
                disabled={busy || !projectId}
                onClick={() => {
                  void startDebug();
                }}
              >
                Start debug session
              </button>
              <button
                type="button"
                disabled={busy || !activeSession || !snapshot}
                onClick={() => {
                  void saveBreakpoints();
                }}
              >
                Replace breakpoints
              </button>
            </div>

            <h3>Saved sessions</h3>
            {sessions.length === 0 ? (
              <p className="nf-debugger-muted">
                No owner-scoped sessions for this project yet.
              </p>
            ) : (
              <ul className="nf-debugger-session-list">
                {sessions.map((item) => (
                  <li key={item.session_id}>
                    <button
                      type="button"
                      disabled={busy}
                      aria-pressed={
                        activeSession?.session_id === item.session_id
                      }
                      onClick={() => {
                        void selectSession(item);
                      }}
                    >
                      <code>{item.session_id}</code>
                      <span>
                        {item.workflow_id} · rev {item.source_revision} ·{" "}
                        {item.state}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="nf-debugger-panel" aria-label="Runtime controls">
            <h2>Controls</h2>
            {activeSession && snapshot ? (
              <>
                <p className="nf-debugger-status" role="status">
                  Session <code>{activeSession.session_id}</code> · execution{" "}
                  <code>{activeSession.execution_id}</code>
                  {activeSession.temporal_run_id
                    ? ` · run ${activeSession.temporal_run_id}`
                    : ""}
                </p>
                <DebuggerCanvasOverlay
                  snapshot={snapshot}
                  breakpoints={breakpoints}
                  selectedNodeId={selectedNodeId}
                  onSelectNode={setSelectedNodeId}
                />
                <div className="nf-debugger-actions">
                  <button
                    type="button"
                    disabled={busy || !paused}
                    onClick={() => {
                      void sendAction("step_over");
                    }}
                  >
                    Step over
                  </button>
                  <button
                    type="button"
                    disabled={busy || !paused}
                    onClick={() => {
                      void sendAction("continue");
                    }}
                  >
                    Continue
                  </button>
                  <button
                    type="button"
                    disabled={busy || !paused}
                    onClick={() => {
                      void sendAction("cancel");
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      void refreshNow();
                    }}
                  >
                    Reconnect baseline
                  </button>
                </div>
                <p className="nf-debugger-muted">
                  Step/continue/cancel require the current paused or waiting
                  boundary and expected revision. Managed-task waits stay
                  ineligible until cleanup support lands.
                </p>
                <h3>Events (metadata)</h3>
                {events.length === 0 ? (
                  <p className="nf-debugger-muted">No retained events yet.</p>
                ) : (
                  <ol className="nf-debugger-events">
                    {events.map((event) => (
                      <li key={event.sequence}>
                        #{event.sequence} {event.kind} · {event.state}
                        {event.node_id ? ` · ${event.node_id}` : ""} · rev{" "}
                        {event.revision}
                      </li>
                    ))}
                  </ol>
                )}
              </>
            ) : (
              <p className="nf-debugger-muted">
                Start a session or select a saved one to enable controls.
              </p>
            )}
          </section>

          <section
            className="nf-debugger-panel"
            aria-label="Variable inspector"
          >
            <h2>Inspector</h2>
            <label htmlFor="debugger-node">Selected node</label>
            <input
              id="debugger-node"
              value={selectedNodeId}
              disabled={busy || !activeSession}
              onChange={(event) => setSelectedNodeId(event.target.value)}
            />
            <div className="nf-debugger-actions">
              <button
                type="button"
                disabled={busy || !activeSession}
                onClick={() => {
                  void loadInspection("workflow");
                }}
              >
                Workflow inputs
              </button>
              <button
                type="button"
                disabled={busy || !activeSession || !selectedNodeId}
                onClick={() => {
                  void loadInspection("input");
                }}
              >
                Step input
              </button>
              <button
                type="button"
                disabled={busy || !activeSession || !selectedNodeId}
                onClick={() => {
                  void loadInspection("output");
                }}
              >
                Step output
              </button>
            </div>
            <p className="nf-debugger-muted">
              Only explicitly public / array-opted fields appear. Secrets and
              unprotected fields stay redacted server-side.
            </p>
            {workflowInputs && (
              <InspectionBlock
                title="Workflow inputs"
                values={workflowInputs.values}
                redacted={workflowInputs.redacted_paths}
                truncated={workflowInputs.truncated}
              />
            )}
            {stepInput && (
              <InspectionBlock
                title={`Step input · ${stepInput.node_id}`}
                values={stepInput.values}
                redacted={stepInput.redacted_paths}
                truncated={stepInput.truncated}
                detail={`${stepInput.activity_ref.activity_id}@${stepInput.activity_ref.version}`}
              />
            )}
            {stepOutput && (
              <InspectionBlock
                title={`Step output · ${stepOutput.node_id}`}
                values={stepOutput.values}
                redacted={stepOutput.redacted_paths}
                truncated={stepOutput.truncated}
                detail={`${stepOutput.activity_ref.activity_id}@${stepOutput.activity_ref.version}`}
              />
            )}
          </section>
        </div>
      )}

      {error && (
        <p className="nf-debugger-error" role="alert">
          {error}
        </p>
      )}
      {notice && !error && (
        <p className="nf-debugger-notice" role="status">
          {notice}
        </p>
      )}
    </div>
  );
}

function InspectionBlock({
  title,
  values,
  redacted,
  truncated,
  detail,
}: {
  title: string;
  values: JsonObject;
  redacted: string[];
  truncated: boolean;
  detail?: string;
}) {
  return (
    <div className="nf-debugger-inspection">
      <h3>{title}</h3>
      {detail && <p className="nf-debugger-muted">{detail}</p>}
      <pre>{formatValues(values)}</pre>
      {redacted.length > 0 && (
        <p className="nf-debugger-muted">
          Redacted paths: {redacted.join(", ")}
        </p>
      )}
      {truncated && (
        <p className="nf-debugger-muted">Response was truncated by budget.</p>
      )}
    </div>
  );
}

/** Browser/e2e mount helper for coordinator App wiring and local harnesses. */
export async function mountDebuggerPanel(
  target: string | HTMLElement,
  props: DebuggerPanelProps,
): Promise<Root> {
  const element =
    typeof target === "string" ? document.getElementById(target) : target;
  if (!element) throw new Error("Debugger panel mount target was not found");
  const root = createRoot(element);
  root.render(createElement(DebuggerPanel, props));
  return root;
}
