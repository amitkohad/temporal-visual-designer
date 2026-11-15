import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import FormRenderer, { draftValuesFromForm } from "../src/FormRenderer";
import { parseFormDefinition, previewFormDefinition } from "../src/form-api";

const definition = parseFormDefinition({
  form_id: "demo",
  schema_version: "0.1",
  data_schema: {
    type: "object",
    properties: {
      name: { type: "string", title: "Full name" },
      amount: { type: "number", title: "Amount" },
      dept: {
        type: "string",
        title: "Department",
        enum: ["Engineering", "Finance"],
      },
      confirmed: { type: "boolean", title: "I confirm", const: true },
      secret: { type: "string", title: "Secret", writeOnly: true },
      locked: { type: "string", title: "Locked", readOnly: true },
    },
    required: ["name", "confirmed"],
    additionalProperties: false,
  },
  layout: {
    field_order: ["name", "amount", "dept", "confirmed", "secret", "locked"],
  },
  visibility_rules: [],
  validation_rules: [],
  field_permissions: {},
  attachment_policy: {},
});

describe("FormRenderer", () => {
  it("renders accessible labeled controls including enum and write-only fields", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const form = previewFormDefinition(definition, { locked: "pinned" });
    const values = draftValuesFromForm(form);
    render(
      <FormRenderer
        form={form}
        values={values}
        onChange={onChange}
        mode="task"
        onSubmit={() => undefined}
      />,
    );
    expect(screen.getByLabelText(/Full name \(required\)/i)).toBeVisible();
    expect(screen.getByLabelText(/Amount/i)).toHaveAttribute("type", "number");
    expect(screen.getByLabelText(/Department/i).tagName).toBe("SELECT");
    expect(screen.getByLabelText(/Secret/i)).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.getByText("Locked")).toBeVisible();
    expect(screen.getByText("pinned")).toBeVisible();
    await user.type(screen.getByLabelText(/Full name \(required\)/i), "Ada");
    expect(onChange).toHaveBeenCalled();
    await user.selectOptions(screen.getByLabelText(/Department/i), "Finance");
    expect(onChange).toHaveBeenCalledWith("dept", "Finance");
  });

  it("focuses the first field error and keeps labels as plain text", async () => {
    const form = previewFormDefinition(definition);
    const { container } = render(
      <FormRenderer
        form={form}
        values={draftValuesFromForm(form)}
        onChange={() => undefined}
        errors={[
          { field_id: "name", message: "Name is required" },
          { field_id: "confirmed", message: "Confirmation required" },
        ]}
        mode="start"
        heading="Start form preview"
        showSubmit={false}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Start form preview" }),
    ).toBeVisible();
    const name = screen.getByLabelText(/Full name \(required\)/i);
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveFocus();
    expect(screen.getByText("Name is required")).toBeVisible();
    expect(container.querySelector("script")).toBeNull();
  });
});
