/**
 * Package promote surface (F09-T03 / T05b React slice): Dev export → UAT
 * validate/stage → materialize → activate against existing signed APIs.
 *
 * Composition contract:
 * - connection — optional App session reuse (org/token; source env defaults)
 * - sourceClient / targetClient — injected PackageClient for tests
 * - releaseClient — injected ReleaseClient for UAT activation tests
 * - onClose — return to prior Dev-loop view
 *
 * Staging alone does not activate. Local-demo has no UAT grant by default.
 */
import { createElement, useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  createPackageClient,
  downloadPackageBytes,
  type ExportedPackage,
  type MaterializationReceipt,
  type PackageClient,
  type PackageImportMetadata,
  type PackageManifest,
} from "./package-api";
import {
  createReleaseClient,
  type ProjectActivation,
  type ReleaseClient,
} from "./release-api";
import { ProjectApiError, type ProjectConnection } from "./project-api";
import "./promote.css";
import "./tokens.css";

export interface PromoteProps {
  onClose: () => void;
  connection?: ProjectConnection;
  sourceClient?: PackageClient;
  targetClient?: PackageClient;
  /** Target-environment release client (activate / getActivation). */
  releaseClient?: ReleaseClient;
  /** Source-environment release client (lookup active Dev release). */
  sourceReleaseClient?: ReleaseClient;
}

type Session = {
  organizationId: string;
  token: string;
  sourceEnvironmentId: string;
  targetEnvironmentId: string;
  source: PackageClient;
  target: PackageClient;
  release: ReleaseClient;
  sourceRelease: ReleaseClient;
};

const failureMessages: Record<string, string> = {
  invalid_request:
    "Check identifiers, ZIP size (≤6 MiB HTTP upload), and configuration revision, then retry.",
  unauthorized: "The token was rejected. Mint a fresh bearer token.",
  forbidden:
    "This identity lacks package.export/import/activate in the selected environment. Local-demo grants business_author on Dev only; UAT needs an explicit grant.",
  not_found:
    "That project, release, staged import, or materialization was not found in this environment.",
  conflict:
    "Operation identity or expected revision conflicts. Reuse the same IDs for exact retries, or choose a new operation ID.",
  unavailable:
    "The host or readiness provider is unavailable. Confirm serve is running and destination configuration/catalog readiness.",
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

export default function Promote({
  onClose,
  connection,
  sourceClient: injectedSource,
  targetClient: injectedTarget,
  releaseClient: injectedRelease,
  sourceReleaseClient: injectedSourceRelease,
}: PromoteProps) {
  const [organizationId, setOrganizationId] = useState(
    connection?.organizationId ?? "local-demo",
  );
  const [sourceEnvironmentId, setSourceEnvironmentId] = useState(
    connection?.environmentId === "uat" || connection?.environmentId === "prod"
      ? "dev"
      : (connection?.environmentId ?? "dev"),
  );
  const [targetEnvironmentId, setTargetEnvironmentId] = useState("uat");
  const [token, setToken] = useState(connection?.token ?? "");
  const [session, setSession] = useState<Session | null>(null);

  const [projectId, setProjectId] = useState("welcome");
  const [releaseId, setReleaseId] = useState("");
  const [sourceActivation, setSourceActivation] =
    useState<ProjectActivation | null>(null);
  const [exported, setExported] = useState<ExportedPackage | null>(null);

  const [importOperationId, setImportOperationId] = useState(() =>
    newOperationId("uat-import"),
  );
  const [validated, setValidated] = useState<PackageManifest | null>(null);
  const [staged, setStaged] = useState<PackageImportMetadata | null>(null);
  const [imports, setImports] = useState<PackageImportMetadata[]>([]);

  const [materializeOperationId, setMaterializeOperationId] = useState(() =>
    newOperationId("uat-materialize"),
  );
  const [configurationRevision, setConfigurationRevision] =
    useState("local-v1");
  const [expectedProjectRevision, setExpectedProjectRevision] = useState("0");
  const [materialized, setMaterialized] =
    useState<MaterializationReceipt | null>(null);
  const [targetActivation, setTargetActivation] =
    useState<ProjectActivation | null>(null);
  const [activateOperationId, setActivateOperationId] = useState(() =>
    newOperationId("uat-activate"),
  );

  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!connection) return;
    void connectWith(
      {
        organizationId: connection.organizationId,
        token: connection.token,
        sourceEnvironmentId:
          connection.environmentId === "uat" ||
          connection.environmentId === "prod"
            ? "dev"
            : connection.environmentId,
        targetEnvironmentId: "uat",
      },
      injectedSource,
      injectedTarget,
      injectedRelease,
      injectedSourceRelease,
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

  async function refreshImports(client: PackageClient) {
    const page = await client.listImports(50);
    setImports(page.items);
    return page.items;
  }

  async function connectWith(
    next: {
      organizationId: string;
      token: string;
      sourceEnvironmentId: string;
      targetEnvironmentId: string;
    },
    sourceOverride?: PackageClient,
    targetOverride?: PackageClient,
    releaseOverride?: ReleaseClient,
    sourceReleaseOverride?: ReleaseClient,
  ) {
    if (!begin()) return;
    try {
      const sourceConnection: ProjectConnection = {
        organizationId: next.organizationId,
        environmentId: next.sourceEnvironmentId,
        token: next.token,
      };
      const targetConnection: ProjectConnection = {
        organizationId: next.organizationId,
        environmentId: next.targetEnvironmentId,
        token: next.token,
      };
      const source =
        sourceOverride ?? injectedSource ?? createPackageClient(sourceConnection);
      const target =
        targetOverride ?? injectedTarget ?? createPackageClient(targetConnection);
      const release =
        releaseOverride ??
        injectedRelease ??
        createReleaseClient(targetConnection);
      const sourceRelease =
        sourceReleaseOverride ??
        injectedSourceRelease ??
        createReleaseClient(sourceConnection);
      let listed: PackageImportMetadata[] = [];
      try {
        listed = await refreshImports(target);
      } catch (err) {
        // Source export may still work when UAT grant is missing.
        if (
          err instanceof ProjectApiError &&
          (err.code === "forbidden" || err.code === "unauthorized")
        ) {
          setImports([]);
          setSession({
            ...next,
            source,
            target,
            release,
            sourceRelease,
          });
          setError(
            `Connected source ${next.sourceEnvironmentId}, but target ${next.targetEnvironmentId} denied package.import. Grant business_author on UAT (local-demo is Dev-only by default), then reconnect. ${messageFor(err)}`,
          );
          return;
        }
        throw err;
      }
      setSession({
        ...next,
        source,
        target,
        release,
        sourceRelease,
      });
      setNotice(
        listed.length === 0
          ? `Connected. Export from ${next.sourceEnvironmentId}, then stage into ${next.targetEnvironmentId}. Staging does not activate.`
          : `Connected. ${listed.length} staged import${listed.length === 1 ? "" : "s"} in ${next.targetEnvironmentId}.`,
      );
    } catch (err) {
      setSession(null);
      setImports([]);
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    await connectWith({
      organizationId: organizationId.trim(),
      token: token.trim(),
      sourceEnvironmentId: sourceEnvironmentId.trim(),
      targetEnvironmentId: targetEnvironmentId.trim(),
    });
  }

  async function lookupSourceRelease() {
    if (!session) return;
    if (!begin()) return;
    try {
      const activation = await session.sourceRelease.getActivation(
        projectId.trim(),
      );
      setSourceActivation(activation);
      if (activation.release_id) {
        setReleaseId(activation.release_id);
        setNotice(
          `Source activation revision ${activation.revision} points at release ${activation.release_id}.`,
        );
      } else {
        setNotice(
          "Source project has no active release. Publish and activate in Dev first, or enter a release ID.",
        );
      }
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function onExport() {
    if (!session) return;
    if (!begin()) return;
    try {
      const next = await session.source.exportPackage(
        projectId.trim(),
        releaseId.trim(),
      );
      setExported(next);
      setValidated(null);
      setStaged(null);
      setMaterialized(null);
      setNotice(
        `Exported ${next.filename}${next.digest ? ` (${next.digest})` : ""}. Download or stage into ${session.targetEnvironmentId}.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  function onDownload() {
    if (!exported) return;
    downloadPackageBytes(exported);
    setNotice("Download started for nexus-flow-project.zip (private blob URL).");
  }

  async function onPickFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size < 1 || file.size > 6 * 1024 * 1024) {
      setError("ZIP must be between 1 byte and 6 MiB for HTTP upload.");
      return;
    }
    try {
      const bytes = await file.arrayBuffer();
      setExported({
        bytes,
        digest: null,
        filename: file.name || "nexus-flow-project.zip",
      });
      setValidated(null);
      setStaged(null);
      setMaterialized(null);
      setNotice(`Loaded ${file.name} (${file.size} bytes) for validate/stage.`);
    } catch {
      setError(failureMessages.invalid_request);
    }
  }

  async function onValidate() {
    if (!session || !exported) return;
    if (!begin()) return;
    try {
      const manifest = await session.target.validatePackage(exported.bytes);
      setValidated(manifest);
      setNotice(
        `Validated ${manifest.project_id} release ${manifest.release_id} (digest ${manifest.package_digest}). No storage or activation occurred.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function onStage() {
    if (!session || !exported) return;
    if (!begin()) return;
    try {
      const receipt = await session.target.stageImport(
        importOperationId.trim(),
        exported.bytes,
      );
      setStaged(receipt);
      setMaterialized(null);
      await refreshImports(session.target);
      setNotice(
        `Staged import ${receipt.operation_id} revision ${receipt.revision}. Materialize separately; activation remains unchanged.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function onMaterialize() {
    if (!session || !staged) return;
    if (!begin()) return;
    try {
      const expected = Number(expectedProjectRevision);
      if (!Number.isSafeInteger(expected) || expected < 0)
        throw new ProjectApiError("invalid_request");
      const receipt = await session.target.materialize(staged.operation_id, {
        operationId: materializeOperationId.trim(),
        expectedProjectRevision: expected,
        configurationRevision: configurationRevision.trim(),
      });
      setMaterialized(receipt);
      setNotice(
        `Materialized destination release ${receipt.release_id} at project revision ${receipt.revision}. Activate explicitly next.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function onActivate() {
    if (!session || !materialized) return;
    if (!begin()) return;
    try {
      const current = await session.release.getActivation(materialized.project_id);
      setTargetActivation(current);
      const next = await session.release.activate(materialized.project_id, {
        releaseId: materialized.release_id,
        configurationRevision: materialized.configuration_revision,
        operationId: activateOperationId.trim(),
        expectedRevision: current.revision,
        runtimeVersion: "proof.v1",
        action: "activate",
      });
      setTargetActivation(next);
      setNotice(
        `Activated ${next.release_id} in ${session.targetEnvironmentId} (activation revision ${next.revision}). New starts use this release.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function selectStaged(item: PackageImportMetadata) {
    if (!session) return;
    if (!begin()) return;
    try {
      const detail = await session.target.getImport(item.operation_id);
      setStaged(detail);
      setImportOperationId(detail.operation_id);
      setMaterialized(null);
      setNotice(
        `Selected staged import ${detail.operation_id} for project ${detail.project_id}.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  return (
    <div className="nf-promote" aria-label="Package promote">
      <header className="nf-promote-header">
        <div>
          <span className="nf-promote-eyebrow">NEXUS FLOW DESIGNER</span>
          <h1>Promote</h1>
        </div>
        <div className="nf-promote-header-actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </header>
      <p className="nf-promote-boundary">
        Export a published Dev release ZIP, validate and stage it into UAT,
        materialize a destination release, then activate. Import alone never
        activates. Local-demo tokens are Dev-only by default — UAT needs an
        explicit <code>business_author</code> grant. This UI is not multi-tenant
        Entra or production-provider acceptance.
      </p>

      {!session && (
        <form className="nf-promote-connect" onSubmit={connect}>
          <h2>Connect</h2>
          <p>
            Same org bearer token for source export and target import. Default
            path is Dev → UAT.
          </p>
          <fieldset disabled={busy}>
            <label htmlFor="promote-org">Organization ID</label>
            <input
              id="promote-org"
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
              autoComplete="off"
              required
            />
            <div className="nf-promote-env-row">
              <div>
                <label htmlFor="promote-source-env">Source environment</label>
                <input
                  id="promote-source-env"
                  value={sourceEnvironmentId}
                  onChange={(event) =>
                    setSourceEnvironmentId(event.target.value)
                  }
                  autoComplete="off"
                  required
                />
              </div>
              <div>
                <label htmlFor="promote-target-env">Target environment</label>
                <input
                  id="promote-target-env"
                  value={targetEnvironmentId}
                  onChange={(event) =>
                    setTargetEnvironmentId(event.target.value)
                  }
                  autoComplete="off"
                  required
                />
              </div>
            </div>
            <label htmlFor="promote-token">Access token</label>
            <input
              id="promote-token"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
              required
            />
            <button type="submit" className="nf-promote-primary">
              Connect
            </button>
          </fieldset>
        </form>
      )}

      {error && (
        <p className="nf-promote-error" role="alert">
          {error}
        </p>
      )}
      {notice && !error && (
        <p className="nf-promote-notice" role="status">
          {notice}
        </p>
      )}

      {session && (
        <div className="nf-promote-layout">
          <section className="nf-promote-panel" aria-label="Export from source">
            <h2>Export from {session.sourceEnvironmentId}</h2>
            <p>
              Download a portable whole-project ZIP for a published release.
              Draft edits are not included.
            </p>
            <fieldset disabled={busy}>
              <label htmlFor="promote-project">Project ID</label>
              <input
                id="promote-project"
                value={projectId}
                onChange={(event) => setProjectId(event.target.value)}
                autoComplete="off"
                required
              />
              <label htmlFor="promote-release">Release ID</label>
              <input
                id="promote-release"
                value={releaseId}
                onChange={(event) => setReleaseId(event.target.value)}
                autoComplete="off"
                required
              />
            </fieldset>
            <div className="nf-promote-actions">
              <button
                type="button"
                disabled={busy}
                onClick={() => void lookupSourceRelease()}
              >
                Use active release
              </button>
              <button
                type="button"
                className="nf-promote-primary"
                disabled={busy || !projectId.trim() || !releaseId.trim()}
                onClick={() => void onExport()}
              >
                Export package
              </button>
              <button
                type="button"
                disabled={busy || !exported}
                onClick={onDownload}
              >
                Download ZIP
              </button>
            </div>
            {sourceActivation && (
              <ul className="nf-promote-meta">
                <li>
                  Source activation revision {sourceActivation.revision}
                  {sourceActivation.release_id
                    ? ` · ${sourceActivation.release_id}`
                    : " · none"}
                </li>
              </ul>
            )}
            {exported && (
              <>
                <h3>Exported package</h3>
                <ul className="nf-promote-meta">
                  <li>{exported.filename}</li>
                  <li>{exported.bytes.byteLength} bytes</li>
                  {exported.digest && (
                    <li>
                      Digest <code>{exported.digest}</code>
                    </li>
                  )}
                </ul>
              </>
            )}
          </section>

          <section className="nf-promote-panel" aria-label="Import to target">
            <h2>Import to {session.targetEnvironmentId}</h2>
            <p>
              Validate without saving, or stage an immutable import. Reuse the
              same operation ID for exact retries.
            </p>
            <fieldset disabled={busy}>
              <label htmlFor="promote-zip">Package ZIP (optional upload)</label>
              <input
                id="promote-zip"
                ref={fileRef}
                type="file"
                accept=".zip,application/zip"
                onChange={(event) => void onPickFile(event)}
              />
              <label htmlFor="promote-import-op">Import operation ID</label>
              <input
                id="promote-import-op"
                value={importOperationId}
                onChange={(event) => setImportOperationId(event.target.value)}
                autoComplete="off"
                required
              />
            </fieldset>
            <div className="nf-promote-actions">
              <button
                type="button"
                disabled={busy || !exported}
                onClick={() => void onValidate()}
              >
                Validate package
              </button>
              <button
                type="button"
                className="nf-promote-primary"
                disabled={busy || !exported || !importOperationId.trim()}
                onClick={() => void onStage()}
              >
                Stage import
              </button>
            </div>
            {validated && (
              <>
                <h3>Validated manifest</h3>
                <ul className="nf-promote-meta">
                  <li>
                    {validated.project_id} · release {validated.release_id}
                  </li>
                  <li>Source revision {validated.source_revision}</li>
                  <li>
                    Digest <code>{validated.package_digest}</code>
                  </li>
                  <li>{validated.activity_refs.length} pinned activities</li>
                </ul>
              </>
            )}
            {staged && (
              <>
                <h3>Staged receipt</h3>
                <ul className="nf-promote-meta">
                  <li>
                    {staged.operation_id} · revision {staged.revision}
                  </li>
                  <li>
                    {staged.project_id} / {staged.release_id}
                  </li>
                  <li>
                    Digest <code>{staged.package_digest}</code>
                  </li>
                  <li>
                    Uploader {staged.actor_id} · {staged.created_at}
                  </li>
                </ul>
              </>
            )}
            <h3>Staged imports</h3>
            {imports.length === 0 ? (
              <p className="nf-promote-empty">No staged imports in target.</p>
            ) : (
              <ul className="nf-promote-list">
                {imports.map((item) => (
                  <li key={item.operation_id}>
                    <button
                      type="button"
                      aria-current={
                        staged?.operation_id === item.operation_id
                          ? "true"
                          : undefined
                      }
                      disabled={busy}
                      onClick={() => void selectStaged(item)}
                    >
                      <span className="nf-promote-list-title">
                        {item.operation_id}
                      </span>
                      <span className="nf-promote-list-meta">
                        {item.project_id} · rev {item.revision} ·{" "}
                        {item.package_digest.slice(0, 18)}…
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section
            className="nf-promote-panel"
            aria-label="Materialize destination release"
          >
            <h2>Materialize</h2>
            <p>
              Create a destination project revision and release from a staged
              import. Requires approved catalog artifacts and configuration
              readiness. Does not activate.
            </p>
            <fieldset disabled={busy || !staged}>
              <label htmlFor="promote-mat-op">Materialization operation ID</label>
              <input
                id="promote-mat-op"
                value={materializeOperationId}
                onChange={(event) =>
                  setMaterializeOperationId(event.target.value)
                }
                autoComplete="off"
              />
              <label htmlFor="promote-config">Configuration revision</label>
              <input
                id="promote-config"
                value={configurationRevision}
                onChange={(event) =>
                  setConfigurationRevision(event.target.value)
                }
                autoComplete="off"
              />
              <label htmlFor="promote-expected">
                Expected project revision (0 = new project)
              </label>
              <input
                id="promote-expected"
                value={expectedProjectRevision}
                onChange={(event) =>
                  setExpectedProjectRevision(event.target.value)
                }
                inputMode="numeric"
                autoComplete="off"
              />
            </fieldset>
            <div className="nf-promote-actions">
              <button
                type="button"
                className="nf-promote-primary"
                disabled={busy || !staged || !materializeOperationId.trim()}
                onClick={() => void onMaterialize()}
              >
                Materialize release
              </button>
            </div>
            {materialized && (
              <>
                <h3>Materialization receipt</h3>
                <ul className="nf-promote-meta">
                  <li>
                    {materialized.operation_id} · {materialized.state}
                  </li>
                  <li>
                    Destination release <code>{materialized.release_id}</code>
                  </li>
                  <li>
                    Project {materialized.project_id} revision{" "}
                    {materialized.revision}
                  </li>
                  <li>
                    Config {materialized.configuration_revision} ·{" "}
                    {materialized.runtime_version}
                  </li>
                </ul>
              </>
            )}
          </section>

          <section
            className="nf-promote-panel"
            aria-label="Activate in target"
          >
            <h2>Activate in {session.targetEnvironmentId}</h2>
            <p>
              Point the target project at the materialized release. Running
              executions stay pinned to their original release.
            </p>
            <fieldset disabled={busy || !materialized}>
              <label htmlFor="promote-act-op">Activation operation ID</label>
              <input
                id="promote-act-op"
                value={activateOperationId}
                onChange={(event) => setActivateOperationId(event.target.value)}
                autoComplete="off"
              />
            </fieldset>
            <div className="nf-promote-actions">
              <button
                type="button"
                className="nf-promote-primary"
                disabled={busy || !materialized || !activateOperationId.trim()}
                onClick={() => void onActivate()}
              >
                Activate release
              </button>
            </div>
            {targetActivation && (
              <>
                <h3>Target activation</h3>
                <ul className="nf-promote-meta">
                  <li>Revision {targetActivation.revision}</li>
                  <li>
                    Release{" "}
                    {targetActivation.release_id ? (
                      <code>{targetActivation.release_id}</code>
                    ) : (
                      "none"
                    )}
                  </li>
                  {targetActivation.operation_id && (
                    <li>Operation {targetActivation.operation_id}</li>
                  )}
                </ul>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

/** Browser/e2e mount helper for coordinator App wiring and local harnesses. */
export async function mountPromote(
  target: string | HTMLElement,
  props: PromoteProps,
): Promise<Root> {
  const element =
    typeof target === "string" ? document.getElementById(target) : target;
  if (!element) throw new Error("Promote mount target was not found");
  const root = createRoot(element);
  root.render(createElement(Promote, props));
  return root;
}
