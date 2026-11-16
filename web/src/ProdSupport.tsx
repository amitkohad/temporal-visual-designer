/**
 * Prod support + evidence/attachments surface (F09-T03 / T05b React slice).
 *
 * Composition contract:
 * - connection — optional App session reuse (org/token; env defaults to prod)
 * - deploymentClient / releaseClient — injected for tests
 * - onClose — return to prior Dev-loop view
 *
 * Local-demo has no Prod production_support grant by default.
 */
import { createElement, useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  createDeploymentClient,
  downloadAttachmentBytes,
  type ActivationHistoryPage,
  type ActivationSummary,
  type AttachmentMetadata,
  type DeploymentClient,
} from "./deployment-api";
import {
  createReleaseClient,
  type ProjectActivation,
  type ReleaseClient,
} from "./release-api";
import { ProjectApiError, type ProjectConnection } from "./project-api";
import "./prod-support.css";
import "./tokens.css";

export interface ProdSupportProps {
  onClose: () => void;
  connection?: ProjectConnection;
  deploymentClient?: DeploymentClient;
  releaseClient?: ReleaseClient;
}

type Session = {
  organizationId: string;
  environmentId: string;
  token: string;
  deployment: DeploymentClient;
  release: ReleaseClient;
};

const failureMessages: Record<string, string> = {
  invalid_request:
    "Check identifiers, evidence bounds (change request ≤128, notes ≤2000), and attachment size (1–2 MiB), then retry.",
  unauthorized: "The token was rejected. Mint a fresh bearer token.",
  forbidden:
    "This identity lacks package.activate / run.read in the selected environment. Local-demo grants business_author on Dev only; Prod needs an explicit production_support grant.",
  not_found:
    "That project, activation operation, or attachment was not found in this environment.",
  conflict:
    "Operation identity, expected revision, or attachment bytes/label conflict. Reuse exact IDs for retries, or choose a new ID.",
  unavailable:
    "The host or attachment store is unavailable. Confirm serve is running and activations.db is intact.",
  invalid_response: "The host returned an unexpected response.",
};

function messageFor(error: unknown): string {
  if (error instanceof ProjectApiError)
    return failureMessages[error.code] ?? failureMessages.unavailable;
  if (error instanceof Error && error.message) return error.message;
  return failureMessages.unavailable;
}

function newOperationId(prefix: string): string {
  const stamp = Date.now().toString(36);
  const random = Math.random().toString(36).slice(2, 8);
  return `${prefix}-${stamp}-${random}`;
}

function evidenceLabel(summary: ActivationSummary | null): string {
  if (!summary?.evidence) return "No text evidence recorded";
  const change = summary.evidence.change_request_number?.trim();
  const notes = summary.evidence.notes?.trim();
  if (!change && !notes) return "Empty evidence object recorded";
  const parts: string[] = [];
  if (change) parts.push(`CR ${change}`);
  if (notes) parts.push(notes.length > 80 ? `${notes.slice(0, 80)}…` : notes);
  return parts.join(" · ");
}

export default function ProdSupport({
  onClose,
  connection,
  deploymentClient: injectedDeployment,
  releaseClient: injectedRelease,
}: ProdSupportProps) {
  const [organizationId, setOrganizationId] = useState(
    connection?.organizationId ?? "local-demo",
  );
  const [environmentId, setEnvironmentId] = useState(
    connection?.environmentId === "dev" || connection?.environmentId === "uat"
      ? "prod"
      : (connection?.environmentId ?? "prod"),
  );
  const [token, setToken] = useState(connection?.token ?? "");
  const [session, setSession] = useState<Session | null>(null);

  const [projectId, setProjectId] = useState("welcome");
  const [current, setCurrent] = useState<ProjectActivation | null>(null);
  const [history, setHistory] = useState<ActivationHistoryPage | null>(null);
  const [selected, setSelected] = useState<ActivationSummary | null>(null);
  const [attachments, setAttachments] = useState<AttachmentMetadata[]>([]);

  const [releaseId, setReleaseId] = useState("");
  const [configurationRevision, setConfigurationRevision] =
    useState("local-v1");
  const [expectedRevision, setExpectedRevision] = useState("0");
  const [activateOperationId, setActivateOperationId] = useState(() =>
    newOperationId("prod-activate"),
  );
  const [changeRequest, setChangeRequest] = useState("");
  const [notes, setNotes] = useState("");

  const [attachmentId, setAttachmentId] = useState(() =>
    newOperationId("approval"),
  );
  const [displayName, setDisplayName] = useState("Approval evidence");
  const fileRef = useRef<HTMLInputElement>(null);

  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!connection) return;
    void connectWith(
      {
        organizationId: connection.organizationId,
        token: connection.token,
        environmentId:
          connection.environmentId === "dev" ||
          connection.environmentId === "uat"
            ? "prod"
            : connection.environmentId,
      },
      injectedDeployment,
      injectedRelease,
    );
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

  async function loadHistory(
    client: DeploymentClient,
    project: string,
  ): Promise<ActivationHistoryPage> {
    const page = await client.listActivations(project, 50);
    setHistory(page);
    return page;
  }

  async function loadAttachments(
    client: DeploymentClient,
    project: string,
    operationId: string,
  ) {
    const listed = await client.listAttachments(project, operationId);
    setAttachments(listed);
    return listed;
  }

  async function connectWith(
    next: {
      organizationId: string;
      environmentId: string;
      token: string;
    },
    deploymentOverride?: DeploymentClient,
    releaseOverride?: ReleaseClient,
  ) {
    if (!begin()) return;
    try {
      const projectConnection: ProjectConnection = {
        organizationId: next.organizationId,
        environmentId: next.environmentId,
        token: next.token,
      };
      const deployment =
        deploymentOverride ??
        injectedDeployment ??
        createDeploymentClient(projectConnection);
      const release =
        releaseOverride ??
        injectedRelease ??
        createReleaseClient(projectConnection);
      let page: ActivationHistoryPage;
      try {
        page = await loadHistory(deployment, projectId.trim() || "welcome");
      } catch (err) {
        if (
          err instanceof ProjectApiError &&
          (err.code === "forbidden" || err.code === "unauthorized")
        ) {
          setSession({
            ...next,
            deployment,
            release,
          });
          setHistory(null);
          setSelected(null);
          setAttachments([]);
          setCurrent(null);
          setError(
            `Connected session for ${next.environmentId}, but history/read was denied. Grant production_support on Prod (local-demo is Dev-only by default), then reconnect. ${messageFor(err)}`,
          );
          return;
        }
        throw err;
      }
      let activation: ProjectActivation | null = null;
      try {
        activation = await release.getActivation(projectId.trim() || "welcome");
        setCurrent(activation);
      } catch {
        setCurrent(null);
      }
      setSession({
        ...next,
        deployment,
        release,
      });
      setSelected(null);
      setAttachments([]);
      setNotice(
        page.items.length === 0
          ? `Connected to ${next.environmentId}. No successful deployment history yet. Activate with optional evidence, then attach files.`
          : `Connected to ${next.environmentId}. ${page.items.length} successful operation${page.items.length === 1 ? "" : "s"} in history.`,
      );
      if (activation?.release_id) {
        setReleaseId(activation.release_id);
        setExpectedRevision(String(activation.revision));
        if (activation.configuration_revision)
          setConfigurationRevision(activation.configuration_revision);
      }
    } catch (err) {
      setSession(null);
      setHistory(null);
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

  async function refresh() {
    if (!session) return;
    if (!begin()) return;
    try {
      const page = await loadHistory(session.deployment, projectId.trim());
      const activation = await session.release.getActivation(projectId.trim());
      setCurrent(activation);
      if (selected) {
        const match = page.items.find(
          (item) => item.operation_id === selected.operation_id,
        );
        if (match) {
          setSelected(match);
          await loadAttachments(
            session.deployment,
            projectId.trim(),
            match.operation_id,
          );
        } else {
          setSelected(null);
          setAttachments([]);
        }
      }
      setNotice(
        `Refreshed ${projectId.trim()} in ${session.environmentId}: ${page.items.length} history item${page.items.length === 1 ? "" : "s"}.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function selectOperation(item: ActivationSummary) {
    if (!session) return;
    if (!begin()) return;
    try {
      const detail = await session.deployment.getActivationOperation(
        projectId.trim(),
        item.operation_id,
      );
      setSelected(detail);
      await loadAttachments(
        session.deployment,
        projectId.trim(),
        detail.operation_id,
      );
      setNotice(
        `Loaded operation ${detail.operation_id} (${detail.action} · rev ${detail.revision}).`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function onActivate() {
    if (!session) return;
    if (!begin()) return;
    try {
      const expected = Number(expectedRevision);
      if (!Number.isSafeInteger(expected) || expected < 0)
        throw new ProjectApiError("invalid_request");
      const change = changeRequest.trim();
      const noteText = notes.trim();
      const evidence =
        change || noteText
          ? {
              ...(change ? { change_request_number: change } : {}),
              ...(noteText ? { notes: noteText } : {}),
            }
          : undefined;
      const activation = await session.release.activate(projectId.trim(), {
        releaseId: releaseId.trim(),
        configurationRevision: configurationRevision.trim(),
        operationId: activateOperationId.trim(),
        expectedRevision: expected,
        runtimeVersion: "proof.v1",
        action: "activate",
        evidence,
      });
      setCurrent(activation);
      const page = await loadHistory(session.deployment, projectId.trim());
      const created = page.items.find(
        (item) => item.operation_id === activateOperationId.trim(),
      );
      if (created) {
        setSelected(created);
        await loadAttachments(
          session.deployment,
          projectId.trim(),
          created.operation_id,
        );
      }
      setExpectedRevision(String(activation.revision));
      setNotice(
        `Activated ${activation.release_id} in ${session.environmentId} as ${activation.operation_id}. Attachments can be uploaded next; missing text evidence never blocks an authorized Prod deploy.`,
      );
      setActivateOperationId(newOperationId("prod-activate"));
    } catch (err) {
      if (err instanceof ProjectApiError && err.code === "forbidden") {
        setError(
          `Prod activation denied. Local-demo business_author cannot deploy to Prod — add a production_support grant on identity.db for this user, then retry. ${messageFor(err)}`,
        );
      } else {
        setError(messageFor(err));
      }
    } finally {
      end();
    }
  }

  async function onPickFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !session || !selected) return;
    if (file.size < 1 || file.size > 2_097_152) {
      setError("Attachment must be between 1 byte and 2 MiB.");
      return;
    }
    if (!begin()) return;
    try {
      const metadata = await session.deployment.uploadAttachment(
        projectId.trim(),
        selected.operation_id,
        attachmentId.trim(),
        file,
        displayName.trim() || "Approval evidence",
      );
      await loadAttachments(
        session.deployment,
        projectId.trim(),
        selected.operation_id,
      );
      setNotice(
        `Uploaded ${metadata.attachment_id} (${metadata.size} bytes, ${metadata.checksum}). Upload does not change the deployment receipt.`,
      );
      setAttachmentId(newOperationId("approval"));
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function onDownload(attachment: AttachmentMetadata) {
    if (!session || !selected) return;
    if (!begin()) return;
    try {
      const downloaded = await session.deployment.downloadAttachment(
        projectId.trim(),
        selected.operation_id,
        attachment.attachment_id,
      );
      downloadAttachmentBytes(
        downloaded,
        `${attachment.attachment_id}.bin`,
      );
      setNotice(
        `Download started for ${attachment.attachment_id} as opaque deployment-evidence bytes (private blob URL).`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  return (
    <div className="nf-prod" aria-label="Prod support">
      <header className="nf-prod-header">
        <div>
          <div className="nf-prod-eyebrow">NEXUS FLOW DESIGNER</div>
          <h1>Prod support</h1>
        </div>
        <div className="nf-prod-header-actions">
          {session && (
            <button type="button" disabled={busy} onClick={() => void refresh()}>
              Refresh
            </button>
          )}
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </header>
      <p className="nf-prod-boundary">
        Signed activation history, optional evidence text, and bounded evidence
        file upload/list/download against existing APIs. Local-demo tokens are
        not multi-tenant Entra; Prod needs an explicit production_support grant
        (Dev business_author alone is denied). Attachments never block
        activation and are not scanned.
      </p>

      {!session && (
        <form className="nf-prod-connect" onSubmit={(event) => void connect(event)}>
          <h2>Connect</h2>
          <p>
            Reuse the Projects bearer token. Default environment is Prod.
            History requires run.read; activate and upload require
            package.activate via production_support.
          </p>
          <fieldset disabled={busy}>
            <label htmlFor="prod-org">Organization</label>
            <input
              id="prod-org"
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
              autoComplete="off"
            />
            <label htmlFor="prod-env">Environment</label>
            <input
              id="prod-env"
              value={environmentId}
              onChange={(event) => setEnvironmentId(event.target.value)}
              autoComplete="off"
            />
            <label htmlFor="prod-token">Bearer token</label>
            <input
              id="prod-token"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
            />
            <label htmlFor="prod-project">Project ID</label>
            <input
              id="prod-project"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              autoComplete="off"
            />
          </fieldset>
          <button
            type="submit"
            className="nf-prod-primary"
            disabled={busy || !token.trim() || !organizationId.trim()}
          >
            Connect
          </button>
        </form>
      )}

      {error && (
        <div className="nf-prod-error" role="alert">
          {error}
        </div>
      )}
      {notice && !error && <div className="nf-prod-notice">{notice}</div>}

      {session && (
        <div className="nf-prod-layout">
          <section className="nf-prod-panel" aria-label="Current activation">
            <h2>Current activation</h2>
            <p>
              Environment <code>{session.environmentId}</code> · project{" "}
              <code>{projectId}</code>. Refresh after deployments elsewhere.
            </p>
            <fieldset disabled={busy}>
              <label htmlFor="prod-project-live">Project ID</label>
              <input
                id="prod-project-live"
                value={projectId}
                onChange={(event) => setProjectId(event.target.value)}
                autoComplete="off"
              />
            </fieldset>
            <div className="nf-prod-actions">
              <button type="button" disabled={busy} onClick={() => void refresh()}>
                Load project
              </button>
            </div>
            {current ? (
              <ul className="nf-prod-meta">
                <li>Revision {current.revision}</li>
                <li>
                  Release{" "}
                  {current.release_id ? (
                    <code>{current.release_id}</code>
                  ) : (
                    "none"
                  )}
                </li>
                {current.operation_id && (
                  <li>Operation {current.operation_id}</li>
                )}
                {current.evidence && (
                  <li>
                    Evidence{" "}
                    {current.evidence.change_request_number ||
                      current.evidence.notes ||
                      "(empty object)"}
                  </li>
                )}
              </ul>
            ) : (
              <p className="nf-prod-empty">No current activation loaded.</p>
            )}
          </section>

          <section
            className="nf-prod-panel"
            aria-label="Activate with optional evidence"
          >
            <h2>Activate in {session.environmentId}</h2>
            <p>
              Optional change-request / notes are immutable with the operation.
              Authorized Prod activation is not blocked when evidence is
              omitted; unauthorized identities (including Dev-only authors) are
              rejected with a clear forbidden response.
            </p>
            <fieldset disabled={busy}>
              <label htmlFor="prod-release">Release ID</label>
              <input
                id="prod-release"
                value={releaseId}
                onChange={(event) => setReleaseId(event.target.value)}
                autoComplete="off"
              />
              <label htmlFor="prod-config">Configuration revision</label>
              <input
                id="prod-config"
                value={configurationRevision}
                onChange={(event) =>
                  setConfigurationRevision(event.target.value)
                }
                autoComplete="off"
              />
              <label htmlFor="prod-expected">Expected activation revision</label>
              <input
                id="prod-expected"
                value={expectedRevision}
                onChange={(event) => setExpectedRevision(event.target.value)}
                inputMode="numeric"
                autoComplete="off"
              />
              <label htmlFor="prod-act-op">Activation operation ID</label>
              <input
                id="prod-act-op"
                value={activateOperationId}
                onChange={(event) => setActivateOperationId(event.target.value)}
                autoComplete="off"
              />
              <label htmlFor="prod-cr">Change request (optional)</label>
              <input
                id="prod-cr"
                value={changeRequest}
                onChange={(event) => setChangeRequest(event.target.value)}
                autoComplete="off"
              />
              <label htmlFor="prod-notes">Notes (optional)</label>
              <textarea
                id="prod-notes"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </fieldset>
            <div className="nf-prod-actions">
              <button
                type="button"
                className="nf-prod-primary"
                disabled={
                  busy ||
                  !releaseId.trim() ||
                  !activateOperationId.trim() ||
                  !configurationRevision.trim()
                }
                onClick={() => void onActivate()}
              >
                Activate release
              </button>
            </div>
          </section>

          <section className="nf-prod-panel" aria-label="Deployment history">
            <h2>Deployment history</h2>
            <p>
              Successful operations only, newest first. Select one to view
              evidence and manage attachments.
            </p>
            {!history || history.items.length === 0 ? (
              <p className="nf-prod-empty">No successful operations yet.</p>
            ) : (
              <ul className="nf-prod-list">
                {history.items.map((item) => (
                  <li key={item.operation_id}>
                    <button
                      type="button"
                      aria-current={
                        selected?.operation_id === item.operation_id
                          ? "true"
                          : undefined
                      }
                      disabled={busy}
                      onClick={() => void selectOperation(item)}
                    >
                      <span className="nf-prod-list-title">
                        {item.operation_id}
                      </span>
                      <span className="nf-prod-list-meta">
                        {item.action} · rev {item.revision} · {item.release_id}{" "}
                        · {evidenceLabel(item)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {selected && (
              <>
                <h3>Selected operation</h3>
                <ul className="nf-prod-meta">
                  <li>
                    {selected.operation_id} · {selected.action} · rev{" "}
                    {selected.revision}
                  </li>
                  <li>
                    Release <code>{selected.release_id}</code>
                  </li>
                  <li>{evidenceLabel(selected)}</li>
                  {selected.evidence?.notes && (
                    <li>Notes: {selected.evidence.notes}</li>
                  )}
                </ul>
              </>
            )}
          </section>

          <section
            className="nf-prod-panel"
            aria-label="Evidence attachments"
          >
            <h2>Evidence attachments</h2>
            <p>
              Upload opaque files after a successful operation (max 10 × 2 MiB).
              Listing is metadata-only; download returns verified bytes named
              deployment-evidence.bin.
            </p>
            {!selected ? (
              <p className="nf-prod-empty">
                Select a history operation to upload or download attachments.
              </p>
            ) : (
              <>
                <fieldset disabled={busy}>
                  <label htmlFor="prod-att-id">Attachment ID</label>
                  <input
                    id="prod-att-id"
                    value={attachmentId}
                    onChange={(event) => setAttachmentId(event.target.value)}
                    autoComplete="off"
                  />
                  <label htmlFor="prod-att-name">Display name</label>
                  <input
                    id="prod-att-name"
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    autoComplete="off"
                  />
                </fieldset>
                <div className="nf-prod-actions">
                  <button
                    type="button"
                    className="nf-prod-primary"
                    disabled={busy || !attachmentId.trim()}
                    onClick={() => fileRef.current?.click()}
                  >
                    Upload file
                  </button>
                  <input
                    ref={fileRef}
                    className="nf-prod-file"
                    type="file"
                    hidden
                    onChange={(event) => void onPickFile(event)}
                  />
                </div>
                {attachments.length === 0 ? (
                  <p className="nf-prod-empty">No attachments on this operation.</p>
                ) : (
                  <ul className="nf-prod-list">
                    {attachments.map((item) => (
                      <li key={item.attachment_id}>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void onDownload(item)}
                        >
                          <span className="nf-prod-list-title">
                            {item.display_name}
                          </span>
                          <span className="nf-prod-list-meta">
                            {item.attachment_id} · {item.size} bytes ·{" "}
                            {item.checksum.slice(0, 18)}… · download
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

/** Browser/e2e mount helper for coordinator App wiring and local harnesses. */
export async function mountProdSupport(
  target: string | HTMLElement,
  props: ProdSupportProps,
): Promise<Root> {
  const element =
    typeof target === "string" ? document.getElementById(target) : target;
  if (!element) throw new Error("Prod support mount target was not found");
  const root = createRoot(element);
  root.render(createElement(ProdSupport, props));
  return root;
}
