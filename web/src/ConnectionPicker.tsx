import type { ConnectionPickOption } from "./connection-api";

export interface ConnectionPickerProps {
  connectionId: string;
  connectionRevision: string;
  options: ConnectionPickOption[];
  onChange: (connectionId: string, connectionRevision: string) => void;
  disabled?: boolean;
  idPrefix?: string;
  label?: string;
}

export default function ConnectionPicker({
  connectionId,
  connectionRevision,
  options,
  onChange,
  disabled = false,
  idPrefix = "connection-picker",
  label = "HTTP connection",
}: ConnectionPickerProps) {
  const selectedKey = `${connectionId}\0${connectionRevision}`;
  const known = options.some(
    (item) =>
      item.connection_id === connectionId &&
      item.connection_revision === connectionRevision,
  );

  return (
    <fieldset className="nf-connection-picker" disabled={disabled}>
      <legend>{label}</legend>
      <label htmlFor={`${idPrefix}-select`}>Known connection revisions</label>
      <select
        id={`${idPrefix}-select`}
        value={known ? selectedKey : ""}
        onChange={(event) => {
          const [id, revision] = event.target.value.split("\0");
          if (id && revision) onChange(id, revision);
        }}
      >
        <option value="">Type or choose a revision…</option>
        {options.map((item) => {
          const key = `${item.connection_id}\0${item.connection_revision}`;
          return (
            <option key={key} value={key}>
              {item.label}
            </option>
          );
        })}
      </select>
      <div className="nf-connection-picker-ids">
        <div>
          <label htmlFor={`${idPrefix}-id`}>Connection ID</label>
          <input
            id={`${idPrefix}-id`}
            value={connectionId}
            onChange={(event) =>
              onChange(event.target.value, connectionRevision)
            }
            autoComplete="off"
            maxLength={128}
            spellCheck={false}
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-revision`}>Revision label</label>
          <input
            id={`${idPrefix}-revision`}
            value={connectionRevision}
            onChange={(event) => onChange(connectionId, event.target.value)}
            autoComplete="off"
            maxLength={128}
            spellCheck={false}
          />
        </div>
      </div>
      <p className="nf-connection-picker-hint">
        Picks registry revision labels only. Secrets and live destinations are
        never shown here. Dev HTTP mocks are separate fixtures pinned by digest.
      </p>
    </fieldset>
  );
}
