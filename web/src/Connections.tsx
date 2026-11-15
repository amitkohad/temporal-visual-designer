import { useEffect, useRef, useState } from "react";
import ConnectionPicker from "./ConnectionPicker";
import {
  createConnectionClient,
  createConnectionWrite,
  createEmailConnectionWrite,
  mergePickOptions,
  pickOptionsFromFixtures,
  pickOptionsFromRevisions,
  validateEmailConnectionWrite,
  validateHttpsOrigin,
  validateMockFixtureWrite,
  validateRecipientDomain,
  validateSenderEmail,
  validateSmtpHost,
  type ConnectionClient,
  type ConnectionMetadata,
  type ConnectionPickOption,
  type EmailConnectionMetadata,
  type HTTPMockFixtureMetadata,
  type HttpMethod,
} from "./connection-api";
import {
  ProjectApiError,
  isIdentifier,
  type ProjectConnection,
} from "./project-api";
import "./connections.css";
import "./tokens.css";

export interface ConnectionsProps {
  onClose: () => void;
  connection?: ProjectConnection;
}

type Session = {
  connection: ProjectConnection;
  client: ConnectionClient;
};

const failureMessages: Record<string, string> = {
  invalid_request:
    "Check the organization, environment, token, and field values.",
  unauthorized: "The token was rejected. Mint a fresh local-demo token.",
  forbidden:
    "This identity cannot manage connections. On local-demo run enable-mock-fixtures for the administrator grant.",
  not_found: "That connection revision or mock fixture was not found.",
  conflict:
    "That revision already exists with different content. Use a new revision label.",
  unavailable:
    "The host is unavailable, or connection/email/mock providers are not enabled. Confirm serve --ui, enable-mock-fixtures, and email registry wiring.",
  invalid_response: "The host returned an unexpected response.",
};

function messageFor(error: unknown): string {
  if (error instanceof ProjectApiError)
    return failureMessages[error.code] ?? failureMessages.unavailable;
  if (error instanceof Error && error.message) return error.message;
  return failureMessages.unavailable;
}

function parseJsonObject(
  text: string,
  label: string,
): import("./project-api").JsonObject | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    const value = JSON.parse(trimmed) as unknown;
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new Error(`${label} must be a JSON object`);
    }
    return value as import("./project-api").JsonObject;
  } catch (error) {
    if (error instanceof Error && error.message.includes("JSON object"))
      throw error;
    throw new Error(`${label} must be valid JSON`);
  }
}

export default function Connections({ onClose, connection }: ConnectionsProps) {
  const [organizationId, setOrganizationId] = useState(
    connection?.organizationId ?? "local-demo",
  );
  const [environmentId, setEnvironmentId] = useState(
    connection?.environmentId ?? "dev",
  );
  const [token, setToken] = useState(connection?.token ?? "");
  const [session, setSession] = useState<Session | null>(null);
  const [connectionId, setConnectionId] = useState("hr-api");
  const [connectionRevision, setConnectionRevision] = useState("v1");
  const [baseOrigin, setBaseOrigin] = useState("https://api.example.com");
  const [allowedOrigins, setAllowedOrigins] = useState(
    "https://api.example.com",
  );
  const [secretReference, setSecretReference] = useState("");
  const [revisions, setRevisions] = useState<ConnectionMetadata[]>([]);
  const [fixtures, setFixtures] = useState<HTTPMockFixtureMetadata[]>([]);
  const [options, setOptions] = useState<ConnectionPickOption[]>([]);
  const [method, setMethod] = useState<HttpMethod>("GET");
  const [path, setPath] = useState("/health");
  const [statusCode, setStatusCode] = useState("200");
  const [requestJson, setRequestJson] = useState("");
  const [responseJson, setResponseJson] = useState('{"ok":true}');
  const [selectedFixture, setSelectedFixture] =
    useState<HTTPMockFixtureMetadata | null>(null);
  const [selectedConnection, setSelectedConnection] =
    useState<ConnectionMetadata | null>(null);
  const [emailConnectionId, setEmailConnectionId] = useState("hr-mail");
  const [emailConnectionRevision, setEmailConnectionRevision] = useState("v1");
  const [smtpHost, setSmtpHost] = useState("smtp.example.test");
  const [smtpPort, setSmtpPort] = useState("465");
  const [emailSender, setEmailSender] = useState("noreply@example.test");
  const [allowedRecipientDomains, setAllowedRecipientDomains] =
    useState("example.test");
  const [emailSecretReference, setEmailSecretReference] = useState("");
  const [emailRevisions, setEmailRevisions] = useState<
    EmailConnectionMetadata[]
  >([]);
  const [selectedEmailConnection, setSelectedEmailConnection] =
    useState<EmailConnectionMetadata | null>(null);
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

  function rememberOptions(
    nextRevisions: ConnectionMetadata[],
    nextFixtures: HTTPMockFixtureMetadata[],
  ) {
    setOptions(
      mergePickOptions(
        pickOptionsFromRevisions(nextRevisions),
        pickOptionsFromFixtures(nextFixtures),
      ),
    );
  }

  async function refreshEmailCatalog(
    client: ConnectionClient,
    id: string,
    revision: string,
  ) {
    let nextEmailRevisions: EmailConnectionMetadata[] = [];
    if (isIdentifier(id)) {
      try {
        const page = await client.listEmailConnectionRevisions(id, 25);
        nextEmailRevisions = page.items;
      } catch (err) {
        if (
          !(
            err instanceof ProjectApiError &&
            (err.code === "not_found" || err.code === "unavailable")
          )
        ) {
          throw err;
        }
      }
    }
    setEmailRevisions(nextEmailRevisions);
    const match = nextEmailRevisions.find(
      (item) =>
        item.connection_id === id && item.connection_revision === revision,
    );
    setSelectedEmailConnection(match ?? null);
  }

  async function refreshCatalog(
    client: ConnectionClient,
    id: string,
    revision: string,
    emailId = emailConnectionId,
    emailRevision = emailConnectionRevision,
  ) {
    let nextRevisions: ConnectionMetadata[] = [];
    let nextFixtures: HTTPMockFixtureMetadata[] = [];
    if (isIdentifier(id)) {
      try {
        const page = await client.listConnectionRevisions(id, 25);
        nextRevisions = page.items;
      } catch (err) {
        if (!(err instanceof ProjectApiError && err.code === "not_found")) {
          throw err;
        }
      }
    }
    try {
      const page = await client.listMockFixtures(50);
      nextFixtures = page.items;
    } catch (err) {
      if (!(err instanceof ProjectApiError && err.code === "forbidden")) {
        throw err;
      }
    }
    setRevisions(nextRevisions);
    setFixtures(nextFixtures);
    rememberOptions(nextRevisions, nextFixtures);
    const match = nextRevisions.find(
      (item) =>
        item.connection_id === id && item.connection_revision === revision,
    );
    setSelectedConnection(match ?? null);
    const fixture = nextFixtures.find(
      (item) =>
        item.connection_id === id && item.connection_revision === revision,
    );
    setSelectedFixture(fixture ?? null);
    await refreshEmailCatalog(client, emailId, emailRevision);
  }

  async function connectWith(next: ProjectConnection) {
    if (!begin()) return;
    try {
      if (next.environmentId !== "dev") {
        throw new Error("Connection and mock administration is Dev-only.");
      }
      const client = createConnectionClient(next);
      setSession({ connection: next, client });
      setOrganizationId(next.organizationId);
      setEnvironmentId(next.environmentId);
      await refreshCatalog(
        client,
        connectionId,
        connectionRevision,
        emailConnectionId,
        emailConnectionRevision,
      );
      setNotice(
        "Connected. Register HTTP or email SMTP metadata, then author a Dev HTTP mock fixture. Metadata registration is not live send.",
      );
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

  function onPick(id: string, revision: string) {
    setConnectionId(id);
    setConnectionRevision(revision);
    const match = revisions.find(
      (item) =>
        item.connection_id === id && item.connection_revision === revision,
    );
    setSelectedConnection(match ?? null);
    const fixture = fixtures.find(
      (item) =>
        item.connection_id === id && item.connection_revision === revision,
    );
    setSelectedFixture(fixture ?? null);
  }

  async function saveConnection(event: React.FormEvent) {
    event.preventDefault();
    if (!session) return;
    if (!begin()) return;
    try {
      if (!isIdentifier(connectionId) || !isIdentifier(connectionRevision)) {
        throw new Error(
          "Connection ID and revision must be stable identifiers.",
        );
      }
      const originError = validateHttpsOrigin(baseOrigin.trim());
      if (originError) throw new Error(originError);
      const origins = allowedOrigins
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean);
      for (const origin of origins) {
        const issue = validateHttpsOrigin(origin);
        if (issue) throw new Error(issue);
      }
      if (secretReference.trim() && !isIdentifier(secretReference.trim())) {
        throw new Error(
          "Secret reference must be an opaque identifier label, not a credential.",
        );
      }
      const body = createConnectionWrite(
        baseOrigin,
        origins,
        secretReference.trim() || undefined,
      );
      const metadata = await session.client.putConnection(
        connectionId,
        connectionRevision,
        body,
      );
      setSelectedConnection(metadata);
      await refreshCatalog(session.client, connectionId, connectionRevision);
      setNotice(
        `Saved HTTP connection ${metadata.connection_id}/${metadata.connection_revision}. Origins stay private; digest ${metadata.definition_digest.slice(0, 18)}…`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function saveEmailConnection(event: React.FormEvent) {
    event.preventDefault();
    if (!session) return;
    if (!begin()) return;
    try {
      if (
        !isIdentifier(emailConnectionId) ||
        !isIdentifier(emailConnectionRevision)
      ) {
        throw new Error(
          "Email connection ID and revision must be stable identifiers.",
        );
      }
      const hostIssue = validateSmtpHost(smtpHost.trim());
      if (hostIssue) throw new Error(hostIssue);
      const senderIssue = validateSenderEmail(emailSender.trim());
      if (senderIssue) throw new Error(senderIssue);
      const domains = allowedRecipientDomains
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean);
      for (const domain of domains) {
        const issue = validateRecipientDomain(domain);
        if (issue) throw new Error(issue);
      }
      if (
        emailSecretReference.trim() &&
        !isIdentifier(emailSecretReference.trim())
      ) {
        throw new Error(
          "Secret reference must be an opaque identifier label, not a credential.",
        );
      }
      const port = Number(smtpPort);
      const body = createEmailConnectionWrite(smtpHost, emailSender, domains, {
        port: Number.isFinite(port) ? port : 465,
        secretReference: emailSecretReference.trim() || undefined,
      });
      const issue = validateEmailConnectionWrite(body);
      if (issue) throw new Error(issue);
      const metadata = await session.client.putEmailConnection(
        emailConnectionId,
        emailConnectionRevision,
        body,
      );
      setSelectedEmailConnection(metadata);
      await refreshEmailCatalog(
        session.client,
        emailConnectionId,
        emailConnectionRevision,
      );
      setNotice(
        `Saved email SMTP connection ${metadata.connection_id}/${metadata.connection_revision}. Host/sender stay private; digest ${metadata.definition_digest.slice(0, 18)}… Not a live send.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function saveMock(event: React.FormEvent) {
    event.preventDefault();
    if (!session) return;
    if (!begin()) return;
    try {
      if (!isIdentifier(connectionId) || !isIdentifier(connectionRevision)) {
        throw new Error("Pick a connection ID and revision first.");
      }
      const code = Number(statusCode);
      const json_body = parseJsonObject(requestJson, "Request JSON");
      const response_json_body = parseJsonObject(responseJson, "Response JSON");
      const body = {
        method,
        path: path.trim(),
        status_code: code,
        json_body,
        response_json_body,
      };
      const issue = validateMockFixtureWrite(body);
      if (issue) throw new Error(issue);
      const metadata = await session.client.putMockFixture(
        connectionId,
        connectionRevision,
        body,
      );
      setSelectedFixture(metadata);
      await refreshCatalog(session.client, connectionId, connectionRevision);
      setNotice(
        `Saved Dev HTTP mock for ${metadata.connection_id}/${metadata.connection_revision}. Fixture digest ${metadata.fixture_digest}. Not a live provider call.`,
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  async function loadSelected() {
    if (!session) return;
    if (!begin()) return;
    try {
      await refreshCatalog(
        session.client,
        connectionId,
        connectionRevision,
        emailConnectionId,
        emailConnectionRevision,
      );
      if (isIdentifier(connectionId) && isIdentifier(connectionRevision)) {
        try {
          const metadata = await session.client.getConnection(
            connectionId,
            connectionRevision,
          );
          setSelectedConnection(metadata);
        } catch (err) {
          if (!(err instanceof ProjectApiError && err.code === "not_found")) {
            throw err;
          }
          setSelectedConnection(null);
        }
        try {
          const fixture = await session.client.getMockFixture(
            connectionId,
            connectionRevision,
          );
          setSelectedFixture(fixture);
        } catch (err) {
          if (!(err instanceof ProjectApiError && err.code === "not_found")) {
            throw err;
          }
          setSelectedFixture(null);
        }
      }
      if (
        isIdentifier(emailConnectionId) &&
        isIdentifier(emailConnectionRevision)
      ) {
        try {
          const metadata = await session.client.getEmailConnection(
            emailConnectionId,
            emailConnectionRevision,
          );
          setSelectedEmailConnection(metadata);
        } catch (err) {
          if (!(err instanceof ProjectApiError && err.code === "not_found")) {
            throw err;
          }
          setSelectedEmailConnection(null);
        }
      }
      setNotice(
        "Refreshed HTTP connection revisions, email SMTP revisions, and Dev mock fixtures.",
      );
    } catch (err) {
      setError(messageFor(err));
    } finally {
      end();
    }
  }

  function onPickEmail(id: string, revision: string) {
    setEmailConnectionId(id);
    setEmailConnectionRevision(revision);
    const match = emailRevisions.find(
      (item) =>
        item.connection_id === id && item.connection_revision === revision,
    );
    setSelectedEmailConnection(match ?? null);
  }

  return (
    <div className="nf-connections-surface" aria-label="Connections">
      <header className="nf-connections-header">
        <div>
          <span className="nf-connections-eyebrow">NEXUS FLOW DESIGNER</span>
          <h1>Connections</h1>
        </div>
        <div className="nf-connections-header-actions">
          {session && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setSession(null);
                setRevisions([]);
                setFixtures([]);
                setOptions([]);
                setEmailRevisions([]);
                setSelectedEmailConnection(null);
                setNotice("Disconnected.");
              }}
            >
              Disconnect
            </button>
          )}
          <button type="button" disabled={busy} onClick={onClose}>
            Close connections
          </button>
        </div>
      </header>
      <p className="nf-connections-boundary">
        Register Dev HTTP connection revisions, email SMTP connection metadata,
        and author JSON mock fixtures through existing admin APIs. Email
        metadata registration is available here; live send needs{" "}
        <code>enable-live-email</code> + <code>put-smtp-secret</code> and is not
        production SMTP. Mocks are digest-pinned fixtures for local testing —
        they are not live SMTP, public egress, or production providers. This UI
        does not send mail.
      </p>
      {error && (
        <p className="nf-connections-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="nf-connections-notice" role="status">
          {notice}
        </p>
      )}
      {!session && (
        <form className="nf-connections-connect" onSubmit={connect}>
          <h2>Connect to manage Dev connectors</h2>
          <p>
            Use org <code>local-demo</code>, environment <code>dev</code>, and a
            token after <code>enable-mock-fixtures</code>. Tokens stay in memory
            for this tab.
          </p>
          <fieldset disabled={busy}>
            <label htmlFor="connections-org">Organization ID</label>
            <input
              id="connections-org"
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
              autoComplete="off"
              maxLength={128}
            />
            <label htmlFor="connections-environment">Environment ID</label>
            <input
              id="connections-environment"
              value={environmentId}
              onChange={(event) => setEnvironmentId(event.target.value)}
              autoComplete="off"
              maxLength={128}
            />
            <label htmlFor="connections-token">Access token</label>
            <input
              id="connections-token"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
            />
            <button type="submit" className="nf-connections-primary">
              Connect
            </button>
          </fieldset>
        </form>
      )}
      {session && (
        <div className="nf-connections-layout">
          <aside
            className="nf-connections-stack"
            aria-label="Known connections"
          >
            <section className="nf-connections-panel">
              <h2>Connection picker</h2>
              <ConnectionPicker
                connectionId={connectionId}
                connectionRevision={connectionRevision}
                options={options}
                onChange={onPick}
                disabled={busy}
              />
              <div className="nf-connections-actions">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void loadSelected()}
                >
                  Refresh
                </button>
              </div>
            </section>
            <section className="nf-connections-panel">
              <h2>Dev mock fixtures</h2>
              {fixtures.length === 0 ? (
                <p>
                  No fixtures listed yet. Save a mock after registering a
                  connection.
                </p>
              ) : (
                <ul className="nf-connections-list">
                  {fixtures.map((item) => {
                    const current =
                      item.connection_id === connectionId &&
                      item.connection_revision === connectionRevision;
                    return (
                      <li
                        key={`${item.connection_id}/${item.connection_revision}`}
                      >
                        <button
                          type="button"
                          aria-current={current ? "true" : undefined}
                          disabled={busy}
                          onClick={() =>
                            onPick(item.connection_id, item.connection_revision)
                          }
                        >
                          <strong>
                            {item.connection_id} / {item.connection_revision}
                          </strong>
                          <small>{item.fixture_digest}</small>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
            <section className="nf-connections-panel">
              <h2>Email SMTP revisions</h2>
              {emailRevisions.length === 0 ? (
                <p>
                  No email revisions listed yet. Save an email SMTP connection
                  below.
                </p>
              ) : (
                <ul className="nf-connections-list">
                  {emailRevisions.map((item) => {
                    const current =
                      item.connection_id === emailConnectionId &&
                      item.connection_revision === emailConnectionRevision;
                    return (
                      <li
                        key={`email-${item.connection_id}/${item.connection_revision}`}
                      >
                        <button
                          type="button"
                          aria-current={current ? "true" : undefined}
                          disabled={busy}
                          onClick={() =>
                            onPickEmail(
                              item.connection_id,
                              item.connection_revision,
                            )
                          }
                        >
                          <strong>
                            {item.connection_id} / {item.connection_revision}
                          </strong>
                          <small>
                            rev {item.revision} · {item.definition_digest}
                          </small>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </aside>
          <div className="nf-connections-stack">
            <form className="nf-connections-panel" onSubmit={saveConnection}>
              <h2>Register HTTP connection</h2>
              <p>
                Metadata-only registry. Origins and secret reference labels are
                never returned by GET; only digests and flags are public.
              </p>
              <fieldset disabled={busy}>
                <label htmlFor="conn-base-origin">Base origin</label>
                <input
                  id="conn-base-origin"
                  value={baseOrigin}
                  onChange={(event) => setBaseOrigin(event.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                />
                <label htmlFor="conn-allowed-origins">Allowed origins</label>
                <textarea
                  id="conn-allowed-origins"
                  className="nf-connections-json"
                  value={allowedOrigins}
                  onChange={(event) => setAllowedOrigins(event.target.value)}
                  spellCheck={false}
                />
                <label htmlFor="conn-secret-ref">
                  Secret reference (optional label)
                </label>
                <input
                  id="conn-secret-ref"
                  value={secretReference}
                  onChange={(event) => setSecretReference(event.target.value)}
                  autoComplete="off"
                  maxLength={128}
                  spellCheck={false}
                />
                <div className="nf-connections-actions">
                  <button type="submit" className="nf-connections-primary">
                    Save connection revision
                  </button>
                </div>
              </fieldset>
              {selectedConnection && (
                <div className="nf-connections-meta" aria-live="polite">
                  Saved metadata:{" "}
                  <code>
                    {selectedConnection.connection_id}/
                    {selectedConnection.connection_revision}
                  </code>
                  <br />
                  Digest: <code>{selectedConnection.definition_digest}</code>
                  <br />
                  Secret reference present:{" "}
                  {selectedConnection.has_secret_reference ? "yes" : "no"}
                </div>
              )}
            </form>
            <form
              className="nf-connections-panel"
              onSubmit={saveEmailConnection}
            >
              <h2>Register email SMTP connection</h2>
              <p>
                Metadata-only SMTP TLS registry. Host, sender, recipient
                domains, and secret reference labels are never returned by GET;
                only digests and flags are public. This form does not dial SMTP
                or send mail.
              </p>
              <fieldset disabled={busy}>
                <label htmlFor="email-conn-id">Email connection ID</label>
                <input
                  id="email-conn-id"
                  value={emailConnectionId}
                  onChange={(event) => setEmailConnectionId(event.target.value)}
                  autoComplete="off"
                  maxLength={128}
                  spellCheck={false}
                />
                <label htmlFor="email-conn-revision">Email revision</label>
                <input
                  id="email-conn-revision"
                  value={emailConnectionRevision}
                  onChange={(event) =>
                    setEmailConnectionRevision(event.target.value)
                  }
                  autoComplete="off"
                  maxLength={128}
                  spellCheck={false}
                />
                <label htmlFor="email-smtp-host">SMTP host</label>
                <input
                  id="email-smtp-host"
                  value={smtpHost}
                  onChange={(event) => setSmtpHost(event.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                />
                <label htmlFor="email-smtp-port">Port</label>
                <input
                  id="email-smtp-port"
                  value={smtpPort}
                  onChange={(event) => setSmtpPort(event.target.value)}
                  inputMode="numeric"
                  autoComplete="off"
                />
                <label htmlFor="email-sender">Sender</label>
                <input
                  id="email-sender"
                  value={emailSender}
                  onChange={(event) => setEmailSender(event.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                />
                <label htmlFor="email-allowed-domains">
                  Allowed recipient domains
                </label>
                <textarea
                  id="email-allowed-domains"
                  className="nf-connections-json"
                  value={allowedRecipientDomains}
                  onChange={(event) =>
                    setAllowedRecipientDomains(event.target.value)
                  }
                  spellCheck={false}
                />
                <label htmlFor="email-secret-ref">
                  Secret reference (optional label)
                </label>
                <input
                  id="email-secret-ref"
                  value={emailSecretReference}
                  onChange={(event) =>
                    setEmailSecretReference(event.target.value)
                  }
                  autoComplete="off"
                  maxLength={128}
                  spellCheck={false}
                />
                <div className="nf-connections-actions">
                  <button type="submit" className="nf-connections-primary">
                    Save email connection revision
                  </button>
                </div>
              </fieldset>
              {selectedEmailConnection && (
                <div className="nf-connections-meta" aria-live="polite">
                  Saved email metadata:{" "}
                  <code>
                    {selectedEmailConnection.connection_id}/
                    {selectedEmailConnection.connection_revision}
                  </code>
                  <br />
                  Transport: <code>{selectedEmailConnection.transport}</code>
                  <br />
                  Digest:{" "}
                  <code>{selectedEmailConnection.definition_digest}</code>
                  <br />
                  Secret reference present:{" "}
                  {selectedEmailConnection.has_secret_reference ? "yes" : "no"}
                </div>
              )}
            </form>
            <form className="nf-connections-panel" onSubmit={saveMock}>
              <h2>Author Dev HTTP mock</h2>
              <p>
                Writes a digest-pinned JSON fixture for the selected connection
                revision. Requires explicit Dev mock enablement. This does not
                dial the origin or send email.
              </p>
              <fieldset disabled={busy}>
                <label htmlFor="mock-method">Method</label>
                <select
                  id="mock-method"
                  value={method}
                  onChange={(event) =>
                    setMethod(event.target.value as HttpMethod)
                  }
                >
                  {(
                    [
                      "GET",
                      "HEAD",
                      "POST",
                      "PUT",
                      "PATCH",
                      "DELETE",
                      "OPTIONS",
                    ] as HttpMethod[]
                  ).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
                <label htmlFor="mock-path">Path</label>
                <input
                  id="mock-path"
                  value={path}
                  onChange={(event) => setPath(event.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                />
                <label htmlFor="mock-status">Status code</label>
                <input
                  id="mock-status"
                  value={statusCode}
                  onChange={(event) => setStatusCode(event.target.value)}
                  inputMode="numeric"
                  autoComplete="off"
                />
                <label htmlFor="mock-request-json">
                  Request JSON (optional)
                </label>
                <textarea
                  id="mock-request-json"
                  className="nf-connections-json"
                  value={requestJson}
                  onChange={(event) => setRequestJson(event.target.value)}
                  spellCheck={false}
                />
                <label htmlFor="mock-response-json">
                  Response JSON (optional)
                </label>
                <textarea
                  id="mock-response-json"
                  className="nf-connections-json"
                  value={responseJson}
                  onChange={(event) => setResponseJson(event.target.value)}
                  spellCheck={false}
                />
                <div className="nf-connections-actions">
                  <button type="submit" className="nf-connections-primary">
                    Save Dev mock fixture
                  </button>
                </div>
              </fieldset>
              {selectedFixture && (
                <div className="nf-connections-meta" aria-live="polite">
                  Fixture metadata for{" "}
                  <code>
                    {selectedFixture.connection_id}/
                    {selectedFixture.connection_revision}
                  </code>
                  <br />
                  Digest: <code>{selectedFixture.fixture_digest}</code>
                  <br />
                  Stored bytes: {selectedFixture.stored_bytes}
                  <br />
                  Bodies are never downloaded through this API.
                </div>
              )}
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
