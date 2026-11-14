import { describe, expect, it } from "vitest";
import {
  listWorkflows,
  projectContentForSave,
  projectDocument,
  withDesignerLayout,
  withExecutableWorkflow,
} from "./project-projection";
import type { JsonObject, JsonValue, ProjectRevision } from "./project-api";

function fixture(): ProjectRevision {
  const workflow = (id: string): JsonObject => ({
    schema_version: "0.1",
    workflow_id: id,
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
        input_mapping: { human_task: { user_ids: ["private-user"] } },
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
  });
  return {
    project_id: "hr",
    revision: 1,
    parent_revision: null,
    summary: "initial",
    author_id: "alice",
    created_at: "2026-11-10T09:00:00Z",
    content: {
      schema_version: "0.1",
      workflows: [workflow("hr"), workflow("purchase")],
      forms: [{ form_id: "private-form", layout: { keep: true } }],
      activity_refs: [],
      connections: [{ connection_id: "private-connector" }],
      configuration_schema: { type: "object" },
      tests: [{ input: { private: "keep" } }],
      layout: { unknown: "keep" },
    },
  };
}
function workflow(revision: ProjectRevision): JsonObject {
  return (revision.content.workflows as JsonObject[])[0];
}
function withMetadata(revision: ProjectRevision): ProjectRevision {
  return {
    ...revision,
    content: {
      ...revision.content,
      layout: {
        unknown: "keep",
        nexus_flow_designer: {
          schema_version: "0.1",
          extension: { keep: true },
          workflows: {
            hr: {
              extension: "workflow-keep",
              nodes: {
                review: {
                  label: "Manager review",
                  description: "Confidential request",
                  x: 570,
                  y: 280,
                  extension: { retained: true },
                },
              },
            },
            purchase: {
              nodes: {
                review: {
                  label: "Purchasing",
                  description: "",
                  x: 300,
                  y: 400,
                },
              },
            },
          },
        },
      },
    },
  };
}
describe("presentation-only project projection", () => {
  it("lists workflow identity and makes stable defaults clear of the caption", () => {
    const source = fixture();
    expect(listWorkflows(source)).toEqual([{ id: "hr" }, { id: "purchase" }]);
    const document = projectDocument(source, "hr");
    expect(
      document.nodes.map((node) => [node.id, node.kind, node.x, node.y]),
    ).toEqual([
      ["start", "start", 570, 140],
      ["review", "approval", 570, 280],
      ["end", "end", 570, 420],
    ]);
    expect(document.edges).toEqual([
      { id: "edge-0", source: "start", target: "review" },
      { id: "edge-1", source: "review", target: "end" },
    ]);
    expect(JSON.stringify(document)).not.toContain("private-user");
    document.nodes[1].label = "changed";
    expect(projectDocument(source, "hr").nodes[1].label).toBe("review");
  });
  it("round trips metadata while preserving all executable/resource fields and unknown metadata", () => {
    const source = withMetadata(fixture());
    const before = structuredClone(source);
    const document = projectDocument(source, "hr");
    document.nodes[1] = {
      ...document.nodes[1],
      label: "Finance <review>",
      description: "Reviewed",
      x: 600,
      y: 300,
    };
    const content = withDesignerLayout(source, "hr", document);
    const expected = structuredClone(source.content);
    const actualLayout = content.layout as JsonObject;
    const actualNamespace = actualLayout.nexus_flow_designer as JsonObject;
    const actualWorkflows = actualNamespace.workflows as JsonObject;
    const actualWorkflow = actualWorkflows.hr as JsonObject;
    const actualNodes = actualWorkflow.nodes as JsonObject;
    expect(actualNodes.review).toEqual({
      label: "Finance <review>",
      description: "Reviewed",
      x: 600,
      y: 300,
      extension: { retained: true },
    });
    expect(actualWorkflows.purchase).toEqual(
      (
        (
          (source.content.layout as JsonObject)
            .nexus_flow_designer as JsonObject
        ).workflows as JsonObject
      ).purchase,
    );
    expect(actualNamespace.extension).toEqual({ keep: true });
    expect(actualWorkflow.extension).toBe("workflow-keep");
    delete expected.layout;
    const untouched = structuredClone(content);
    delete untouched.layout;
    expect(untouched).toEqual(expected);
    expect(source).toEqual(before);
  });
  it("preserves independently staged changes in multiple workflows", () => {
    const source = fixture();
    const first = projectDocument(source, "hr");
    first.nodes[1].label = "HR edited";
    const staged = {
      ...source,
      content: withDesignerLayout(source, "hr", first),
    };
    const second = projectDocument(staged, "purchase");
    second.nodes[1].label = "Purchasing edited";
    const finished = {
      ...staged,
      content: withDesignerLayout(staged, "purchase", second),
    };
    expect(projectDocument(finished, "hr").nodes[1].label).toBe("HR edited");
    expect(projectDocument(finished, "purchase").nodes[1].label).toBe(
      "Purchasing edited",
    );
  });
  it.each([
    (value: JsonObject) => {
      value.nodes = [];
    },
    (value: JsonObject) => {
      (value.nodes as JsonObject[])[1].kind = "activity";
    },
    (value: JsonObject) => {
      (value.nodes as JsonObject[])[1].node_id = "start";
    },
    (value: JsonObject) => {
      (value.edges as JsonObject[])[0].target_node_id = "unknown";
    },
    (value: JsonObject) => {
      (value.edges as JsonObject[])[0].source_port = "wrong";
    },
    (value: JsonObject) => {
      value.error_routes = [(value.edges as JsonObject[])[0]];
    },
    (value: JsonObject) => {
      value.error_routes = null;
    },
    (value: JsonObject) => {
      value.edges = Array(201).fill((value.edges as JsonObject[])[0]);
    },
  ])("rejects unsupported or incomplete graph %#", (change) => {
    const source = fixture();
    change(workflow(source));
    expect(() => projectDocument(source, "hr")).toThrow(
      "Project request failed",
    );
  });
  it.each([
    null,
    [],
    { nexus_flow_designer: null },
    { nexus_flow_designer: { schema_version: "0.2", workflows: {} } },
    {
      nexus_flow_designer: {
        schema_version: "0.1",
        workflows: {
          hr: { nodes: { review: { label: "", description: "", x: 1, y: 2 } } },
        },
      },
    },
  ])("rejects malformed existing layout %#", (layout) => {
    const source = fixture();
    source.content.layout = layout as JsonValue;
    expect(() => projectDocument(source, "hr")).toThrow(
      "Project request failed",
    );
  });
  it.each([
    (document: ReturnType<typeof projectDocument>) => {
      document.nodes.pop();
    },
    (document: ReturnType<typeof projectDocument>) => {
      document.nodes[1].id = "tampered";
    },
    (document: ReturnType<typeof projectDocument>) => {
      document.nodes[1].kind = "timer";
    },
    (document: ReturnType<typeof projectDocument>) => {
      document.edges[0].target = "end";
    },
    (document: ReturnType<typeof projectDocument>) => {
      document.nodes[1].label = "  ";
    },
    (document: ReturnType<typeof projectDocument>) => {
      document.nodes[1].label = "x".repeat(81);
    },
    (document: ReturnType<typeof projectDocument>) => {
      document.nodes[1].description = "x".repeat(241);
    },
    (document: ReturnType<typeof projectDocument>) => {
      document.nodes[1].x = NaN;
    },
    (document: ReturnType<typeof projectDocument>) => {
      document.nodes[1].y = 741;
    },
  ])(
    "rejects graph mutation or invalid presentation without partial updates %#",
    (change) => {
      const source = withMetadata(fixture());
      const before = structuredClone(source);
      const document = projectDocument(source, "hr");
      change(document);
      expect(() => withDesignerLayout(source, "hr", document)).toThrow(
        "Project request failed",
      );
      expect(source).toEqual(before);
    },
  );
  it("fails closed on dense layouts without complete metadata and accepts explicit layouts", () => {
    const source = fixture();
    const template = (workflow(source).nodes as JsonObject[])[1];
    workflow(source).nodes = Array.from({ length: 17 }, (_, index) => ({
      ...template,
      node_id: "step-" + index,
    }));
    workflow(source).edges = [];
    expect(() => projectDocument(source, "hr")).toThrow(
      "Project request failed",
    );
    const nodes = Object.fromEntries(
      Array.from({ length: 17 }, (_, index) => [
        "step-" + index,
        {
          label: "Step " + index,
          description: "",
          x: 200,
          y: 140 + index * 30,
        },
      ]),
    );
    source.content.layout = {
      nexus_flow_designer: {
        schema_version: "0.1",
        workflows: { hr: { nodes } },
      },
    };
    expect(projectDocument(source, "hr").nodes).toHaveLength(17);
  });
  it("lays out medium graphs with sufficient default row spacing", () => {
    const source = fixture();
    const template = (workflow(source).nodes as JsonObject[])[1];
    workflow(source).nodes = Array.from({ length: 16 }, (_, index) => ({
      ...template,
      node_id: "step-" + index,
    }));
    workflow(source).edges = [];
    const nodes = projectDocument(source, "hr").nodes;
    expect(nodes[0]).toMatchObject({ x: 210, y: 160 });
    expect(nodes[15]).toMatchObject({ x: 930, y: 640 });
  });
  it("supports prototype-named own identifiers without modifying prototypes", () => {
    const source = fixture();
    workflow(source).workflow_id = "__proto__";
    const nodes = workflow(source).nodes as JsonObject[];
    nodes[1].node_id = "constructor";
    const edges = workflow(source).edges as JsonObject[];
    edges[0].target_node_id = "constructor";
    edges[1].source_node_id = "constructor";
    source.content.layout = JSON.parse('{"__proto__":{"retained":true}}');
    const document = projectDocument(source, "__proto__");
    document.nodes[1].label = "Safe own metadata";
    const content = withDesignerLayout(source, "__proto__", document);
    const layout = content.layout as JsonObject;
    expect(Object.hasOwn(layout, "__proto__")).toBe(true);
    const namespace = layout.nexus_flow_designer as JsonObject;
    expect(Object.hasOwn(namespace.workflows as JsonObject, "__proto__")).toBe(
      true,
    );
    expect(
      projectDocument({ ...source, content }, "__proto__").nodes[1].label,
    ).toBe("Safe own metadata");
    expect(Object.prototype).not.toHaveProperty("retained");
  });
  it("does not round opaque unsafe integer data when staging a layout", () => {
    const source = fixture();
    const document = projectDocument(source, "hr");
    source.content.numeric = 9007199254740992;
    expect(() => withDesignerLayout(source, "hr", document)).toThrow(
      "Project request failed",
    );
  });
  it("writes executable insert while preserving sibling workflows and resources", () => {
    const source = withMetadata(fixture());
    const baseline = projectDocument(source, "hr");
    const document = projectDocument(source, "hr");
    document.nodes.push({
      id: "timer-1",
      kind: "timer",
      label: "Cooldown",
      description: "Wait one minute",
      x: 600,
      y: 320,
    });
    document.edges = [
      { id: "edge-0", source: "start", target: "review" },
      { id: "edge-insert", source: "review", target: "timer-1" },
      { id: "timer-1-next", source: "timer-1", target: "end" },
    ];
    const layoutOnly = projectContentForSave(source, "hr", baseline, baseline);
    expect(layoutOnly.executable).toBe(false);
    const saved = projectContentForSave(source, "hr", document, baseline);
    expect(saved.executable).toBe(true);
    const workflows = saved.content.workflows as JsonObject[];
    expect(workflows[0].workflow_id).toBe("hr");
    expect((workflows[0].nodes as JsonObject[]).map((node) => node.node_id)).toEqual(
      ["start", "review", "end", "timer-1"],
    );
    expect(
      (workflows[0].nodes as JsonObject[]).find(
        (node) => node.node_id === "timer-1",
      ),
    ).toMatchObject({ kind: "wait", input_mapping: { seconds: 60 } });
    expect(
      (workflows[0].nodes as JsonObject[]).find(
        (node) => node.node_id === "review",
      )?.input_mapping,
    ).toEqual({ human_task: { user_ids: ["private-user"] } });
    expect(workflows[1]).toEqual(
      (source.content.workflows as JsonObject[])[1],
    );
    expect(saved.content.forms).toEqual(source.content.forms);
    expect(
      (
        (
          (saved.content.layout as JsonObject)
            .nexus_flow_designer as JsonObject
        ).workflows as JsonObject
      ).purchase,
    ).toEqual(
      (
        (
          (source.content.layout as JsonObject)
            .nexus_flow_designer as JsonObject
        ).workflows as JsonObject
      ).purchase,
    );
    expect(
      projectDocument({ ...source, content: saved.content }, "hr").nodes.map(
        (node) => node.id,
      ),
    ).toEqual(["start", "review", "end", "timer-1"]);
    expect(() => withExecutableWorkflow(source, "hr", baseline)).not.toThrow();
  });
});
