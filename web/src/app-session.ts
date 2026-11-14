/** Thin App-owned helpers for MVP Dev-loop composition. */
import type { DesignerPublishGate } from "./Projects";
import type { ProjectConnection } from "./project-api";

export type AppView = "sample" | "projects" | "release" | "tasks";

export const emptyPublishGate: DesignerPublishGate = {
  connection: null,
  projectId: null,
  headRevision: null,
  workflowId: null,
  validationIssues: [],
  publishBlocked: false,
};

export type ReleaseTargets = {
  connection: ProjectConnection;
  projectId: string;
  headRevision: number;
  workflowId: string;
};

/** Returns mount props for ReleaseControls when the Designer gate is complete. */
export function releaseTargetsFromGate(
  gate: DesignerPublishGate,
): ReleaseTargets | null {
  if (
    !gate.connection ||
    !gate.projectId ||
    gate.headRevision == null ||
    gate.headRevision < 1 ||
    !gate.workflowId
  ) {
    return null;
  }
  return {
    connection: gate.connection,
    projectId: gate.projectId,
    headRevision: gate.headRevision,
    workflowId: gate.workflowId,
  };
}
