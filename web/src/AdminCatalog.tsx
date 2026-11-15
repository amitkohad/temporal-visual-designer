/**
 * Admin catalog publish lifecycle surface (F04-T004).
 *
 * Composition contract:
 * - connection — optional App session reuse (org/env/token)
 * - client — injected CatalogClient for tests
 * - onClose — return to prior Dev-loop view
 *
 * Against existing signed APIs only: import → publish → search → deprecate/withdraw.
 * Does not execute activities or claim worker/capability acceptance.
 */
import { createElement, useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  activityRefFromImport,
  catalogDisplayLabel,
  createCatalogClient,
  type ActivityImport,
  type CatalogClient,
  type CatalogEntry,
  type TransitionTarget,
} from "./catalog-api";
import { ProjectApiError, type ProjectConnection } from "./project-api";
import "./admin-catalog.css";
import "./tokens.css";

export interface AdminCatalogProps {
  onClose: () => void;
  connection?: ProjectConnection;
  /** Injected client for tests; production uses createCatalogClient(connection). */
  client?: CatalogClient;
}

type Session = {
  connection: ProjectConnection;
  client: CatalogClient;
};

const failureMessages: Record<string, string> = {
  invalid_request:
    "Check the organization, environment, token, and ZIP bytes, then retry.",
  unauthorized: "The token was rejected. Mint a fresh publisher-capable token.",
  forbidden:
    "This identity cannot import or publish activities. Grant activity_publisher (local-demo has no publisher rights by default).",
  not_found: "That import or catalog entry was not found in this environment.",
  conflict:
    "A conflicting generation or digest exists. Reload the entry and retry with the current generation.",
  unavailable:
    "The host or capability provider is unavailable. Confirm serve is running with a compatible worker capability.",
  invalid_response: "The host returned an unexpected response.",
};

function messageFor(error: unknown): string {
  if (error instanceof ProjectApiError)
    return failureMessages[error.code] ?? failureMessages.unavailable;
  if (error instanceof Error && error.message) return error.message;
  return failureMessages.unavailable;
}

export default function AdminCatalog({
  onClose,
  connection,
  client: injected,
}: AdminCatalogProps) {
  const [organizationId, setOrganizationId] = useState(
    connection?.organizationId ?? "local-demo",
  );
  const [environmentId, setEnvironmentId] = useState(
    connection?.environmentId ?? "dev",
  );
  const [token, setToken] = useState(connection?.token ?? "");
  const [session, setSession] = useState<Session | null>(null);
  const [imported, setImported] = useState<ActivityImport | null>(null);
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [selected, setSelected] = useState<CatalogEntry | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!connection) return;
    void connectWith(connection, injected);
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

  async function refreshList(client: CatalogClient, search = query) {
    const listed = await client.search(search.trim(), 50, 0);
    setEntries(listed);
    if (selected) {
      const match = listed.find(
        (item) =>
          item.reference.activity_id === selected.reference.activity_id &&
          item.reference.version === selected.reference.version &&
          item.reference.artifact_digest === selected.reference.artifact_digest,
      );
      if (match) setSelected(match);
    }
    return listed;
  }

  async function connectWith(
    next: ProjectConnection,
    clientOverride?: CatalogClient,
  ) {
    if (!begin()) return;
    try {
      const client = clientOverride ?? injected ?? createCatalogClient(next);
      const listed = await client.search("", 50, 0);
      setSession({ connection: next, client });
      setEntries(listed);
      setNotice(
        listed.length === 0
          ? "Connected. Import a validated ZIP to publish, or search once entries exist."
          : `Connected. ${listed.length} published catalog entr${listed.length === 1 ? "y" : "ies"} visible.`,
      );
    } catch (err) {
      setSession(null);
      setEntries([]);
      setSelected(null);
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

  async function onImportFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!session || !file) return;
    if (!begin()) return;
    try {
      if (file.size < 1 || file.size > 6 * 1024 * 1024) {
        throw new Error(
          "ZIP must be between 1 byte and 6 MiB for HTTP upload.",
        );
      }
      const next = await session.client.importBundle(file);
      setImported(next);
      const label = catalogDisplayLabel(next);
      setNotice(
        `Validated import ${label.displayName} (${label.activityId}@${label.version}). Publish when ready.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function onPublish() {
    if (!session || !imported) return;
    if (!begin()) return;
    try {
      const entry = await session.client.publish(imported.digest);
      setSelected(entry);
      setImported(null);
      await refreshList(session.client);
      const label = catalogDisplayLabel(entry);
      setNotice(
        `Published ${label.displayName} generation ${entry.generation}. Palette readers can resolve this version.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function onSearch(event: React.FormEvent) {
    event.preventDefault();
    if (!session) return;
    if (!begin()) return;
    try {
      const listed = await refreshList(session.client);
      setNotice(
        listed.length === 0
          ? "No published (non-withdrawn) entries matched that query."
          : `Found ${listed.length} entr${listed.length === 1 ? "y" : "ies"}.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function onTransition(target: TransitionTarget) {
    if (!session || !selected) return;
    if (!begin()) return;
    try {
      const next = await session.client.transition(
        selected.reference,
        target,
        selected.generation,
      );
      setSelected(next);
      await refreshList(session.client);
      setNotice(
        target === "deprecated"
          ? `Deprecated ${next.reference.activity_id}@${next.reference.version}. Hidden from default search; pinned refs remain.`
          : `Withdrew ${next.reference.activity_id}@${next.reference.version}. Removed from search; resolve still returns metadata.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  const importLabel = imported ? catalogDisplayLabel(imported) : null;
  const importRef = imported ? activityRefFromImport(imported) : null;
  const selectedLabel = selected ? catalogDisplayLabel(selected) : null;

  return (
    <div className="nf-admin-catalog" aria-label="Admin activity catalog">
      <header className="nf-admin-header">
        <div>
          <span className="nf-admin-eyebrow">NEXUS FLOW DESIGNER</span>
          <h1>Admin catalog</h1>
        </div>
        <div className="nf-admin-header-actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </header>
      <p className="nf-admin-boundary">
        Import a private ZIP, publish immutable versions, then deprecate or
        withdraw. Requires scoped <code>activity_publisher</code>. Validation
        never executes Python; publication needs a configured worker capability
        provider. Local-demo tokens have no publisher grant by default.
      </p>

      {!session && (
        <form className="nf-admin-connect" onSubmit={connect}>
          <h2>Connect</h2>
          <p>
            Use the same org/env bearer session as Projects. Publisher rights
            are separate from ordinary authoring grants.
          </p>
          <fieldset disabled={busy}>
            <label htmlFor="admin-org">Organization ID</label>
            <input
              id="admin-org"
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
              autoComplete="off"
              required
            />
            <label htmlFor="admin-env">Environment ID</label>
            <input
              id="admin-env"
              value={environmentId}
              onChange={(event) => setEnvironmentId(event.target.value)}
              autoComplete="off"
              required
            />
            <label htmlFor="admin-token">Access token</label>
            <input
              id="admin-token"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
              required
            />
            <button type="submit" className="nf-admin-primary">
              Connect
            </button>
          </fieldset>
        </form>
      )}

      {error && (
        <p className="nf-admin-error" role="alert">
          {error}
        </p>
      )}
      {notice && !error && (
        <p className="nf-admin-notice" role="status">
          {notice}
        </p>
      )}

      {session && (
        <div className="nf-admin-layout">
          <section className="nf-admin-panel" aria-label="Import and publish">
            <h2>Import and publish</h2>
            <p>
              Upload raw <code>application/zip</code> bytes. Identical retries
              keep the first validated import. Publish makes the digest visible
              to the palette.
            </p>
            <fieldset disabled={busy}>
              <label htmlFor="admin-zip">Activity bundle ZIP</label>
              <input
                id="admin-zip"
                ref={fileRef}
                type="file"
                accept=".zip,application/zip"
                onChange={onImportFile}
              />
            </fieldset>
            {imported && importLabel && (
              <>
                <h3>Validated candidate</h3>
                <ul className="nf-admin-meta">
                  <li>
                    <strong>{importLabel.displayName}</strong>
                    {importLabel.category ? ` · ${importLabel.category}` : ""}
                  </li>
                  <li>
                    {importLabel.activityId}@{importLabel.version}
                  </li>
                  <li>
                    Digest <code>{imported.digest}</code>
                  </li>
                  <li>
                    Author {imported.author_id} · {imported.created_at}
                  </li>
                  {importRef && (
                    <li>
                      Ref digest <code>{importRef.artifact_digest}</code>
                    </li>
                  )}
                </ul>
                <div className="nf-admin-actions">
                  <button
                    type="button"
                    className="nf-admin-primary"
                    disabled={busy}
                    onClick={() => void onPublish()}
                  >
                    Publish to catalog
                  </button>
                </div>
              </>
            )}
            {!imported && (
              <p className="nf-admin-empty">
                No pending import. Choose a ZIP to validate privately, then
                publish.
              </p>
            )}
          </section>

          <section className="nf-admin-panel" aria-label="Published catalog">
            <h2>Published catalog</h2>
            <form onSubmit={onSearch}>
              <fieldset disabled={busy} className="nf-admin-search-row">
                <div>
                  <label htmlFor="admin-query">Search</label>
                  <input
                    id="admin-query"
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Display name or id"
                  />
                </div>
                <button type="submit">Search</button>
              </fieldset>
            </form>
            {entries.length === 0 ? (
              <p className="nf-admin-empty">No catalog entries in this view.</p>
            ) : (
              <ul className="nf-admin-list">
                {entries.map((entry) => {
                  const label = catalogDisplayLabel(entry);
                  const key = `${entry.reference.activity_id}@${entry.reference.version}:${entry.reference.artifact_digest}`;
                  const current =
                    selected?.reference.artifact_digest ===
                      entry.reference.artifact_digest &&
                    selected.reference.activity_id ===
                      entry.reference.activity_id &&
                    selected.reference.version === entry.reference.version;
                  return (
                    <li key={key}>
                      <button
                        type="button"
                        aria-current={current ? "true" : undefined}
                        disabled={busy}
                        onClick={() => setSelected(entry)}
                      >
                        <span className="nf-admin-list-title">
                          {label.displayName}
                          <span
                            className="nf-admin-state"
                            data-state={entry.state}
                          >
                            {entry.state}
                          </span>
                        </span>
                        <span className="nf-admin-list-meta">
                          {label.activityId}@{label.version}
                          {label.category ? ` · ${label.category}` : ""} · gen{" "}
                          {entry.generation}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}

            {selected && selectedLabel && (
              <>
                <h3>Selected entry</h3>
                <ul className="nf-admin-meta">
                  <li>
                    <strong>{selectedLabel.displayName}</strong>
                    <span
                      className="nf-admin-state"
                      data-state={selected.state}
                    >
                      {selected.state}
                    </span>
                  </li>
                  <li>
                    {selected.reference.activity_id}@
                    {selected.reference.version}
                  </li>
                  <li>
                    Digest <code>{selected.reference.artifact_digest}</code>
                  </li>
                  <li>
                    Generation {selected.generation} · publisher{" "}
                    {selected.publisher_id}
                  </li>
                  <li>
                    Updated {selected.updated_at} by {selected.updated_by}
                  </li>
                </ul>
                <div className="nf-admin-actions">
                  <button
                    type="button"
                    disabled={
                      busy ||
                      selected.state === "deprecated" ||
                      selected.state === "withdrawn"
                    }
                    onClick={() => void onTransition("deprecated")}
                  >
                    Deprecate
                  </button>
                  <button
                    type="button"
                    className="nf-admin-danger"
                    disabled={busy || selected.state === "withdrawn"}
                    onClick={() => void onTransition("withdrawn")}
                  >
                    Withdraw
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
export async function mountAdminCatalog(
  target: string | HTMLElement,
  props: AdminCatalogProps,
): Promise<Root> {
  const element =
    typeof target === "string" ? document.getElementById(target) : target;
  if (!element) throw new Error("Admin catalog mount target was not found");
  const root = createRoot(element);
  root.render(createElement(AdminCatalog, props));
  return root;
}
