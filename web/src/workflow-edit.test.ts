import { describe, expect, it } from "vitest";
import type { DesignerDocument } from "./model";
import {
  canDeleteStep,
  canInsertAfter,
  canReorderStep,
  defaultWireNode,
  deleteDesignerStep,
  rebuildWorkflowFromDocument,
  reorderDesignerStep,
  sameTopology,
} from "./workflow-edit";
import type { JsonObject } from "./project-api";

function linear(): DesignerDocument {
  return {
    nodes: [
      {
        id: "start",
        kind: "start",
        label: "Start",
        description: "",
        x: 400,
        y: 90,
      },
      {
        id: "manager",
        kind: "approval",
        label: "Manager",
        description: "",
        x: 400,
        y: 220,
      },
      {
        id: "hr",
        kind: "approval",
        label: "HR",
        description: "",
        x: 400,
        y: 350,
      },
      {
        id: "end",
        kind: "end",
        label: "End",
        description: "",
        x: 400,
        y: 480,
      },
    ],
    edges: [
      { id: "e0", source: "start", target: "manager" },
      { id: "e1", source: "manager", target: "hr" },
      { id: "e2", source: "hr", target: "end" },
    ],
  };
}

function wire(): JsonObject {
  return {
    schema_version: "0.1",
    workflow_id: "employee-onboarding",
    input_schema: { type: "object" },
    output_schema: {},
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
        node_id: "manager",
        kind: "approval",
        named_ports: { inputs: ["in"], outputs: ["out"] },
        input_mapping: {
          human_task: {
            rule: "any_one",
            user_ids: ["manager-user"],
            group_ids: [],
          },
        },
      },
      {
        node_id: "hr",
        kind: "approval",
        named_ports: { inputs: ["in"], outputs: ["out"] },
        input_mapping: {
          human_task: {
            rule: "any_one",
            user_ids: ["hr-user"],
            group_ids: [],
          },
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
        target_node_id: "manager",
        target_port: "in",
      },
      {
        source_node_id: "manager",
        source_port: "out",
        target_node_id: "hr",
        target_port: "in",
      },
      {
        source_node_id: "hr",
        source_port: "out",
        target_node_id: "end",
        target_port: "in",
      },
    ],
    error_routes: [],
  };
}

describe("workflow edit helpers", () => {
  it("deletes and reorders mid-chain steps without touching start or end", () => {
    const document = linear();
    expect(canDeleteStep(document, "start")).toBe(false);
    expect(canDeleteStep(document, "manager")).toBe(true);
    expect(canReorderStep(document, "hr", "earlier")).toBe(true);
    const deleted = deleteDesignerStep(document, "manager")!;
    expect(deleted.nodes.map((node) => node.id)).toEqual([
      "start",
      "hr",
      "end",
    ]);
    expect(deleted.edges).toEqual([
      { id: "e2", source: "hr", target: "end" },
      { id: "e0", source: "start", target: "hr" },
    ]);
    const reordered = reorderDesignerStep(document, "hr", "earlier")!;
    expect(
      reordered.edges.find((edge) => edge.source === "start")?.target,
    ).toBe("hr");
    expect(reordered.edges.find((edge) => edge.source === "hr")?.target).toBe(
      "manager",
    );
  });

  it("rebuilds executable workflow JSON for inserted wait/approval steps", () => {
    const document = linear();
    expect(canInsertAfter(document, "manager")).toBe(true);
    document.nodes.push({
      id: "timer-1",
      kind: "timer",
      label: "Wait timer",
      description: "Duration not configured",
      x: 520,
      y: 300,
    });
    document.edges = [
      { id: "e0", source: "start", target: "manager" },
      { id: "e1", source: "manager", target: "timer-1" },
      { id: "timer-1-next", source: "timer-1", target: "hr" },
      { id: "e2", source: "hr", target: "end" },
    ];
    expect(sameTopology(linear(), document)).toBe(false);
    const rebuilt = rebuildWorkflowFromDocument(wire(), document);
    expect((rebuilt.nodes as JsonObject[]).map((node) => node.node_id)).toEqual(
      ["start", "manager", "hr", "end", "timer-1"],
    );
    const inserted = (rebuilt.nodes as JsonObject[]).find(
      (node) => node.node_id === "timer-1",
    );
    expect(inserted).toMatchObject({
      kind: "wait",
      input_mapping: { seconds: 60 },
    });
    const manager = (rebuilt.nodes as JsonObject[]).find(
      (node) => node.node_id === "manager",
    );
    expect(manager?.input_mapping).toEqual({
      human_task: {
        rule: "any_one",
        user_ids: ["manager-user"],
        group_ids: [],
      },
    });
    expect(defaultWireNode("approval", "approval-1", wire()).kind).toBe(
      "approval",
    );
  });
});
