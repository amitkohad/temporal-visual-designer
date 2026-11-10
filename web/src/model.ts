/** Local authoring sample state. This is not a deployable workflow contract. */
export type NodeKind =
  | "start"
  | "approval"
  | "decision"
  | "reference"
  | "update"
  | "subworkflow"
  | "end"
  | "timer";

export interface DesignerNode {
  id: string;
  kind: NodeKind;
  label: string;
  description: string;
  x: number;
  y: number;
}

export interface DesignerEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
}

export interface DesignerDocument {
  nodes: DesignerNode[];
  edges: DesignerEdge[];
}

export interface EditorState {
  document: DesignerDocument;
  past: DesignerDocument[];
  future: DesignerDocument[];
  selectedId: string;
}

export type EditorAction =
  | { type: "select"; id: string }
  | { type: "rename"; id: string; label: string }
  | { type: "describe"; id: string; description: string }
  | { type: "edit"; id: string; label: string; description: string }
  | { type: "move"; id: string; dx: number; dy: number }
  | { type: "insert"; kind: "approval" | "timer"; afterId: string }
  | { type: "undo" }
  | { type: "redo" };

const HISTORY_LIMIT = 50;
const NODE_LIMIT = 50;
const clamp = (value: number, minimum: number, maximum: number): number =>
  Math.max(minimum, Math.min(maximum, value));

/** Every call owns independent arrays and node/edge objects. */
export function initialState(): EditorState {
  return {
    document: {
      nodes: [
        {
          id: "start",
          kind: "start",
          label: "Start",
          description: "",
          x: 570,
          y: 94,
        },
        {
          id: "approval",
          kind: "approval",
          label: "Manager approval",
          description: "Purchasing managers · Any one",
          x: 570,
          y: 208,
        },
        {
          id: "decision",
          kind: "decision",
          label: "Approval outcome",
          description: "",
          x: 570,
          y: 363,
        },
        {
          id: "reference",
          kind: "reference",
          label: "Create reference",
          description: "Generate purchase reference",
          x: 288,
          y: 363,
        },
        {
          id: "status",
          kind: "update",
          label: "Set request status",
          description: "Status = Rejected",
          x: 852,
          y: 363,
        },
        {
          id: "subworkflow",
          kind: "subworkflow",
          label: "Create purchase order",
          description: "Reusable workflow · v1.2",
          x: 288,
          y: 538,
        },
        {
          id: "end",
          kind: "end",
          label: "End",
          description: "",
          x: 570,
          y: 726,
        },
      ],
      edges: [
        { id: "start-approval", source: "start", target: "approval" },
        { id: "approval-decision", source: "approval", target: "decision" },
        {
          id: "decision-approved",
          source: "decision",
          target: "reference",
          label: "Approved",
        },
        {
          id: "decision-rejected",
          source: "decision",
          target: "status",
          label: "Rejected",
        },
        {
          id: "reference-subworkflow",
          source: "reference",
          target: "subworkflow",
        },
        { id: "subworkflow-end", source: "subworkflow", target: "end" },
        { id: "status-end", source: "status", target: "end" },
      ],
    },
    past: [],
    future: [],
    selectedId: "decision",
  };
}

function selection(document: DesignerDocument, selectedId: string): string {
  return document.nodes.some((node) => node.id === selectedId)
    ? selectedId
    : (document.nodes.find((node) => node.id === "decision")?.id ??
        document.nodes[0]?.id ??
        "");
}

function commit(
  state: EditorState,
  document: DesignerDocument,
  selectedId = state.selectedId,
): EditorState {
  return {
    document,
    past: [...state.past, state.document].slice(-HISTORY_LIMIT),
    future: [],
    selectedId: selection(document, selectedId),
  };
}

/** All edits are immutable and invalid/empty operations preserve state identity. */
export function editorReducer(
  state: EditorState,
  action: EditorAction,
): EditorState {
  if (typeof action !== "object" || action === null || Array.isArray(action))
    return state;
  if (action.type === "undo") {
    const document = state.past.at(-1);
    if (!document) return state;
    return {
      document,
      past: state.past.slice(0, -1),
      future: [state.document, ...state.future].slice(0, HISTORY_LIMIT),
      selectedId: selection(document, state.selectedId),
    };
  }
  if (action.type === "redo") {
    const document = state.future[0];
    if (!document) return state;
    return {
      document,
      past: [...state.past, state.document].slice(-HISTORY_LIMIT),
      future: state.future.slice(1),
      selectedId: selection(document, state.selectedId),
    };
  }
  if (action.type === "insert") {
    if (
      (action.kind !== "approval" && action.kind !== "timer") ||
      typeof action.afterId !== "string" ||
      state.document.nodes.length >= NODE_LIMIT
    )
      return state;
    const source = state.document.nodes.find(
      (node) => node.id === action.afterId,
    );
    const outgoing = state.document.edges.filter(
      (edge) => edge.source === action.afterId,
    );
    if (!source || source.kind === "end" || outgoing.length !== 1) return state;
    const edge = outgoing[0];
    if (!edge) return state;
    let sequence = 1;
    while (
      state.document.nodes.some(
        (node) => node.id === `${action.kind}-${sequence}`,
      ) ||
      state.document.edges.some(
        (item) => item.id === `${action.kind}-${sequence}-next`,
      )
    )
      sequence += 1;
    const id = `${action.kind}-${sequence}`;
    const node: DesignerNode = {
      id,
      kind: action.kind,
      label: action.kind === "approval" ? "New approval" : "Wait timer",
      description:
        action.kind === "approval"
          ? "Approvers not configured"
          : "Duration not configured",
      x: clamp(source.x + 120, 80, 1090),
      y: clamp(source.y + 96, 40, 740),
    };
    return commit(
      state,
      {
        nodes: [...state.document.nodes, node],
        edges: [
          ...state.document.edges.map((item) =>
            item.id === edge.id ? { ...item, target: id } : item,
          ),
          { id: `${id}-next`, source: id, target: edge.target },
        ],
      },
      id,
    );
  }
  if (!("id" in action) || typeof action.id !== "string") return state;
  const node = state.document.nodes.find((item) => item.id === action.id);
  if (!node) return state;
  if (action.type === "select") {
    return node.id === state.selectedId
      ? state
      : { ...state, selectedId: node.id };
  }
  let updated: DesignerNode;
  switch (action.type) {
    case "edit": {
      if (
        typeof action.label !== "string" ||
        typeof action.description !== "string"
      )
        return state;
      const label = action.label.trim();
      if (!label || label.length > 80 || action.description.length > 240)
        return state;
      if (label === node.label && action.description === node.description)
        return state;
      updated = { ...node, label, description: action.description };
      break;
    }
    case "rename": {
      if (typeof action.label !== "string") return state;
      const label = action.label.trim();
      if (!label || label.length > 80 || label === node.label) return state;
      updated = { ...node, label };
      break;
    }
    case "describe":
      if (
        typeof action.description !== "string" ||
        action.description.length > 240 ||
        action.description === node.description
      )
        return state;
      updated = { ...node, description: action.description };
      break;
    case "move": {
      if (
        typeof action.dx !== "number" ||
        typeof action.dy !== "number" ||
        !Number.isFinite(action.dx) ||
        !Number.isFinite(action.dy)
      )
        return state;
      const x = clamp(node.x + action.dx, 80, 1090);
      const y = clamp(node.y + action.dy, 40, 740);
      if (x === node.x && y === node.y) return state;
      updated = { ...node, x, y };
      break;
    }
    default:
      return state;
  }
  return commit(state, {
    nodes: state.document.nodes.map((item) =>
      item.id === node.id ? updated : item,
    ),
    edges: state.document.edges,
  });
}
