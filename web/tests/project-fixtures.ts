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

export function saveAcknowledgement(body: {
  expected_revision: number;
  content: JsonObject;
}): ProjectRevision {
  return {
    ...projectRevision(body.expected_revision + 1),
    parent_revision: body.expected_revision,
    summary: "Update designer layout",
    content: body.content,
  };
}
