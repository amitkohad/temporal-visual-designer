import type { JsonObject, ProjectRevision } from "../src/project-api";

/** Python API wire fixtures; not a local designer document or a live API claim. */
export function projectRevision(revision = 7): ProjectRevision {
  return {
    project_id: "hr",
    revision,
    parent_revision: revision - 1,
    summary: "Author reviewed project",
    author_id: "author-oid",
    created_at: "2026-11-10T09:00:00Z",
    content: {
      schema_version: "0.1",
      workflows: [workflow("onboarding"), workflow("purchase")],
      forms: [
        {
          schema_version: "0.1",
          form_id: "employee",
          data_schema: {
            type: "object",
            properties: { name: { type: "string" } },
          },
          layout: { renderer: "standard" },
        },
      ],
      activity_refs: [],
      connections: [
        {
          connection_id: "hr-system",
          connector_type: "http",
          configuration_schema: { type: "object" },
        },
      ],
      configuration_schema: {
        type: "object",
        properties: { department: { type: "string" } },
      },
      tests: [
        {
          scenario_id: "happy-path",
          workflow_id: "onboarding",
          input: { name: "Example" },
        },
      ],
      layout: {
        retained: "project layout extension",
        nexus_flow_designer: {
          schema_version: "0.1",
          extension: "keep namespace extension",
          workflows: {
            onboarding: {
              extension: "keep workflow extension",
              nodes: {
                start: {
                  label: "Start onboarding",
                  description: "",
                  x: 400,
                  y: 90,
                  extension: "keep node extension",
                },
                review: {
                  label: "Manager review",
                  description: "Existing approval",
                  x: 400,
                  y: 230,
                },
                end: { label: "Complete", description: "", x: 400, y: 400 },
              },
            },
            purchase: {
              nodes: {
                review: {
                  label: "Purchase review",
                  description: "",
                  x: 500,
                  y: 220,
                },
              },
            },
          },
        },
      },
    },
  };
}

function workflow(workflowId: string): JsonObject {
  return {
    schema_version: "0.1",
    workflow_id: workflowId,
    input_schema: { type: "object" },
    output_schema: { type: "object" },
    start_definitions: [
      { start_id: "manual", node_id: "start", kind: "manual" },
    ],
    nodes: [
      {
        node_id: "start",
        kind: "start",
        named_ports: { inputs: [], outputs: ["out"] },
        input_mapping: {},
      },
      {
        node_id: "review",
        kind: "approval",
        named_ports: { inputs: ["in"], outputs: ["out"] },
        input_mapping: {
          assignment: { group_ids: ["managers"], rule: "any_one" },
        },
      },
      {
        node_id: "end",
        kind: "end",
        named_ports: { inputs: ["in"], outputs: [] },
        input_mapping: {},
      },
    ],
    edges: [
      {
        source_node_id: "start",
        source_port: "out",
        target_node_id: "review",
        target_port: "in",
      },
      {
        source_node_id: "review",
        source_port: "out",
        target_node_id: "end",
        target_port: "in",
      },
    ],
    error_routes: [],
  };
}

export function projectList() {
  return [
    {
      scope: { organization_id: "acme", environment_id: "dev" },
      project_id: "hr",
      name: "Human resources",
      head_revision: 7,
    },
  ];
}

export function templateList() {
  return [
    {
      template_id: "employee-onboarding",
      version: "1",
      name: "Employee onboarding",
      description: "Collect manager, HR and IT approvals for a new employee.",
      workflow_id: "employee-onboarding",
      assignment_slots: ["manager", "hr", "it"],
      input_schema: {
        type: "object",
        properties: {
          employee_name: { type: "string", minLength: 1 },
          department: { type: "string", minLength: 1 },
          start_date: { type: "string", minLength: 1 },
        },
        required: ["employee_name", "department", "start_date"],
        additionalProperties: false,
      },
    },
    {
      template_id: "purchase-requisition",
      version: "1",
      name: "Purchase requisition",
      description:
        "Collect manager and finance approvals for a purchase request.",
      workflow_id: "purchase-requisition",
      assignment_slots: ["manager", "finance"],
      input_schema: {
        type: "object",
        properties: {
          requester: { type: "string", minLength: 1 },
          item: { type: "string", minLength: 1 },
          amount: { type: "number", minimum: 0.01 },
          currency: { type: "string", minLength: 3, maxLength: 3 },
        },
        required: ["requester", "item", "amount", "currency"],
        additionalProperties: false,
      },
    },
  ];
}

export function templateProjectRevision(
  projectId = "people",
  name = "People onboarding",
): ProjectRevision {
  const slots = ["manager", "hr", "it"];
  const nodes = [
    {
      node_id: "start",
      kind: "start",
      named_ports: { inputs: [], outputs: ["out"] },
      input_mapping: {},
    },
    ...slots.map((slot) => ({
      node_id: slot,
      kind: "approval",
      named_ports: { inputs: ["in"], outputs: ["out"] },
      input_mapping: {
        human_task: {
          rule: "any_one",
          user_ids: ["manager-user"],
          group_ids: [],
          form: {
            definition: {
              form_id: `employee-onboarding-${slot}`,
              schema_version: "0.1",
              data_schema: { type: "object" },
            },
            initial_values: {},
          },
        },
      },
    })),
    {
      node_id: "end",
      kind: "end",
      named_ports: { inputs: ["in"], outputs: [] },
      input_mapping: {},
    },
  ];
  const ids = nodes.map((node) => node.node_id);
  return {
    project_id: projectId,
    revision: 1,
    parent_revision: null,
    summary: `Created from template employee-onboarding`,
    author_id: "author-oid",
    created_at: "2026-11-14T09:00:00Z",
    content: {
      schema_version: "0.1",
      workflows: [
        {
          schema_version: "0.1",
          workflow_id: "employee-onboarding",
          input_schema: {
            type: "object",
            properties: {
              employee_name: { type: "string", minLength: 1 },
              department: { type: "string", minLength: 1 },
              start_date: { type: "string", minLength: 1 },
            },
            required: ["employee_name", "department", "start_date"],
            additionalProperties: false,
          },
          output_schema: {},
          start_definitions: [
            { start_id: "manual", node_id: "start", kind: "manual" },
          ],
          nodes,
          edges: ids.slice(0, -1).map((source, index) => ({
            source_node_id: source,
            source_port: "out",
            target_node_id: ids[index + 1],
            target_port: "in",
          })),
          error_routes: [],
        },
      ],
      forms: [],
      activity_refs: [],
      connections: [],
      configuration_schema: { type: "object" },
      tests: [],
    },
  };
}

export function saveAcknowledgement(body: {
  expected_revision: number;
  content: JsonObject;
  summary?: string;
}): ProjectRevision {
  return {
    ...projectRevision(body.expected_revision + 1),
    parent_revision: body.expected_revision,
    summary: body.summary ?? "Update designer layout",
    content: body.content,
  };
}
