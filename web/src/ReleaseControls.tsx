/**
 * Mountable publish / activate / start / status panel for Projects or App.
 *
 * Composition contract (frozen for Phase 1C):
 * - connection, projectId, headRevision, workflowId — required scope + targets
 * - validationOk — parent Designer validation gate; when false, Publish is blocked
 * - disabled — parent busy/conflict; disables all mutating actions
 * - configurationRevision — defaults to local-demo `local-v1`
 * - onActivated / onStarted — optional parent hooks after success
 * - onOpenTasks(runId) — deep-link affordance to task inbox by run_id
 *
 * Do not call publish when validationOk is false. Parent owns validation UX;
 * this control only respects the gate.
 */
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import type { ProjectConnection } from "./project-api";
import { ProjectApiError } from "./project-api";
import {
  TERMINAL_EXECUTION_STATES,
  createReleaseClient,
  newIdempotencyKey,
  type ExecutionStatus,
  type ProjectActivation,
  type ProjectRelease,
  type ReleaseClient,
} from "./release-api";
import "./release-controls.css";
import "./tokens.css";

export type ReleaseControlsProps = {
  connection: ProjectConnection;
  projectId: string;
  headRevision: number;
  workflowId: string;
  /** Parent sets false when Designer validation failed or has not passed. */
  validationOk?: boolean;
  disabled?: boolean;
  /** Local-demo default; override for non-demo configuration revisions. */
  configurationRevision?: string;
  onActivated?: (activation: ProjectActivation) => void;
  onStarted?: (executionId: string) => void;
  onOpenTasks?: (runId: string) => void;
  /** Injected client for tests; production uses createReleaseClient(connection). */
  client?: ReleaseClient;
  /** Status poll interval ms (tests may lower). */
  pollIntervalMs?: number;
};

const failureMessages: Record<string, string> = {
  invalid_request:
    "Check the project, revision, and configuration, then try again.",
  unauthorized: "Your access token is missing or expired. Reconnect and retry.",
  forbidden: "You do not have permission for this release action.",
  not_found: "The project, release, or run was not found in this environment.",
  conflict:
    "A conflicting release or activation exists. Reload activation state and retry with a new operation id when needed.",
  unavailable: "The release service is unavailable. Retry with the same key.",
  invalid_response: "The release service returned an unsupported response.",
};

function failure(error: unknown) {
  return (
    failureMessages[
      error instanceof ProjectApiError ? error.code : "unavailable"
    ] ?? failureMessages.unavailable
  );
}

function statusSummary(status: ExecutionStatus): string {
  const node = status.node_id ? ` · node ${status.node_id}` : "";
  const done =
    status.completed_nodes.length > 0
      ? ` · ${status.completed_nodes.length} completed`
      : "";
  return `${status.state}${node}${done}`;
}

export default function ReleaseControls({
  connection,
  projectId,
  headRevision,
  workflowId,
  validationOk = true,
  disabled = false,
  configurationRevision = "local-v1",
  onActivated,
  onStarted,
  onOpenTasks,
  client: injected,
  pollIntervalMs = 2000,
}: ReleaseControlsProps) {
  const clientRef = useRef<ReleaseClient | null>(injected ?? null);
  if (!clientRef.current) {
    clientRef.current = createReleaseClient(connection);
  }
  const client = injected ?? clientRef.current;

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [release, setRelease] = useState<ProjectRelease | null>(null);
  const [activation, setActivation] = useState<ProjectActivation | null>(null);
  const [executionId, setExecutionId] = useState("");
  const [status, setStatus] = useState<ExecutionStatus | null>(null);
  const publishKey = useRef<string | null>(null);
  const activateKey = useRef<string | null>(null);
  const startKey = useRef<string | null>(null);
  const pollGen = useRef(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const current = await client.getActivation(projectId);
        if (!cancelled) setActivation(current);
      } catch {
        /* optional preload; actions still work */
      }
    })();
    return () => {
      cancelled = true;
      pollGen.current += 1;
    };
  }, [client, projectId]);

  useEffect(() => {
    if (!executionId) return;
    const generation = ++pollGen.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;

    async function poll() {
      if (cancelled || generation !== pollGen.current) return;
      try {
        const next = await client.getExecution(projectId, executionId);
        if (cancelled || generation !== pollGen.current) return;
        setStatus(next);
        setError("");
        if (!TERMINAL_EXECUTION_STATES.has(next.state)) {
          timer = setTimeout(poll, pollIntervalMs);
        }
      } catch (err) {
        if (cancelled || generation !== pollGen.current) return;
        setError(failure(err));
        timer = setTimeout(poll, pollIntervalMs);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [client, projectId, executionId, pollIntervalMs]);

  const locked = disabled || busy;
  const canPublish = !locked && validationOk && headRevision >= 1;
  const canActivate = !locked && release !== null;
  const canStart =
    !locked &&
    activation !== null &&
    activation.release_id !== null &&
    activation.revision >= 1;

  async function run<T>(label: string, work: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await work();
      setNotice(label);
      return result;
    } catch (err) {
      setError(failure(err));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function onPublish() {
    if (!canPublish) return;
    if (!publishKey.current) publishKey.current = newIdempotencyKey("pub");
    const result = await run("Release published.", () =>
      client.publish(projectId, headRevision, publishKey.current!),
    );
    if (result) {
      setRelease(result);
      publishKey.current = null;
      activateKey.current = null;
    }
  }

  async function onActivate() {
    if (!canActivate || !release) return;
    if (!activateKey.current) activateKey.current = newIdempotencyKey("act");
    const expectedRevision = activation?.revision ?? 0;
    const result = await run("Release activated.", () =>
      client.activate(projectId, {
        releaseId: release.release_id,
        configurationRevision,
        operationId: activateKey.current!,
        expectedRevision,
      }),
    );
    if (result) {
      setActivation(result);
      activateKey.current = null;
      startKey.current = null;
      onActivated?.(result);
    }
  }

  async function onStart() {
    if (!canStart) return;
    if (!startKey.current) startKey.current = newIdempotencyKey("run");
    const result = await run("Execution started.", () =>
      client.start(projectId, {
        workflowId,
        idempotencyKey: startKey.current!,
        input: {},
        source: "manual",
      }),
    );
    if (result) {
      setExecutionId(result.execution_id);
      setStatus(null);
      startKey.current = null;
      onStarted?.(result.execution_id);
    }
  }

  async function onRefreshStatus() {
    if (!executionId || locked) return;
    await run("Status refreshed.", async () => {
      const next = await client.getExecution(projectId, executionId);
      setStatus(next);
      return next;
    });
  }

  return (
    <section
      className="release-controls"
      aria-label="Publish, activate, and run"
    >
      <header className="release-controls-header">
        <p className="release-eyebrow">Run controls</p>
        <h2>Publish and start</h2>
        <p className="release-lede">
          Publish revision {headRevision}, activate with{" "}
          <code>{configurationRevision}</code>, then start{" "}
          <code>{workflowId}</code>.
        </p>
      </header>

      {!validationOk ? (
        <p className="release-gate" role="status">
          Publish is blocked until Designer validation passes.
        </p>
      ) : null}

      <div className="release-actions">
        <button
          type="button"
          className="release-primary"
          disabled={!canPublish}
          onClick={() => void onPublish()}
        >
          Publish release
        </button>
        <button
          type="button"
          disabled={!canActivate}
          onClick={() => void onActivate()}
        >
          Activate
        </button>
        <button
          type="button"
          disabled={!canStart}
          onClick={() => void onStart()}
        >
          Start execution
        </button>
        <button
          type="button"
          disabled={locked || !executionId}
          onClick={() => void onRefreshStatus()}
        >
          Refresh status
        </button>
      </div>

      <dl className="release-summary">
        <div>
          <dt>Release</dt>
          <dd>{release?.release_id ?? "—"}</dd>
        </div>
        <div>
          <dt>Activation</dt>
          <dd>
            {activation?.release_id
              ? `rev ${activation.revision} · ${activation.release_id}`
              : activation
                ? "none active"
                : "—"}
          </dd>
        </div>
        <div>
          <dt>Execution</dt>
          <dd>{executionId || "—"}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{status ? statusSummary(status) : executionId ? "polling…" : "—"}</dd>
        </div>
      </dl>

      {executionId && onOpenTasks ? (
        <p className="release-tasks-link">
          <button
            type="button"
            className="release-link"
            disabled={locked}
            onClick={() => onOpenTasks(executionId)}
          >
            Open tasks for this run
          </button>
        </p>
      ) : executionId ? (
        <p className="release-tasks-hint" role="note">
          Task inbox filter: <code>run_id={executionId}</code>
        </p>
      ) : null}

      {error ? (
        <p className="release-error" role="alert">
          {error}
        </p>
      ) : null}
      {notice && !error ? (
        <p className="release-notice" role="status">
          {notice}
        </p>
      ) : null}
    </section>
  );
}

/** Optional e2e harness: set window.__NEXUS_RELEASE_CONTROLS_E2E__ before loading this module as entry. */
declare global {
  interface Window {
    __NEXUS_RELEASE_CONTROLS_E2E__?: ReleaseControlsProps;
  }
}

const e2eProps =
  typeof window !== "undefined" ? window.__NEXUS_RELEASE_CONTROLS_E2E__ : undefined;
const e2eRoot =
  typeof document !== "undefined" ? document.getElementById("root") : null;
if (e2eProps && e2eRoot && !e2eRoot.dataset.releaseControlsMounted) {
  e2eRoot.dataset.releaseControlsMounted = "1";
  createRoot(e2eRoot).render(<ReleaseControls {...e2eProps} />);
}
