import { useEffect, useId, useRef } from "react";
import type {
  JsonObject,
  JsonValue,
  RenderableForm,
  RenderableFormField,
} from "./form-api";
import "./forms.css";

export type FormRenderMode = "task" | "start" | "preview";

export interface FormFieldError {
  field_id: string;
  message: string;
}

export interface FormRendererProps {
  form: RenderableForm;
  values: JsonObject;
  onChange: (fieldId: string, value: JsonValue) => void;
  errors?: FormFieldError[];
  disabled?: boolean;
  mode?: FormRenderMode;
  idPrefix?: string;
  heading?: string;
  submitLabel?: string;
  onSubmit?: () => void;
  showSubmit?: boolean;
}

function fieldSchema(field: RenderableFormField): JsonObject {
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

function fieldType(field: RenderableFormField): string {
  const schema = fieldSchema(field);
  return typeof schema.type === "string" ? schema.type : "string";
}

function enumOptions(field: RenderableFormField): string[] {
  const schema = fieldSchema(field);
  if (!Array.isArray(schema.enum)) return [];
  return schema.enum.filter((item): item is string => typeof item === "string");
}

function displayValue(value: JsonValue | undefined): string {
  if (value === undefined) return "—";
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number" || typeof value === "string")
    return String(value);
  return JSON.stringify(value);
}

/** Labels and descriptions are plain text only — never HTML. */
function SafeText({ text }: { text: string }) {
  return <>{text}</>;
}

export function draftValuesFromForm(form: RenderableForm): JsonObject {
  const draft: JsonObject = {};
  for (const field of form.fields) {
    if (!field.editable) continue;
    if (Object.hasOwn(form.values, field.field_id)) {
      draft[field.field_id] = form.values[field.field_id];
      continue;
    }
    const type = fieldType(field);
    if (type === "boolean") draft[field.field_id] = false;
    else if (type === "number" || type === "integer") draft[field.field_id] = 0;
    else draft[field.field_id] = "";
  }
  return draft;
}

export default function FormRenderer({
  form,
  values,
  onChange,
  errors = [],
  disabled = false,
  mode = "task",
  idPrefix,
  heading,
  submitLabel = "Submit form",
  onSubmit,
  showSubmit = true,
}: FormRendererProps) {
  const reactId = useId();
  const prefix = idPrefix ?? `form-${reactId}`;
  const errorMap = new Map(errors.map((item) => [item.field_id, item.message]));
  const firstErrorId = errors[0]?.field_id ?? null;
  const firstErrorRef = useRef<HTMLElement | null>(null);
  const title =
    heading ??
    (mode === "start"
      ? "Start form"
      : mode === "preview"
        ? "Form preview"
        : "Managed form");

  useEffect(() => {
    if (!firstErrorId) return;
    firstErrorRef.current?.focus();
  }, [firstErrorId, errors]);

  return (
    <form
      className="nf-form-renderer"
      data-mode={mode}
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit?.();
      }}
    >
      <h3 id={`${prefix}-title`}>{title}</h3>
      <fieldset disabled={disabled} aria-labelledby={`${prefix}-title`}>
        {form.fields.map((field, index) => {
          const schema = fieldSchema(field);
          const type = fieldType(field);
          const options = enumOptions(field);
          const value = field.editable
            ? values[field.field_id]
            : form.values[field.field_id];
          const controlId = `${prefix}-field-${field.field_id}`;
          const errorId = `${prefix}-error-${field.field_id}`;
          const helpId = `${prefix}-help-${field.field_id}`;
          const error = errorMap.get(field.field_id);
          const describedBy = [
            typeof schema.description === "string" &&
            schema.description.length > 0
              ? helpId
              : null,
            error ? errorId : null,
          ]
            .filter(Boolean)
            .join(" ");
          const isFirstError = firstErrorId === field.field_id;
          const requiredMark = field.required ? " (required)" : "";
          const attachErrorRef = isFirstError
            ? (node: HTMLElement | null) => {
                firstErrorRef.current = node;
              }
            : undefined;

          if (!field.editable) {
            return (
              <div className="nf-form-field" key={field.field_id}>
                <label htmlFor={controlId}>
                  <SafeText text={field.label} />
                </label>
                <div
                  id={controlId}
                  className="nf-form-readonly"
                  tabIndex={-1}
                  ref={attachErrorRef}
                >
                  {displayValue(value)}
                </div>
                {typeof schema.description === "string" &&
                  schema.description.length > 0 && (
                    <p id={helpId} className="nf-form-help">
                      <SafeText text={schema.description} />
                    </p>
                  )}
                {error && (
                  <p id={errorId} className="nf-form-field-error" role="alert">
                    {error}
                  </p>
                )}
              </div>
            );
          }

          if (type === "boolean") {
            return (
              <div
                className="nf-form-field nf-form-checkbox"
                key={field.field_id}
              >
                <input
                  id={controlId}
                  type="checkbox"
                  checked={value === true}
                  required={field.required || schema.const === true}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={describedBy || undefined}
                  ref={attachErrorRef}
                  onChange={(event) =>
                    onChange(field.field_id, event.target.checked)
                  }
                />
                <label htmlFor={controlId}>
                  <SafeText text={`${field.label}${requiredMark}`} />
                </label>
                {typeof schema.description === "string" &&
                  schema.description.length > 0 && (
                    <p id={helpId} className="nf-form-help">
                      <SafeText text={schema.description} />
                    </p>
                  )}
                {error && (
                  <p id={errorId} className="nf-form-field-error" role="alert">
                    {error}
                  </p>
                )}
              </div>
            );
          }

          if (options.length > 0) {
            return (
              <div className="nf-form-field" key={field.field_id}>
                <label htmlFor={controlId}>
                  <SafeText text={`${field.label}${requiredMark}`} />
                </label>
                <select
                  id={controlId}
                  value={
                    value === undefined || value === null ? "" : String(value)
                  }
                  required={field.required}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={describedBy || undefined}
                  ref={attachErrorRef}
                  onChange={(event) =>
                    onChange(field.field_id, event.target.value)
                  }
                >
                  <option value="">Select…</option>
                  {options.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
                {typeof schema.description === "string" &&
                  schema.description.length > 0 && (
                    <p id={helpId} className="nf-form-help">
                      <SafeText text={schema.description} />
                    </p>
                  )}
                {error && (
                  <p id={errorId} className="nf-form-field-error" role="alert">
                    {error}
                  </p>
                )}
              </div>
            );
          }

          const inputType =
            field.write_only || schema.writeOnly === true
              ? "password"
              : type === "number" || type === "integer"
                ? "number"
                : "text";

          return (
            <div className="nf-form-field" key={field.field_id}>
              <label htmlFor={controlId}>
                <SafeText text={`${field.label}${requiredMark}`} />
              </label>
              <input
                id={controlId}
                type={inputType}
                inputMode={
                  type === "number" || type === "integer"
                    ? "decimal"
                    : undefined
                }
                step={type === "integer" ? 1 : undefined}
                value={
                  value === undefined || value === null ? "" : String(value)
                }
                required={field.required}
                aria-invalid={error ? true : undefined}
                aria-describedby={describedBy || undefined}
                autoComplete={field.write_only ? "new-password" : "off"}
                ref={attachErrorRef}
                onChange={(event) => {
                  const raw = event.target.value;
                  if (type === "number" || type === "integer") {
                    onChange(
                      field.field_id,
                      raw === "" ? "" : Number(raw),
                    );
                    return;
                  }
                  onChange(field.field_id, raw);
                }}
              />
              {typeof schema.description === "string" &&
                schema.description.length > 0 && (
                  <p id={helpId} className="nf-form-help">
                    <SafeText text={schema.description} />
                  </p>
                )}
              {error && (
                <p id={errorId} className="nf-form-field-error" role="alert">
                  {error}
                </p>
              )}
              {index === 0 && mode === "start" && (
                <span className="nf-form-mode-hint">Start path fields</span>
              )}
            </div>
          );
        })}
      </fieldset>
      {showSubmit && onSubmit && (
        <div className="nf-form-actions">
          <button className="nf-form-primary" disabled={disabled} type="submit">
            {submitLabel}
          </button>
        </div>
      )}
    </form>
  );
}
