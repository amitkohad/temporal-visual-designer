/** Presentation-only projection. Executable workflow fields are never rewritten. */
import type { DesignerDocument, DesignerNode, NodeKind } from "./model";
import { isIdentifier, isObject, ProjectApiError } from "./project-api";
import type { JsonObject, JsonValue, ProjectRevision } from "./project-api";

const kinds: Record<string, NodeKind> = {
  start: "start",
  end: "end",
  approval: "approval",
  decision: "decision",
  wait: "timer",
  set_data: "update",
  child_workflow: "subworkflow",
};
function own(object: JsonObject, key: string): JsonValue | undefined {
  return Object.hasOwn(object, key) ? object[key] : undefined;
}
function copy(value: JsonObject): JsonObject {
  function check(item: unknown, depth: number): void {
    if (depth > 100) throw new Error();
    if (item === null || typeof item === "string" || typeof item === "boolean")
      return;
    if (
      typeof item === "number" &&
      Number.isFinite(item) &&
      (!Number.isInteger(item) || Number.isSafeInteger(item))
    )
      return;
    if (Array.isArray(item)) {
      for (const child of item) check(child, depth + 1);
      return;
    }
    if (!isObject(item) || Object.getPrototypeOf(item) !== Object.prototype)
      throw new Error();
    for (const child of Object.values(item)) check(child, depth + 1);
  }
  check(value, 0);
  const text = JSON.stringify(value);
  if (new TextEncoder().encode(text).byteLength > 6 * 1024 * 1024)
    throw new Error();
  return JSON.parse(text) as JsonObject;
}
function workflows(revision: ProjectRevision): JsonObject[] {
  if (
    !isObject(revision) ||
    !isObject(revision.content) ||
    revision.content.schema_version !== "0.1" ||
    !Array.isArray(revision.content.workflows) ||
    revision.content.workflows.length > 100
  )
    throw new Error();
  const ids = new Set<string>();
  return revision.content.workflows.map((value) => {
    if (
      !isObject(value) ||
      value.schema_version !== "0.1" ||
      !isIdentifier(value.workflow_id) ||
      ids.has(value.workflow_id)
    )
      throw new Error();
    ids.add(value.workflow_id);
    return value;
  });
}
function layoutNode(value: unknown): value is JsonObject & {
  label: string;
  description: string;
  x: number;
  y: number;
} {
  return (
    isObject(value) &&
    typeof value.label === "string" &&
    value.label.trim().length >= 1 &&
    value.label.trim().length <= 80 &&
    value.label === value.label.trim() &&
    typeof value.description === "string" &&
    value.description.length <= 240 &&
    typeof value.x === "number" &&
    Number.isFinite(value.x) &&
    value.x >= 80 &&
    value.x <= 1090 &&
    typeof value.y === "number" &&
    Number.isFinite(value.y) &&
    value.y >= 40 &&
    value.y <= 740
  );
}
function metadata(content: JsonObject): {
  layout: JsonObject;
  namespace: JsonObject;
  workflows: JsonObject;
} {
  const storedLayout = own(content, "layout");
  const layout = storedLayout === undefined ? {} : storedLayout;
  if (!isObject(layout)) throw new Error();
  const namespace = own(layout, "nexus_flow_designer");
  if (namespace === undefined)
    return { layout, namespace: { schema_version: "0.1" }, workflows: {} };
  if (
    !isObject(namespace) ||
    namespace.schema_version !== "0.1" ||
    !isObject(namespace.workflows) ||
    Object.keys(namespace.workflows).length > 100
  )
    throw new Error();
  for (const [id, workflow] of Object.entries(namespace.workflows)) {
    if (
      !isIdentifier(id) ||
      !isObject(workflow) ||
      !isObject(workflow.nodes) ||
      Object.keys(workflow.nodes).length > 1000
    )
      throw new Error();
    for (const [nodeId, node] of Object.entries(workflow.nodes))
      if (!isIdentifier(nodeId) || !layoutNode(node)) throw new Error();
  }
  return { layout, namespace, workflows: namespace.workflows };
}
function project(
  revision: ProjectRevision,
  workflowId: string,
): DesignerDocument {
  if (!isIdentifier(workflowId)) throw new Error();
  const workflow = workflows(revision).find(
    (value) => value.workflow_id === workflowId,
  );
  if (
    !workflow ||
    !Array.isArray(workflow.nodes) ||
    workflow.nodes.length < 2 ||
    workflow.nodes.length > 50 ||
    !Array.isArray(workflow.edges) ||
    workflow.edges.length > 200 ||
    !Array.isArray(workflow.error_routes) ||
    workflow.error_routes.length !== 0
  )
    throw new Error();
  const nodeValues = workflow.nodes;
  const stored = metadata(revision.content);
  const workflowLayout = own(stored.workflows, workflowId);
  const nodesLayout =
    isObject(workflowLayout) && isObject(workflowLayout.nodes)
      ? workflowLayout.nodes
      : {};
  if (
    nodeValues.length > 16 &&
    nodeValues.some(
      (value) =>
        !isObject(value) ||
        !isIdentifier(value.node_id) ||
        !Object.hasOwn(nodesLayout, value.node_id),
    )
  )
    throw new Error();
  const ids = new Set<string>();
  const ports = new Map<string, { inputs: string[]; outputs: string[] }>();
  const nodes: DesignerNode[] = nodeValues.map((value, index) => {
    if (
      !isObject(value) ||
      !isIdentifier(value.node_id) ||
      ids.has(value.node_id) ||
      typeof value.kind !== "string" ||
      !Object.hasOwn(kinds, value.kind) ||
      !isObject(value.named_ports) ||
      !Array.isArray(value.named_ports.inputs) ||
      !Array.isArray(value.named_ports.outputs) ||
      !value.named_ports.inputs.every(isIdentifier) ||
      !value.named_ports.outputs.every(isIdentifier) ||
      !isObject(value.input_mapping)
    )
      throw new Error();
    ids.add(value.node_id);
    ports.set(value.node_id, {
      inputs: value.named_ports.inputs as string[],
      outputs: value.named_ports.outputs as string[],
    });
    const nodeLayout = own(nodesLayout, value.node_id);
    if (nodeLayout !== undefined && !layoutNode(nodeLayout)) throw new Error();
    return {
      id: value.node_id,
      kind: kinds[value.kind],
      label:
        nodeLayout === undefined
          ? value.node_id
          : ((nodeLayout as JsonObject).label as string),
      description:
        nodeLayout === undefined
          ? ""
          : ((nodeLayout as JsonObject).description as string),
      x:
        nodeLayout === undefined
          ? nodeValues.length <= 5
            ? 570
            : 210 + (index % 4) * 240
          : ((nodeLayout as JsonObject).x as number),
      y:
        nodeLayout === undefined
          ? nodeValues.length <= 5
            ? 140 + index * 140
            : 160 + Math.floor(index / 4) * 160
          : ((nodeLayout as JsonObject).y as number),
    };
  });
  const edges = workflow.edges.map((value, index) => {
    if (
      !isObject(value) ||
      !isIdentifier(value.source_node_id) ||
      !isIdentifier(value.target_node_id) ||
      !isIdentifier(value.source_port) ||
      !isIdentifier(value.target_port) ||
      !ids.has(value.source_node_id) ||
      !ids.has(value.target_node_id) ||
      !ports.get(value.source_node_id)!.outputs.includes(value.source_port) ||
      !ports.get(value.target_node_id)!.inputs.includes(value.target_port)
    )
      throw new Error();
    const source = nodes.find((node) => node.id === value.source_node_id)!;
    return {
      id: `edge-${index}`,
      source: value.source_node_id,
      target: value.target_node_id,
      ...(source.kind === "decision" ? { label: value.source_port } : {}),
    };
  });
  return { nodes, edges };
}

export function listWorkflows(revision: ProjectRevision): { id: string }[] {
  try {
    return workflows(revision).map((value) => ({
      id: value.workflow_id as string,
    }));
  } catch {
    throw new ProjectApiError("invalid_response");
  }
}
export function projectDocument(
  revision: ProjectRevision,
  workflowId: string,
): DesignerDocument {
  try {
    return project(revision, workflowId);
  } catch {
    throw new ProjectApiError("invalid_response");
  }
}
export function withDesignerLayout(
  revision: ProjectRevision,
  workflowId: string,
  document: DesignerDocument,
): JsonObject {
  try {
    const expected = project(revision, workflowId);
    if (
      !isObject(document) ||
      !Array.isArray(document.nodes) ||
      document.nodes.length !== expected.nodes.length ||
      !Array.isArray(document.edges) ||
      JSON.stringify(document.edges) !== JSON.stringify(expected.edges)
    )
      throw new Error();
    const ids = new Set<string>();
    for (const node of document.nodes) {
      const original = expected.nodes.find((value) => value.id === node.id);
      if (
        !original ||
        ids.has(node.id) ||
        original.kind !== node.kind ||
        !layoutNode(node)
      )
        throw new Error();
      ids.add(node.id);
    }
    const content = copy(revision.content);
    const stored = metadata(content);
    const workflowLayout = own(stored.workflows, workflowId);
    const prior = isObject(workflowLayout) ? workflowLayout : {};
    let nodes: JsonObject = isObject(prior.nodes) ? prior.nodes : {};
    for (const node of document.nodes) {
      const priorNode = own(nodes, node.id);
      nodes = {
        ...nodes,
        [node.id]: {
          ...(isObject(priorNode) ? priorNode : {}),
          label: node.label,
          description: node.description,
          x: node.x,
          y: node.y,
        },
      };
    }
    return {
      ...content,
      layout: {
        ...stored.layout,
        nexus_flow_designer: {
          ...stored.namespace,
          schema_version: "0.1",
          workflows: { ...stored.workflows, [workflowId]: { ...prior, nodes } },
        },
      },
    };
  } catch {
    throw new ProjectApiError("invalid_response");
  }
}
