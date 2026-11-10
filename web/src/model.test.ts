import { describe, expect, it } from "vitest";
import {
  editorReducer,
  initialState,
  type EditorAction,
  type EditorState,
} from "./model";

function freeze(state: EditorState): EditorState {
  for (const node of state.document.nodes) Object.freeze(node);
  for (const edge of state.document.edges) Object.freeze(edge);
  Object.freeze(state.document.nodes);
  Object.freeze(state.document.edges);
  Object.freeze(state.document);
  Object.freeze(state.past);
  Object.freeze(state.future);
  return Object.freeze(state);
}

describe("local designer document", () => {
  it("returns independent fresh samples with explicit alternative paths and convergence", () => {
    const first = initialState();
    const second = initialState();
    expect(first).toEqual(second);
    expect(first).not.toBe(second);
    expect(first.document.nodes).toHaveLength(7);
    expect(first.selectedId).toBe("decision");
    expect(
      first.document.edges.filter((edge) => edge.source === "decision"),
    ).toEqual([
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
    ]);
    expect(
      first.document.edges.filter((edge) => edge.target === "end"),
    ).toHaveLength(2);
    first.document.nodes[0].label = "Changed";
    first.document.edges[0].target = "Changed";
    expect(second.document.nodes[0].label).toBe("Start");
    expect(second.document.edges[0].target).toBe("approval");
  });

  it("changes selection without creating or discarding edit history", () => {
    const edited = editorReducer(initialState(), {
      type: "rename",
      id: "approval",
      label: "Review request",
    });
    const undone = editorReducer(edited, { type: "undo" });
    const selected = editorReducer(freeze(undone), {
      type: "select",
      id: "approval",
    });
    expect(selected.selectedId).toBe("approval");
    expect(selected.document).toBe(undone.document);
    expect(selected.past).toBe(undone.past);
    expect(selected.future).toBe(undone.future);
    expect(editorReducer(selected, { type: "select", id: "approval" })).toBe(
      selected,
    );
  });

  it("renames and describes immutably, retaining graph semantics and bounded plain text", () => {
    const original = freeze(initialState());
    const edited = editorReducer(original, {
      type: "rename",
      id: "decision",
      label: "  <img src=x onerror=alert(1)>  ",
    });
    expect(
      edited.document.nodes.find((node) => node.id === "decision")?.label,
    ).toBe("<img src=x onerror=alert(1)>");
    expect(edited.document.edges).toBe(original.document.edges);
    expect(
      original.document.nodes.find((node) => node.id === "decision")?.label,
    ).toBe("Approval outcome");
    const described = editorReducer(freeze(edited), {
      type: "describe",
      id: "decision",
      description: "🙂 Business text\nSecond line",
    });
    expect(
      described.document.nodes.find((node) => node.id === "decision")
        ?.description,
    ).toBe("🙂 Business text\nSecond line");
    expect(described.past).toEqual([original.document, edited.document]);
  });

  it("accepts text limits inclusively", () => {
    const source = initialState();
    const renamed = editorReducer(source, {
      type: "rename",
      id: "decision",
      label: "x".repeat(80),
    });
    const described = editorReducer(source, {
      type: "describe",
      id: "decision",
      description: "x".repeat(240),
    });
    expect(renamed).not.toBe(source);
    expect(described).not.toBe(source);
  });

  it("applies name and description as one atomic undoable edit", () => {
    const state = freeze(initialState());
    const edited = editorReducer(state, {
      type: "edit",
      id: "approval",
      label: "  Procurement review  ",
      description: "All purchasing managers",
    });
    expect(
      edited.document.nodes.find((node) => node.id === "approval"),
    ).toEqual({
      ...state.document.nodes.find((node) => node.id === "approval"),
      label: "Procurement review",
      description: "All purchasing managers",
    });
    expect(edited.past).toEqual([state.document]);
    expect(edited.document.edges).toBe(state.document.edges);
    const undone = editorReducer(freeze(edited), { type: "undo" });
    expect(undone.document).toBe(state.document);
    const redone = editorReducer(freeze(undone), { type: "redo" });
    expect(redone.document).toBe(edited.document);
    expect(
      editorReducer(edited, {
        type: "edit",
        id: "approval",
        label: "Procurement review",
        description: "All purchasing managers",
      }),
    ).toBe(edited);
  });

  it.each([
    { label: " ", description: "Valid new description" },
    { label: "x".repeat(81), description: "Valid new description" },
    { label: "Valid new name", description: "x".repeat(241) },
    { label: null, description: "Valid new description" },
    { label: "Valid new name", description: 3 },
  ])(
    "rejects an invalid atomic edit without partially applying valid fields %#",
    (changes) => {
      const state = freeze(initialState());
      expect(
        editorReducer(state, {
          type: "edit",
          id: "approval",
          ...changes,
        } as EditorAction),
      ).toBe(state);
    },
  );

  it("moves a node within bounds without changing its identity or edges", () => {
    const original = freeze(initialState());
    const moved = editorReducer(original, {
      type: "move",
      id: "approval",
      dx: -9999,
      dy: 9999,
    });
    expect(moved.document.nodes.find((node) => node.id === "approval")).toEqual(
      {
        ...original.document.nodes.find((node) => node.id === "approval"),
        x: 80,
        y: 740,
      },
    );
    expect(moved.document.edges).toBe(original.document.edges);
    const boundary = editorReducer(moved, {
      type: "move",
      id: "approval",
      dx: 9999,
      dy: -9999,
    });
    expect(
      boundary.document.nodes.find((node) => node.id === "approval")?.x,
    ).toBe(1090);
    expect(
      boundary.document.nodes.find((node) => node.id === "approval")?.y,
    ).toBe(40);
    expect(
      editorReducer(boundary, {
        type: "move",
        id: "approval",
        dx: 10,
        dy: -10,
      }),
    ).toBe(boundary);
  });

  it.each(["approval", "timer"] as const)(
    "inserts a %s on one path, preserving all other paths",
    (kind) => {
      const original = freeze(initialState());
      const inserted = editorReducer(original, {
        type: "insert",
        kind,
        afterId: "reference",
      });
      const id = `${kind}-1`;
      expect(inserted.selectedId).toBe(id);
      expect(inserted.document.nodes).toHaveLength(8);
      expect(
        inserted.document.nodes.find((node) => node.id === id),
      ).toMatchObject({ kind, x: 408, y: 459 });
      expect(
        inserted.document.edges.find(
          (edge) => edge.id === "reference-subworkflow",
        ),
      ).toEqual({
        id: "reference-subworkflow",
        source: "reference",
        target: id,
      });
      expect(
        inserted.document.edges.find((edge) => edge.id === `${id}-next`),
      ).toEqual({
        id: `${id}-next`,
        source: id,
        target: "subworkflow",
      });
      for (const edge of original.document.edges.filter(
        (edge) => edge.source !== "reference",
      )) {
        expect(
          inserted.document.edges.find((item) => item.id === edge.id),
        ).toEqual(edge);
      }
      expect(inserted.past).toEqual([original.document]);
      expect(original.document.nodes).toHaveLength(7);
    },
  );

  it("preserves a labeled single outgoing edge when inserting", () => {
    const state = initialState();
    state.document.edges = state.document.edges.map((edge) =>
      edge.source === "start" ? { ...edge, label: "Request submitted" } : edge,
    );
    const edited = editorReducer(freeze(state), {
      type: "insert",
      kind: "timer",
      afterId: "start",
    });
    expect(
      edited.document.edges.find((edge) => edge.id === "start-approval"),
    ).toEqual({
      id: "start-approval",
      source: "start",
      target: "timer-1",
      label: "Request submitted",
    });
    expect(
      edited.document.edges.find((edge) => edge.source === "timer-1")?.label,
    ).toBeUndefined();
  });

  it("uses unique insertion identities and stops at the node limit", () => {
    let state = initialState();
    for (let i = 1; i <= 43; i += 1) {
      state = editorReducer(state, {
        type: "insert",
        kind: "timer",
        afterId: "start",
      });
      expect(state.selectedId).toBe(`timer-${i}`);
    }
    expect(state.document.nodes).toHaveLength(50);
    expect(new Set(state.document.nodes.map((node) => node.id)).size).toBe(50);
    expect(new Set(state.document.edges.map((edge) => edge.id)).size).toBe(
      state.document.edges.length,
    );
    expect(
      editorReducer(state, {
        type: "insert",
        kind: "approval",
        afterId: "start",
      }),
    ).toBe(state);
  });

  it("undoes and redoes exact document snapshots, repairing selection after insertion disappears", () => {
    const initial = freeze(initialState());
    const inserted = editorReducer(initial, {
      type: "insert",
      kind: "approval",
      afterId: "start",
    });
    const renamed = editorReducer(freeze(inserted), {
      type: "rename",
      id: "approval-1",
      label: "New review",
    });
    const firstUndo = editorReducer(freeze(renamed), { type: "undo" });
    const secondUndo = editorReducer(freeze(firstUndo), { type: "undo" });
    expect(firstUndo.document).toBe(inserted.document);
    expect(secondUndo.document).toBe(initial.document);
    expect(secondUndo.selectedId).toBe("decision");
    const firstRedo = editorReducer(freeze(secondUndo), { type: "redo" });
    const secondRedo = editorReducer(freeze(firstRedo), { type: "redo" });
    expect(firstRedo.document).toBe(inserted.document);
    expect(secondRedo.document).toBe(renamed.document);
    expect(secondRedo.future).toEqual([]);
    expect(editorReducer(secondRedo, { type: "redo" })).toBe(secondRedo);
    expect(editorReducer(initial, { type: "undo" })).toBe(initial);
  });

  it("clears the redo branch only after an effective edit and bounds history", () => {
    let state = initialState();
    for (let i = 0; i < 60; i += 1)
      state = editorReducer(state, {
        type: "rename",
        id: "start",
        label: `Start ${i}`,
      });
    expect(state.past).toHaveLength(50);
    state = editorReducer(state, { type: "undo" });
    expect(state.future).toHaveLength(1);
    const current = state.document.nodes.find(
      (node) => node.id === "start",
    )!.label;
    expect(
      editorReducer(state, { type: "rename", id: "start", label: current }),
    ).toBe(state);
    const edited = editorReducer(state, {
      type: "describe",
      id: "start",
      description: "New branch",
    });
    expect(edited.future).toEqual([]);
    for (let i = 0; i < 100; i += 1)
      state = editorReducer(state, { type: "undo" });
    expect(state.past).toHaveLength(0);
    expect(state.future).toHaveLength(50);
  });

  it.each([
    null,
    undefined,
    true,
    1,
    "undo",
    [],
    {},
    { type: "unknown" },
    { type: "select", id: "missing" },
    { type: "rename", id: "missing", label: "a" },
    { type: "rename", id: "start", label: "" },
    { type: "rename", id: "start", label: "   " },
    { type: "rename", id: "start", label: "x".repeat(81) },
    { type: "rename", id: "start", label: 4 },
    { type: "describe", id: "start", description: "x".repeat(241) },
    { type: "describe", id: "start", description: null },
    { type: "move", id: "start", dx: NaN, dy: 1 },
    { type: "move", id: "start", dx: 1, dy: Infinity },
    { type: "move", id: "start", dx: "1", dy: 0 },
    { type: "move", id: "start", dx: 0, dy: 0 },
    { type: "insert", kind: "decision", afterId: "start" },
    { type: "insert", kind: "timer", afterId: "decision" },
    { type: "insert", kind: "timer", afterId: "end" },
    { type: "insert", kind: "timer", afterId: "missing" },
    { type: "insert", kind: "timer" },
  ])(
    "rejects malformed or unsupported action %# without editing or history",
    (action) => {
      const state = freeze(initialState());
      expect(editorReducer(state, action as EditorAction)).toBe(state);
    },
  );
});
