import { describe, expect, it } from "vitest";
import {
  bindFormToApproval,
  blankFieldDraft,
  catalogProjectForms,
  definitionFromDrafts,
  draftsFromDefinition,
  emptyFormDefinition,
  parseFormDefinition,
  previewFormDefinition,
  upsertProjectForm,
  validateFormDefinition,
} from "./form-api";

const sampleForm = {
  form_id: "confirm",
  schema_version: "0.1",
  data_schema: {
    type: "object",
    properties: {
      employee: { type: "string", title: "Employee" },
      confirmed: { type: "boolean", title: "Confirm", const: true },
      dept: {
        type: "string",
        title: "Department",
        enum: ["Engineering", "Finance"],
      },
    },
    required: ["employee", "confirmed"],
    additionalProperties: false,
  },
  layout: { field_order: ["employee", "dept", "confirmed"] },
  visibility_rules: [],
  validation_rules: [],
  field_permissions: {},
  attachment_policy: {},
};

describe("form-api", () => {
  it("parses, validates, and previews a supported form definition", () => {
    const form = parseFormDefinition(sampleForm);
    expect(validateFormDefinition(form)).toEqual([]);
    const preview = previewFormDefinition(form, { employee: "Ada" });
    expect(preview.fields.map((field) => field.field_id)).toEqual([
      "employee",
      "dept",
      "confirmed",
    ]);
    expect(preview.fields[0]).toMatchObject({
      label: "Employee",
      editable: true,
      required: true,
    });
    expect(preview.values).toEqual({ employee: "Ada" });
    expect(JSON.parse(preview.fields[1]!.schema_json).enum).toEqual([
      "Engineering",
      "Finance",
    ]);
  });

  it("round-trips drafts while preserving required and enum options", () => {
    const form = parseFormDefinition(sampleForm);
    const drafts = draftsFromDefinition(form);
    drafts.push({
      ...blankFieldDraft(4),
      field_id: "notes",
      title: "Notes",
      required: false,
    });
    const next = definitionFromDrafts(form.form_id, drafts, form);
    expect(next.data_schema.required).toEqual(["employee", "confirmed"]);
    expect(next.data_schema.properties.dept?.enum).toEqual([
      "Engineering",
      "Finance",
    ]);
    expect(next.layout.field_order).toEqual([
      "employee",
      "dept",
      "confirmed",
      "notes",
    ]);
  });

  it("rejects attachment policies and unknown required fields", () => {
    const form = emptyFormDefinition("bad");
    form.attachment_policy = { max_files: 1 };
    form.data_schema.required = ["missing"];
    const issues = validateFormDefinition(form);
    expect(issues.map((issue) => issue.code)).toEqual(
      expect.arrayContaining(["unsupported_attachments", "unknown_field"]),
    );
  });

  it("upserts project forms and binds inline approval definitions", () => {
    const content = {
      schema_version: "0.1",
      workflows: [
        {
          workflow_id: "main",
          nodes: [
            {
              node_id: "manager",
              kind: "approval",
              named_ports: { inputs: ["in"], outputs: ["out"] },
              input_mapping: {
                human_task: {
                  rule: "any_one",
                  user_ids: ["u1"],
                  group_ids: [],
                },
              },
            },
          ],
          edges: [],
        },
      ],
      forms: [],
    };
    const form = parseFormDefinition(sampleForm);
    const withForm = upsertProjectForm(content, form);
    expect(catalogProjectForms(withForm).projectForms).toHaveLength(1);
    const bound = bindFormToApproval(withForm, "main", "manager", form);
    const catalog = catalogProjectForms(bound);
    expect(catalog.bindings[0]).toMatchObject({
      workflowId: "main",
      nodeId: "manager",
      formId: "confirm",
      hasInlineForm: true,
    });
    const humanTask = (
      bound.workflows as Array<{
        nodes: Array<{ input_mapping: { human_task: { form: unknown } } }>;
      }>
    )[0]!.nodes[0]!.input_mapping.human_task;
    expect(
      (humanTask.form as { definition: { form_id: string } }).definition
        .form_id,
    ).toBe("confirm");
  });
});
