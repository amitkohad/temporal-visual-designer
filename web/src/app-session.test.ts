import { describe, expect, it } from "vitest";
import {
  emptyPublishGate,
  releaseTargetsFromGate,
} from "./app-session";
import type { DesignerPublishGate } from "./Projects";

const connection = {
  organizationId: "local-demo",
  environmentId: "dev",
  token: "test-token",
};

describe("releaseTargetsFromGate", () => {
  it("returns null until connection, project, revision, and workflow are set", () => {
    expect(releaseTargetsFromGate(emptyPublishGate)).toBeNull();
    const partial: DesignerPublishGate = {
      ...emptyPublishGate,
      connection,
      projectId: "demo",
      headRevision: 1,
    };
    expect(releaseTargetsFromGate(partial)).toBeNull();
  });

  it("returns mount targets when the Designer gate is complete", () => {
    const gate: DesignerPublishGate = {
      connection,
      projectId: "demo",
      headRevision: 3,
      workflowId: "main",
      validationIssues: [],
      publishBlocked: true,
    };
    expect(releaseTargetsFromGate(gate)).toEqual({
      connection,
      projectId: "demo",
      headRevision: 3,
      workflowId: "main",
    });
  });
});
