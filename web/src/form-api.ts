/** Form definition authoring helpers and renderable preview wire (schema 0.1). */
import {
  createProjectClient,
  isIdentifier,
  isObject,
  type JsonObject,
  type JsonValue,
  type ProjectConnection,
  type ProjectClient,
  type ProjectRevision,
  type ProjectSummary,
} from "./project-api";

export type FormFieldType =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "object"
  | "array"
  | "null";

export interface FormFieldSchema {
  type: FormFieldType;
  title?: string;
  description?: string;
  enum?: JsonValue[];
  const?: JsonValue;
  readOnly?: boolean;
  writeOnly?: boolean;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
}

export interface FormDefinition {
  form_id: string;
  schema_version: "0.1";
  data_schema: {
    type: "object";
    properties: Record<string, FormFieldSchema>;
    required?: string[];
    additionalProperties: false;
  };
  layout: { field_order?: string[] };
  visibility_rules: JsonObject[];
  validation_rules: JsonObject[];
  field_permissions: Record<string, { read_only?: boolean }>;
  attachment_policy: JsonObject;
}

export interface FormFieldDraft {
  field_id: string;
  title: string;
  type: FormFieldType;
  required: boolean;
  readOnly: boolean;
  writeOnly: boolean;
  enumOptions: string;
  description: string;
}

export interface RenderableFormField {
  field_id: string;
  label: string;
  schema_json: string;
  editable: boolean;
  required: boolean;
  write_only: boolean;
}

export interface RenderableForm {
  revision: number;
  fields: RenderableFormField[];
  values: JsonObject;
}

export interface FormIssue {
  path: string;
  code: string;
  message: string;
}

export interface ApprovalFormBinding {
  workflowId: string;
  nodeId: string;
  formId: string | null;
  hasInlineForm: boolean;
}

export interface ProjectFormCatalog {
  projectForms: FormDefinition[];
  bindings: ApprovalFormBinding[];
}

const FIELD_TYPES = new Set<FormFieldType>([
  "string",
  "number",
  "integer",
  "boolean",
  "object",
  "array",
  "null",
]);

function canonical(value: unknown, depth = 0): string {
  if (depth > 100) throw new Error("canonical depth");
  if (value === null || typeof value === "boolean" || typeof value === "string")
    return JSON.stringify(value);
  if (
    typeof value === "number" &&
    Number.isFinite(value) &&
    (!Number.isInteger(value) || Number.isSafeInteger(value))
  )
    return JSON.stringify(value);
  if (Array.isArray(value))
    return (
      "[" + value.map((item) => canonical(item, depth + 1)).join(",") + "]"
    );
  if (isObject(value) && Object.getPrototypeOf(value) === Object.prototype) {
    return (
      "{" +
      Object.keys(value)
        .sort()
        .map(
          (key) => JSON.stringify(key) + ":" + canonical(value[key], depth + 1),
        )
        .join(",") +
      "}"
    );
  }
  throw new Error("canonical value");
}

function asFieldSchema(value: unknown): FormFieldSchema | null {
  if (!isObject(value) || typeof value.type !== "string") return null;
  if (!FIELD_TYPES.has(value.type as FormFieldType)) return null;
  return value as unknown as FormFieldSchema;
}

export function emptyFormDefinition(formId = "new-form"): FormDefinition {
  if (!isIdentifier(formId)) throw new Error("invalid form id");
  return {
    form_id: formId,
    schema_version: "0.1",
    data_schema: {
      type: "object",
      properties: {},
      required: [],
      additionalProperties: false,
    },
    layout: { field_order: [] },
    visibility_rules: [],
    validation_rules: [],
    field_permissions: {},
    attachment_policy: {},
  };
}

export function parseFormDefinition(value: unknown): FormDefinition {
  if (
    !isObject(value) ||
    !isIdentifier(value.form_id) ||
    value.schema_version !== "0.1" ||
    !isObject(value.data_schema) ||
    value.data_schema.type !== "object" ||
    !isObject(value.data_schema.properties) ||
    value.data_schema.additionalProperties !== false
  )
    throw new Error("invalid form definition");
  const properties: Record<string, FormFieldSchema> = {};
  for (const [key, schema] of Object.entries(value.data_schema.properties)) {
    if (!isIdentifier(key)) throw new Error("invalid field id");
    const field = asFieldSchema(schema);
    if (!field) throw new Error("invalid field schema");
    properties[key] = field;
  }
  const required = Array.isArray(value.data_schema.required)
    ? value.data_schema.required.filter(
        (item): item is string => typeof item === "string",
      )
    : [];
  const layout = isObject(value.layout) ? value.layout : {};
  const fieldOrder = Array.isArray(layout.field_order)
    ? layout.field_order.filter(
        (item): item is string => typeof item === "string",
      )
    : Object.keys(properties);
  const field_permissions: Record<string, { read_only?: boolean }> = {};
  if (isObject(value.field_permissions)) {
    for (const [key, permission] of Object.entries(value.field_permissions)) {
      if (
        isObject(permission) &&
        (permission.read_only === undefined ||
          typeof permission.read_only === "boolean")
      ) {
        field_permissions[key] = { read_only: permission.read_only === true };
      }
    }
  }
  return {
    form_id: value.form_id,
    schema_version: "0.1",
    data_schema: {
      type: "object",
      properties,
      required,
      additionalProperties: false,
    },
    layout: { field_order: fieldOrder },
    visibility_rules: Array.isArray(value.visibility_rules)
      ? (value.visibility_rules.filter(isObject) as JsonObject[])
      : [],
    validation_rules: Array.isArray(value.validation_rules)
      ? (value.validation_rules.filter(isObject) as JsonObject[])
      : [],
    field_permissions,
    attachment_policy: isObject(value.attachment_policy)
      ? value.attachment_policy
      : {},
  };
}

export function validateFormDefinition(form: FormDefinition): FormIssue[] {
  const issues: FormIssue[] = [];
  if (!isIdentifier(form.form_id)) {
    issues.push({
      path: "/form_id",
      code: "field_id",
      message: "Form ID must be a stable identifier",
    });
  }
  const properties = form.data_schema.properties;
  if (Object.keys(properties).length > 100) {
    issues.push({
      path: "/data_schema/properties",
      code: "field_limit",
      message: "Forms support at most 100 fields",
    });
  }
  if (form.data_schema.additionalProperties !== false) {
    issues.push({
      path: "/data_schema/additionalProperties",
      code: "form_schema",
      message: "Form schema must forbid undeclared fields",
    });
  }
  if (Object.keys(form.attachment_policy).length > 0) {
    issues.push({
      path: "/attachment_policy",
      code: "unsupported_attachments",
      message: "Attachment policies are not supported in this subset",
    });
  }
  for (const [fieldId, schema] of Object.entries(properties)) {
    const path = `/data_schema/properties/${fieldId}`;
    if (!isIdentifier(fieldId)) {
      issues.push({
        path,
        code: "field_id",
        message: "Field ID must be a stable identifier",
      });
    }
    if (!FIELD_TYPES.has(schema.type)) {
      issues.push({
        path,
        code: "field_schema",
        message: "Field schema must declare one supported type",
      });
    }
    if (schema.readOnly !== undefined && typeof schema.readOnly !== "boolean") {
      issues.push({
        path: `${path}/readOnly`,
        code: "unsupported_annotation",
        message: "Only direct field boolean access annotations are supported",
      });
    }
    if (
      schema.writeOnly !== undefined &&
      typeof schema.writeOnly !== "boolean"
    ) {
      issues.push({
        path: `${path}/writeOnly`,
        code: "unsupported_annotation",
        message: "Only direct field boolean access annotations are supported",
      });
    }
  }
  for (const fieldId of form.data_schema.required ?? []) {
    if (!(fieldId in properties)) {
      issues.push({
        path: `/data_schema/required/${fieldId}`,
        code: "unknown_field",
        message: "Required list references an undeclared field",
      });
    }
  }
  for (const fieldId of form.layout.field_order ?? []) {
    if (!(fieldId in properties)) {
      issues.push({
        path: `/layout/field_order/${fieldId}`,
        code: "unknown_field",
        message: "Layout references an undeclared field",
      });
    }
  }
  return issues;
}

export function draftsFromDefinition(form: FormDefinition): FormFieldDraft[] {
  const order = [
    ...(form.layout.field_order ?? []),
    ...Object.keys(form.data_schema.properties).filter(
      (key) => !(form.layout.field_order ?? []).includes(key),
    ),
  ];
  const required = new Set(form.data_schema.required ?? []);
  return order.map((fieldId) => {
    const schema = form.data_schema.properties[fieldId];
    const enums = Array.isArray(schema.enum)
      ? schema.enum
          .filter((item): item is string => typeof item === "string")
          .join(", ")
      : "";
    return {
      field_id: fieldId,
      title:
        typeof schema.title === "string" && schema.title.length > 0
          ? schema.title
          : fieldId,
      type: schema.type,
      required: required.has(fieldId),
      readOnly: schema.readOnly === true,
      writeOnly: schema.writeOnly === true,
      enumOptions: enums,
      description:
        typeof schema.description === "string" ? schema.description : "",
    };
  });
}

export function definitionFromDrafts(
  formId: string,
  drafts: FormFieldDraft[],
  previous?: FormDefinition,
): FormDefinition {
  if (!isIdentifier(formId)) throw new Error("invalid form id");
  const properties: Record<string, FormFieldSchema> = {};
  const required: string[] = [];
  const field_order: string[] = [];
  for (const draft of drafts) {
    if (!isIdentifier(draft.field_id)) throw new Error("invalid field id");
    if (field_order.includes(draft.field_id)) throw new Error("duplicate field");
    field_order.push(draft.field_id);
    const schema: FormFieldSchema = { type: draft.type };
    if (draft.title.trim()) schema.title = draft.title.trim();
    if (draft.description.trim()) schema.description = draft.description.trim();
    if (draft.readOnly) schema.readOnly = true;
    if (draft.writeOnly) schema.writeOnly = true;
    const options = draft.enumOptions
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    if (options.length > 0 && draft.type === "string") schema.enum = options;
    properties[draft.field_id] = schema;
    if (draft.required) required.push(draft.field_id);
  }
  return {
    form_id: formId,
    schema_version: "0.1",
    data_schema: {
      type: "object",
      properties,
      required,
      additionalProperties: false,
    },
    layout: { field_order },
    visibility_rules: previous?.visibility_rules ?? [],
    validation_rules: previous?.validation_rules ?? [],
    field_permissions: previous?.field_permissions ?? {},
    attachment_policy: {},
  };
}

export function blankFieldDraft(sequence = 1): FormFieldDraft {
  return {
    field_id: `field_${sequence}`,
    title: `Field ${sequence}`,
    type: "string",
    required: false,
    readOnly: false,
    writeOnly: false,
    enumOptions: "",
    description: "",
  };
}

/** Local preview wire matching task/start FormView shape (no server round-trip). */
export function previewFormDefinition(
  form: FormDefinition,
  values: JsonObject = {},
  revision = 1,
): RenderableForm {
  const issues = validateFormDefinition(form);
  if (issues.length > 0) throw new Error(issues[0]?.message ?? "invalid form");
  const properties = form.data_schema.properties;
  const required = new Set(form.data_schema.required ?? []);
  const order = [
    ...(form.layout.field_order ?? []),
    ...Object.keys(properties).filter(
      (key) => !(form.layout.field_order ?? []).includes(key),
    ),
  ];
  const fields: RenderableFormField[] = [];
  const publicValues: JsonObject = {};
  for (const fieldId of order) {
    const schema = properties[fieldId];
    if (!schema) continue;
    const secret = schema.writeOnly === true;
    const readOnly =
      schema.readOnly === true ||
      form.field_permissions[fieldId]?.read_only === true;
    const metadata = secret
      ? { type: schema.type, writeOnly: true }
      : { ...schema };
    fields.push({
      field_id: fieldId,
      label:
        typeof schema.title === "string" && schema.title.length > 0
          ? schema.title
          : fieldId,
      schema_json: canonical(metadata),
      editable: !readOnly,
      required: required.has(fieldId),
      write_only: secret,
    });
    if (!secret && Object.hasOwn(values, fieldId)) {
      publicValues[fieldId] = values[fieldId];
    }
  }
  return { revision, fields, values: publicValues };
}

export function parseRenderableForm(value: {
  revision: number;
  fields: RenderableFormField[];
  values: JsonObject;
}): RenderableForm {
  return {
    revision: value.revision,
    fields: value.fields.map((field) => ({ ...field })),
    values: { ...value.values },
  };
}

function workflowList(content: JsonObject): JsonObject[] {
  return Array.isArray(content.workflows)
    ? content.workflows.filter(isObject)
    : [];
}

function formList(content: JsonObject): FormDefinition[] {
  if (!Array.isArray(content.forms)) return [];
  const forms: FormDefinition[] = [];
  for (const value of content.forms) {
    try {
      forms.push(parseFormDefinition(value));
    } catch {
      // Skip unsupported / foreign form entries rather than fail the catalog.
    }
  }
  return forms;
}

export function catalogProjectForms(content: JsonObject): ProjectFormCatalog {
  const projectForms = formList(content);
  const bindings: ApprovalFormBinding[] = [];
  for (const workflow of workflowList(content)) {
    const workflowId =
      typeof workflow.workflow_id === "string" ? workflow.workflow_id : "";
    if (!isIdentifier(workflowId) || !Array.isArray(workflow.nodes)) continue;
    for (const node of workflow.nodes) {
      if (!isObject(node) || node.kind !== "approval") continue;
      if (!isIdentifier(node.node_id)) continue;
      let formId: string | null = null;
      let hasInlineForm = false;
      if (
        isObject(node.input_mapping) &&
        isObject(node.input_mapping.human_task) &&
        isObject(node.input_mapping.human_task.form) &&
        isObject(node.input_mapping.human_task.form.definition)
      ) {
        hasInlineForm = true;
        const id = node.input_mapping.human_task.form.definition.form_id;
        formId = typeof id === "string" ? id : null;
      }
      bindings.push({
        workflowId,
        nodeId: node.node_id,
        formId,
        hasInlineForm,
      });
    }
  }
  return { projectForms, bindings };
}

export function readInlineApprovalForm(
  content: JsonObject,
  workflowId: string,
  nodeId: string,
): { definition: FormDefinition; initial_values: JsonObject } | null {
  for (const workflow of workflowList(content)) {
    if (workflow.workflow_id !== workflowId || !Array.isArray(workflow.nodes))
      continue;
    for (const node of workflow.nodes) {
      if (
        !isObject(node) ||
        node.node_id !== nodeId ||
        !isObject(node.input_mapping) ||
        !isObject(node.input_mapping.human_task) ||
        !isObject(node.input_mapping.human_task.form)
      )
        continue;
      const form = node.input_mapping.human_task.form;
      if (!isObject(form.definition)) return null;
      return {
        definition: parseFormDefinition(form.definition),
        initial_values: isObject(form.initial_values)
          ? form.initial_values
          : {},
      };
    }
  }
  return null;
}

function formDefinitionJson(form: FormDefinition): JsonObject {
  return JSON.parse(
    canonical({
      form_id: form.form_id,
      schema_version: form.schema_version,
      data_schema: form.data_schema,
      layout: form.layout,
      visibility_rules: form.visibility_rules,
      validation_rules: form.validation_rules,
      field_permissions: form.field_permissions,
      attachment_policy: form.attachment_policy,
    }),
  ) as JsonObject;
}

export function upsertProjectForm(
  content: JsonObject,
  form: FormDefinition,
): JsonObject {
  const issues = validateFormDefinition(form);
  if (issues.length > 0) throw new Error(issues[0]?.message ?? "invalid form");
  const forms = formList(content).filter(
    (item) => item.form_id !== form.form_id,
  );
  forms.push(form);
  forms.sort((left, right) => left.form_id.localeCompare(right.form_id));
  return {
    ...content,
    forms: forms.map((item) => formDefinitionJson(item)),
  };
}

export function bindFormToApproval(
  content: JsonObject,
  workflowId: string,
  nodeId: string,
  form: FormDefinition,
  initialValues: JsonObject = {},
): JsonObject {
  const issues = validateFormDefinition(form);
  if (issues.length > 0) throw new Error(issues[0]?.message ?? "invalid form");
  let found = false;
  const workflows = workflowList(content).map((workflow) => {
    if (workflow.workflow_id !== workflowId || !Array.isArray(workflow.nodes))
      return workflow;
    const nodes = workflow.nodes.map((node) => {
      if (!isObject(node) || node.node_id !== nodeId || node.kind !== "approval")
        return node;
      found = true;
      const mapping = isObject(node.input_mapping) ? node.input_mapping : {};
      const humanTask = isObject(mapping.human_task)
        ? mapping.human_task
        : {
            rule: "any_one",
            user_ids: ["pending-assignment"],
            group_ids: [],
          };
      const previousForm = isObject(humanTask.form) ? humanTask.form : {};
      return {
        ...node,
        input_mapping: {
          ...mapping,
          human_task: {
            ...humanTask,
            form: {
              ...previousForm,
              definition: formDefinitionJson(form),
              initial_values: isObject(previousForm.initial_values)
                ? previousForm.initial_values
                : initialValues,
            },
          },
        },
      };
    });
    return { ...workflow, nodes };
  });
  if (!found) throw new Error("approval node not found");
  return { ...content, workflows };
}

export interface FormAuthoringClient {
  listProjects(): Promise<ProjectSummary[]>;
  readProject(id: string): Promise<ProjectRevision>;
  saveProject(
    id: string,
    expectedRevision: number,
    content: JsonObject,
    summary: string,
  ): Promise<ProjectRevision>;
}

export function createFormAuthoringClient(
  connection: ProjectConnection,
  fetcher: typeof fetch = fetch,
): FormAuthoringClient {
  const client: ProjectClient = createProjectClient(connection, fetcher);
  return {
    listProjects: () => client.listProjects(),
    readProject: (id) => client.readProject(id),
    saveProject: (id, expectedRevision, content, summary) =>
      client.saveProject(id, expectedRevision, content, summary),
  };
}

export function nextFieldSequence(drafts: FormFieldDraft[]): number {
  let sequence = drafts.length + 1;
  const ids = new Set(drafts.map((draft) => draft.field_id));
  while (ids.has(`field_${sequence}`)) sequence += 1;
  return sequence;
}

export type { JsonObject, JsonValue, ProjectConnection, ProjectRevision };
