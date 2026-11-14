/** MVP executable graph edits for the connected Designer subset. */
import type {
  DesignerDocument,
  DesignerEdge,
  DesignerNode,
  NodeKind,
} from "./model";
import { isIdentifier, isObject, type JsonObject } from "./project-api";

const HISTORY_LIMIT = 50;
const NODE_LIMIT = 50;
const clamp = (value: number, minimum: number, maximum: number): number =>
  Math.max(minimum, Math.min(maximum, value));

const visualToWire: Partial<Record<NodeKind, string>> = {
  start: "start",
  end: "end",
  approval: "approval",
  decision: "decision",
  timer: "wait",
  update: "set_data",
  subworkflow: "child_workflow",
};

export type InsertableKind = "approval" | "timer";

export function topologyKey(document: DesignerDocument): string {
  return JSON.stringify({
    nodes: document.nodes.map((node) => [node.id, node.kind]),
    edges: document.edges,
  });
}

export function sameTopology(
  left: DesignerDocument,
  right: DesignerDocument,
): boolean {
  return topologyKey(left) === topologyKey(right);
}

export function wireKindForVisual(kind: NodeKind): string | null {
  return Object.hasOwn(visualToWire, kind) ? (visualToWire[kind] ?? null) : null;
}

export function canInsertAfter(
  document: DesignerDocument,
  afterId: string,
): boolean {
  if (document.nodes.length >= NODE_LIMIT) return false;
  const source = document.nodes.find((node) => node.id === afterId);
  if (!source || source.kind === "end" || source.kind === "decision")
    return false;
  return document.edges.filter((edge) => edge.source === afterId).length === 1;
}

export function canDeleteStep(
  document: DesignerDocument,
  nodeId: string,
): boolean {
  const node = document.nodes.find((item) => item.id === nodeId);
  if (!node || node.kind === "start" || node.kind === "end") return false;
  const incoming = document.edges.filter((edge) => edge.target === nodeId);
  const outgoing = document.edges.filter((edge) => edge.source === nodeId);
  return incoming.length === 1 && outgoing.length === 1;
}

export function canReorderStep(
  document: DesignerDocument,
  nodeId: string,
  direction: "earlier" | "later",
): boolean {
  const node = document.nodes.find((item) => item.id === nodeId);
  if (!node || node.kind === "start" || node.kind === "end") return false;
  const incoming = document.edges.filter((edge) => edge.target === nodeId);
  const outgoing = document.edges.filter((edge) => edge.source === nodeId);
  if (incoming.length !== 1 || outgoing.length !== 1) return false;
  if (direction === "earlier") {
    const predecessor = document.nodes.find(
      (item) => item.id === incoming[0].source,
    );
    if (!predecessor || predecessor.kind === "start") return false;
    return (
      document.edges.filter((edge) => edge.source === predecessor.id)
        .length === 1 &&
      document.edges.filter((edge) => edge.target === predecessor.id)
        .length === 1
    );
  }
  const successor = document.nodes.find(
    (item) => item.id === outgoing[0].target,
  );
  if (!successor || successor.kind === "end") return false;
  return (
    document.edges.filter((edge) => edge.source === successor.id).length ===
      1 &&
    document.edges.filter((edge) => edge.target === successor.id).length === 1
  );
}

export function deleteDesignerStep(
  document: DesignerDocument,
  nodeId: string,
): DesignerDocument | null {
  if (!canDeleteStep(document, nodeId)) return null;
  const incoming = document.edges.find((edge) => edge.target === nodeId)!;
  const outgoing = document.edges.find((edge) => edge.source === nodeId)!;
  const bridged: DesignerEdge = {
    id: incoming.id,
    source: incoming.source,
    target: outgoing.target,
    ...(incoming.label !== undefined
      ? { label: incoming.label }
      : outgoing.label !== undefined
        ? { label: outgoing.label }
        : {}),
  };
  return {
    nodes: document.nodes.filter((node) => node.id !== nodeId),
    edges: [
      ...document.edges.filter(
        (edge) => edge.source !== nodeId && edge.target !== nodeId,
      ),
      bridged,
    ],
  };
}

export function reorderDesignerStep(
  document: DesignerDocument,
  nodeId: string,
  direction: "earlier" | "later",
): DesignerDocument | null {
  if (!canReorderStep(document, nodeId, direction)) return null;
  const node = document.nodes.find((item) => item.id === nodeId)!;
  if (direction === "earlier") {
    const incoming = document.edges.find((edge) => edge.target === nodeId)!;
    const predecessorId = incoming.source;
    return swapAdjacent(document, predecessorId, node.id);
  }
  const outgoing = document.edges.find((edge) => edge.source === nodeId)!;
  return swapAdjacent(document, node.id, outgoing.target);
}

function swapAdjacent(
  document: DesignerDocument,
  leftId: string,
  rightId: string,
): DesignerDocument | null {
  const intoLeft = document.edges.find((edge) => edge.target === leftId);
  const leftToRight = document.edges.find(
    (edge) => edge.source === leftId && edge.target === rightId,
  );
  const outOfRight = document.edges.find((edge) => edge.source === rightId);
  if (!intoLeft || !leftToRight || !outOfRight) return null;
  const replaced = document.edges
    .filter(
      (edge) =>
        edge.id !== intoLeft.id &&
        edge.id !== leftToRight.id &&
        edge.id !== outOfRight.id,
    )
    .concat([
      {
        id: intoLeft.id,
        source: intoLeft.source,
        target: rightId,
        ...(intoLeft.label !== undefined ? { label: intoLeft.label } : {}),
      },
      {
        id: leftToRight.id,
        source: rightId,
        target: leftId,
        ...(leftToRight.label !== undefined
          ? { label: leftToRight.label }
          : {}),
      },
      {
        id: outOfRight.id,
        source: leftId,
        target: outOfRight.target,
        ...(outOfRight.label !== undefined ? { label: outOfRight.label } : {}),
      },
    ]);
  return { nodes: document.nodes, edges: replaced };
}

export function allocateStepId(
  document: DesignerDocument,
  kind: InsertableKind,
): string {
  let sequence = 1;
  while (
    document.nodes.some((node) => node.id === `${kind}-${sequence}`) ||
    document.edges.some((edge) => edge.id === `${kind}-${sequence}-next`)
  )
    sequence += 1;
  return `${kind}-${sequence}`;
}

/** Default wire payload for an inserted MVP step. Preserves template assignment shape when available. */
export function defaultWireNode(
  kind: "approval" | "wait",
  nodeId: string,
  workflow?: JsonObject,
): JsonObject {
  if (!isIdentifier(nodeId)) throw new Error("invalid node id");
  if (kind === "wait") {
    return {
      node_id: nodeId,
      kind: "wait",
      named_ports: { inputs: ["in"], outputs: ["out"] },
      input_mapping: { seconds: 60 },
    };
  }
  let humanTask: JsonObject = {
    rule: "any_one",
    user_ids: ["pending-assignment"],
    group_ids: [],
  };
  if (workflow && Array.isArray(workflow.nodes)) {
    for (const value of workflow.nodes) {
      if (
        isObject(value) &&
        value.kind === "approval" &&
        isObject(value.input_mapping) &&
        isObject(value.input_mapping.human_task)
      ) {
        humanTask = {
          ...value.input_mapping.human_task,
        };
        break;
      }
    }
  }
  return {
    node_id: nodeId,
    kind: "approval",
    named_ports: { inputs: ["in"], outputs: ["out"] },
    input_mapping: { human_task: humanTask },
  };
}

export function rebuildWorkflowFromDocument(
  workflow: JsonObject,
  document: DesignerDocument,
): JsonObject {
  if (
    !isObject(workflow) ||
    !Array.isArray(workflow.nodes) ||
    !Array.isArray(workflow.edges) ||
    document.nodes.length < 2 ||
    document.nodes.length > NODE_LIMIT ||
    document.edges.length > 200
  )
    throw new Error();
  const prior = new Map<string, JsonObject>();
  for (const value of workflow.nodes) {
    if (!isObject(value) || !isIdentifier(value.node_id)) throw new Error();
    prior.set(value.node_id, value);
  }
  const ids = new Set<string>();
  const nodes: JsonObject[] = document.nodes.map((node) => {
    if (ids.has(node.id) || !isIdentifier(node.id)) throw new Error();
    ids.add(node.id);
    const wireKind = wireKindForVisual(node.kind);
    if (!wireKind) throw new Error();
    const existing = prior.get(node.id);
    if (existing) {
      if (existing.kind !== wireKind) throw new Error();
      return { ...existing, node_id: node.id, kind: wireKind };
    }
    if (wireKind !== "approval" && wireKind !== "wait") throw new Error();
    return defaultWireNode(wireKind, node.id, workflow);
  });
  const ports = new Map(
    nodes.map((node) => {
      if (
        !isObject(node.named_ports) ||
        !Array.isArray(node.named_ports.inputs) ||
        !Array.isArray(node.named_ports.outputs)
      )
        throw new Error();
      return [
        node.node_id as string,
        {
          inputs: node.named_ports.inputs as string[],
          outputs: node.named_ports.outputs as string[],
        },
      ];
    }),
  );
  const priorEdges = workflow.edges.filter(isObject) as JsonObject[];
  const edges: JsonObject[] = document.edges.map((edge) => {
    if (
      !isIdentifier(edge.source) ||
      !isIdentifier(edge.target) ||
      !ids.has(edge.source) ||
      !ids.has(edge.target)
    )
      throw new Error();
    const sourceNode = document.nodes.find((node) => node.id === edge.source)!;
    const sourcePorts = ports.get(edge.source)!;
    const targetPorts = ports.get(edge.target)!;
    let sourcePort =
      sourceNode.kind === "decision"
        ? (edge.label ?? sourcePorts.outputs[0])
        : sourcePorts.outputs[0];
    const matched = priorEdges.find(
      (item) =>
        item.source_node_id === edge.source &&
        item.target_node_id === edge.target &&
        (sourceNode.kind !== "decision" ||
          item.source_port === edge.label ||
          edge.label === undefined),
    );
    if (matched && isIdentifier(matched.source_port))
      sourcePort = matched.source_port;
    const targetPort = targetPorts.inputs[0];
    if (
      !isIdentifier(sourcePort) ||
      !isIdentifier(targetPort) ||
      !sourcePorts.outputs.includes(sourcePort) ||
      !targetPorts.inputs.includes(targetPort)
    )
      throw new Error();
    return {
      source_node_id: edge.source,
      source_port: sourcePort,
      target_node_id: edge.target,
      target_port: targetPort,
    };
  });
  return {
    ...workflow,
    nodes,
    edges,
    error_routes: Array.isArray(workflow.error_routes)
      ? workflow.error_routes
      : [],
  };
}

export function commitDocument(
  past: DesignerDocument[],
  current: DesignerDocument,
  next: DesignerDocument,
  selectedId: string,
): {
  document: DesignerDocument;
  past: DesignerDocument[];
  future: DesignerDocument[];
  selectedId: string;
} {
  const selection = next.nodes.some((node) => node.id === selectedId)
    ? selectedId
    : (next.nodes[0]?.id ?? "");
  return {
    document: next,
    past: [...past, current].slice(-HISTORY_LIMIT),
    future: [],
    selectedId: selection,
  };
}

export function layoutDefaultsForInsert(
  source: DesignerNode,
  kind: InsertableKind,
  id: string,
): DesignerNode {
  return {
    id,
    kind,
    label: kind === "approval" ? "New approval" : "Wait timer",
    description:
      kind === "approval"
        ? "Approvers not configured"
        : "Duration not configured",
    x: clamp(source.x + 120, 80, 1090),
    y: clamp(source.y + 96, 40, 740),
  };
}
