/**
 * Canvas overlay adapter for the Dev debugger panel.
 * Owns no Designer-core / Canvas paths — parents pass node id sets only.
 */
import type { DebugSnapshot } from "./debugger-api";

export type DebuggerCanvasOverlayProps = {
  snapshot: DebugSnapshot | null;
  breakpoints: string[];
  selectedNodeId?: string;
  onSelectNode?: (nodeId: string) => void;
};

function unique(ids: string[]): string[] {
  return [...new Set(ids.filter(Boolean))];
}

export default function DebuggerCanvasOverlay({
  snapshot,
  breakpoints,
  selectedNodeId = "",
  onSelectNode,
}: DebuggerCanvasOverlayProps) {
  const current = snapshot?.node_id ?? "";
  const completed = snapshot?.completed_nodes ?? [];
  const nodes = unique([
    current,
    ...breakpoints,
    ...completed,
    ...(selectedNodeId ? [selectedNodeId] : []),
  ]);

  if (!snapshot && breakpoints.length === 0) {
    return (
      <div className="nf-debugger-overlay" aria-label="Debugger canvas overlay">
        <p className="nf-debugger-muted">
          Start or reconnect a session to see the current node, breakpoints, and
          completed steps. This overlay does not edit the Designer canvas.
        </p>
      </div>
    );
  }

  return (
    <div className="nf-debugger-overlay" aria-label="Debugger canvas overlay">
      <div className="nf-debugger-overlay-legend" aria-hidden="true">
        <span data-kind="current">Current</span>
        <span data-kind="breakpoint">Breakpoint</span>
        <span data-kind="completed">Completed</span>
      </div>
      {snapshot && (
        <p className="nf-debugger-overlay-status" role="status">
          Runtime <strong>{snapshot.state}</strong> at{" "}
          <code>{snapshot.node_id}</code> · revision {snapshot.revision}
        </p>
      )}
      <ul className="nf-debugger-overlay-nodes">
        {nodes.map((nodeId) => {
          const kinds: string[] = [];
          if (nodeId === current) kinds.push("current");
          if (breakpoints.includes(nodeId)) kinds.push("breakpoint");
          if (completed.includes(nodeId)) kinds.push("completed");
          const selected = nodeId === selectedNodeId;
          return (
            <li key={nodeId}>
              <button
                type="button"
                className="nf-debugger-overlay-node"
                data-kinds={kinds.join(" ")}
                aria-pressed={selected}
                onClick={() => onSelectNode?.(nodeId)}
              >
                <code>{nodeId}</code>
                <span className="nf-debugger-overlay-tags">
                  {kinds.join(" · ") || "node"}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
