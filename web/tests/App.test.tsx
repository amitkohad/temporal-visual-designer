import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../src/App";

function node(label: string) {
  return screen.getAllByRole("button", { name: `Select ${label}` })[0];
}

describe("business-user sample workspace", () => {
  it("makes the sample and disconnected execution boundary explicit", () => {
    render(<App />);
    expect(
      screen.getByText("Sample project · changes stay in this tab"),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Test workflow" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Export project" }),
    ).toBeDisabled();
    expect(node("Approval outcome")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "Approval outcome",
    );
    expect(screen.getAllByText("Approved").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Rejected").length).toBeGreaterThan(0);
    expect(screen.queryByText("All changes saved")).not.toBeInTheDocument();
  });

  it("navigates sample → Projects → Release → Tasks without losing the sample entry", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Projects" }));
    expect(screen.getByRole("heading", { name: "Projects" })).toBeVisible();
    expect(
      screen.getByRole("navigation", { name: "Dev loop" }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Release" }));
    expect(screen.getByRole("heading", { name: "Release" })).toBeVisible();
    expect(screen.getByText("Connect a project first")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Tasks" }));
    expect(screen.getByRole("heading", { name: "Task portal" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Sample" }));
    expect(
      screen.getByText("Sample project · changes stay in this tab"),
    ).toBeVisible();
  });

  it("selects a step, edits text, and restores the same graph through undo and redo", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(node("Manager approval"));
    const name = screen.getByRole("textbox", { name: "Step name" });
    await user.clear(name);
    await user.type(name, "Finance approval");
    await user.click(screen.getByRole("button", { name: "Apply changes" }));
    expect(node("Finance approval")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Unsaved changes")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(node("Manager approval")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Redo" }));
    expect(node("Finance approval")).toBeVisible();
    expect(node("Approval outcome")).toBeVisible();
    expect(node("Create reference")).toBeVisible();
    expect(node("Set request status")).toBeVisible();
  });

  it("renders markup-like labels literally rather than interpreting HTML", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    const literal = "<img src=x onerror=alert(1)>";
    const name = screen.getByRole("textbox", { name: "Step name" });
    await user.clear(name);
    await user.type(name, literal);
    await user.click(screen.getByRole("button", { name: "Apply changes" }));
    expect(node(literal)).toBeVisible();
    expect(container.querySelector('img[src="x"]')).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
  });

  it("applies name and description as one undoable business action", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(node("Manager approval"));
    await user.clear(screen.getByRole("textbox", { name: "Step name" }));
    await user.type(
      screen.getByRole("textbox", { name: "Step name" }),
      "Finance review",
    );
    await user.clear(screen.getByRole("textbox", { name: "Description" }));
    await user.type(
      screen.getByRole("textbox", { name: "Description" }),
      "Finance reviewers · All approvers",
    );
    await user.click(screen.getByRole("button", { name: "Apply changes" }));
    expect(node("Finance review")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(node("Manager approval")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue(
      "Purchasing managers · Any one",
    );
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Redo" }));
    expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue(
      "Finance reviewers · All approvers",
    );
  });

  it("rejects an empty step name without losing the selected node", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.clear(screen.getByRole("textbox", { name: "Step name" }));
    await user.click(screen.getByRole("button", { name: "Apply changes" }));
    expect(node("Approval outcome")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
  });

  it("finds a palette activity and inserts on a single outgoing path with undo", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(node("Manager approval"));
    await user.click(screen.getByRole("button", { name: "Activities" }));
    const search = screen.getByRole("searchbox", { name: "Search activities" });
    await user.type(search, "timer");
    expect(
      screen.queryByRole("button", { name: "Add approval" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add timer" }));
    expect(screen.getByText("Unsaved changes")).toBeVisible();
    expect(screen.getAllByRole("button", { name: /^Select / }).length).toBe(8);
    expect(node("Approval outcome")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getAllByRole("button", { name: /^Select / }).length).toBe(7);
  });

  it("supports the semantic outline and reopening properties after closing", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Close properties" }));
    expect(
      screen.queryByRole("textbox", { name: "Step name" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Workflow outline" }));
    const outline = screen.getByRole("navigation", {
      name: "Workflow outline",
    });
    await user.click(
      within(outline).getByRole("button", {
        name: "Select Set request status",
      }),
    );
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "Set request status",
    );
  });

  it("warns before unload only when the in-tab draft has edits", async () => {
    const user = userEvent.setup();
    render(<App />);
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    fireEvent.change(screen.getByRole("textbox", { name: "Step name" }), {
      target: { value: "Changed outcome" },
    });
    await user.click(screen.getByRole("button", { name: "Apply changes" }));
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });

  it("retains pending property edits when selecting another step and warns before reload", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.clear(screen.getByRole("textbox", { name: "Step name" }));
    await user.type(
      screen.getByRole("textbox", { name: "Step name" }),
      "Pending decision label",
    );
    await user.click(node("Manager approval"));
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "Manager approval",
    );
    await user.click(node("Approval outcome"));
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "Pending decision label",
    );
    expect(node("Approval outcome")).toBeVisible();
    const pending = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(pending);
    expect(pending.defaultPrevented).toBe(true);
    await user.click(screen.getByRole("button", { name: "Apply changes" }));
    expect(node("Pending decision label")).toBeVisible();
  });

  it("prevents insertion at a decision or terminal step with actionable guidance", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Activities" }));
    expect(screen.getByRole("button", { name: "Add approval" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add timer" })).toBeDisabled();
    expect(
      screen.getByText(/Choose a step with one outgoing path in the outline/),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Close activities" }));
    await user.click(node("End"));
    await user.click(screen.getByRole("button", { name: "Activities" }));
    expect(screen.getByRole("button", { name: "Add timer" })).toBeDisabled();
  });
});
