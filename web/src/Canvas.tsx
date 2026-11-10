import { useCallback, useEffect, useId, useRef, useState } from "react";
import type {
  DesignerDocument,
  DesignerEdge,
  DesignerNode,
  NodeKind,
} from "./model";
import "./canvas.css";

interface CanvasProps {
  document: DesignerDocument;
  selectedId: string;
  onSelect: (id: string) => void;
  onMove: (id: string, dx: number, dy: number) => void;
}

const WIDTH = 1170;
const HEIGHT = 824;
const ASSETS: Partial<Record<NodeKind, string>> = {
  start: new URL("./assets/start.svg", import.meta.url).href,
  approval: new URL("./assets/approval.svg", import.meta.url).href,
  decision: new URL("./assets/decision.svg", import.meta.url).href,
  reference: new URL("./assets/reference.svg", import.meta.url).href,
  update: new URL("./assets/update.svg", import.meta.url).href,
  subworkflow: new URL("./assets/subworkflow.svg", import.meta.url).href,
};

function radius(node: DesignerNode) {
  return node.kind === "decision"
    ? 44
    : node.kind === "start" || node.kind === "end"
      ? 28
      : 32;
}

// Labels sit below their nodes. Vertical lines pause through that label band,
// rather than crossing text; horizontal decision routes remain explicit.
function connection(source: DesignerNode, target: DesignerNode): string {
  const from = radius(source);
  const to = radius(target);
  const direction = target.x >= source.x ? 1 : -1;
  if (source.kind === "decision" && Math.abs(source.y - target.y) < 100) {
    return `M ${source.x + from * direction} ${source.y} L ${target.x - to * direction} ${target.y}`;
  }
  if (target.kind === "end" && Math.abs(source.x - target.x) > 100) {
    if (source.y > target.y - 250) {
      const bendX = target.x;
      const bendY = source.y;
      return `M ${source.x + from * direction} ${source.y} H ${bendX - direction * 44} Q ${bendX} ${bendY} ${bendX} ${bendY + 44} V ${target.y - to}`;
    }
    const below = source.y + 90;
    const bendY = target.y - 84;
    return `M ${source.x} ${source.y + from} V ${source.y + 40} M ${source.x} ${below} V ${bendY - 32} Q ${source.x} ${bendY} ${source.x + direction * 32} ${bendY} H ${target.x - direction * 44} Q ${target.x} ${bendY} ${target.x} ${bendY + 44} V ${target.y - to}`;
  }
  const labelBottom = source.y + (source.kind === "start" ? 61 : 90);
  if (target.y - to > labelBottom + 5 && Math.abs(source.x - target.x) < 16) {
    return `M ${source.x} ${source.y + from} V ${source.y + from + 7} M ${source.x} ${labelBottom} V ${target.y - to}`;
  }
  const side = source.x + direction * (from + 100);
  const before = target.y - to - 24;
  return `M ${source.x + direction * from} ${source.y} H ${side} V ${before} H ${target.x} V ${target.y - to}`;
}

function TimerIcon() {
  return (
    <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">
      <circle
        cx="15"
        cy="17"
        r="10"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      />
      <path
        d="M11 3h8M15 7V3m0 7v8l5 3"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function WorkflowCanvas({
  document,
  selectedId,
  onSelect,
  onMove,
}: CanvasProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const markerId = `workflow-arrow-${useId().replace(/:/g, "")}`;
  const fit = useCallback(() => {
    const area = viewport.current;
    if (!area || area.clientWidth < 1 || area.clientHeight < 1) return;
    setZoom(
      Math.max(
        0.25,
        Math.min(1, area.clientWidth / WIDTH, area.clientHeight / HEIGHT),
      ),
    );
    area.scrollTo?.({ left: 0, top: 0 });
  }, []);

  useEffect(() => {
    fit();
    const element = viewport.current;
    if (!element) return;
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(fit);
      observer.observe(element);
      return () => observer.disconnect();
    }
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [fit]);

  const edges = document.edges.flatMap((edge: DesignerEdge) => {
    const source = document.nodes.find((node) => node.id === edge.source);
    const target = document.nodes.find((node) => node.id === edge.target);
    return source && target ? [{ edge, source, target }] : [];
  });

  return (
    <section className="workflow-canvas" aria-label="Workflow canvas">
      <p id="canvas-keyboard-help" className="canvas-sr-only">
        Select a step to edit its properties. When a step is focused, use arrow
        keys to move it, or Shift and an arrow key for a larger move. Use the
        workflow outline for a text alternative.
      </p>
      <div className="canvas-viewport" ref={viewport}>
        <div
          className="canvas-scaled-space"
          style={{ width: WIDTH * zoom, height: HEIGHT * zoom }}
        >
          <div className="canvas-world" style={{ transform: `scale(${zoom})` }}>
            <div className="canvas-caption">
              <span>PURCHASE REQUISITION</span>
              <span className="canvas-step-count">
                {document.nodes.length} steps · Local sample
              </span>
            </div>
            <svg
              className="canvas-connections"
              width={WIDTH}
              height={HEIGHT}
              viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
              aria-label="Workflow connections"
              role="img"
            >
              <title>
                Directional workflow connections, including Approved and
                Rejected decision paths
              </title>
              <defs>
                <marker
                  id={markerId}
                  markerWidth="8"
                  markerHeight="8"
                  refX="7"
                  refY="4"
                  orient="auto"
                  markerUnits="userSpaceOnUse"
                >
                  <path
                    d="M1 1L7 4L1 7"
                    fill="none"
                    stroke="#a9bfc7"
                    strokeWidth="1.8"
                  />
                </marker>
              </defs>
              {edges.map(({ edge, source, target }) => (
                <g key={edge.id} data-edge-id={edge.id}>
                  <path
                    d={connection(source, target)}
                    fill="none"
                    stroke="#a9bfc7"
                    strokeWidth="2"
                    markerEnd={`url(#${markerId})`}
                  />
                  <title>
                    {source.label} → {target.label}
                    {edge.label ? `: ${edge.label}` : ""}
                  </title>
                </g>
              ))}
            </svg>
            {edges
              .filter(({ edge }) => edge.label)
              .map(({ edge, source, target }) => (
                <span
                  key={edge.id}
                  className={`canvas-branch-label ${edge.label === "Rejected" ? "canvas-branch-rejected" : ""}`}
                  style={{
                    left: (source.x + target.x) / 2,
                    top: Math.min(source.y, target.y) - 38,
                  }}
                >
                  {edge.label}
                </span>
              ))}
            {document.nodes.map((node) => {
              const event = node.kind === "start" || node.kind === "end";
              const size = radius(node) * 2;
              return (
                <div className="canvas-node-group" key={node.id}>
                  <button
                    type="button"
                    className={`canvas-node canvas-node-${node.kind} ${selectedId === node.id ? "canvas-node-selected" : ""}`}
                    style={{
                      left: node.x,
                      top: node.y,
                      width: size,
                      height: size,
                    }}
                    aria-label={`Select ${node.label}`}
                    aria-pressed={selectedId === node.id}
                    aria-describedby="canvas-keyboard-help"
                    title={`${node.label} · ${node.kind}`}
                    onClick={() => onSelect(node.id)}
                    onKeyDown={(event) => {
                      const amount = event.shiftKey ? 40 : 20;
                      const movement: Record<string, [number, number]> = {
                        ArrowUp: [0, -amount],
                        ArrowDown: [0, amount],
                        ArrowLeft: [-amount, 0],
                        ArrowRight: [amount, 0],
                      };
                      if (
                        movement[event.key] &&
                        !event.ctrlKey &&
                        !event.metaKey &&
                        !event.altKey
                      ) {
                        event.preventDefault();
                        onSelect(node.id);
                        const [dx, dy] = movement[event.key];
                        onMove(node.id, dx, dy);
                      }
                    }}
                  >
                    {ASSETS[node.kind] ? (
                      <img
                        src={ASSETS[node.kind]}
                        alt=""
                        width={
                          node.kind === "decision"
                            ? 88
                            : node.kind === "start"
                              ? 23
                              : 34
                        }
                        height={
                          node.kind === "decision"
                            ? 88
                            : node.kind === "start"
                              ? 28
                              : 34
                        }
                        draggable="false"
                      />
                    ) : node.kind === "end" ? (
                      <span className="canvas-end-square" />
                    ) : (
                      <TimerIcon />
                    )}
                  </button>
                  <div
                    className={`canvas-node-label ${event ? "canvas-event-label" : ""}`}
                    style={{
                      left: node.x - 108,
                      top:
                        node.y +
                        (node.kind === "decision" ? 58 : event ? 38 : 44),
                    }}
                  >
                    <span title={node.label}>{node.label}</span>
                    {!event && node.description && (
                      <small title={node.description}>{node.description}</small>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <div className="canvas-controls" aria-label="Canvas view controls">
        <button
          type="button"
          aria-label="Zoom out"
          disabled={zoom <= 0.25}
          onClick={() => setZoom((value) => Math.max(0.25, value - 0.1))}
        >
          −
        </button>
        <output aria-label="Canvas zoom">{Math.round(zoom * 100)}%</output>
        <button
          type="button"
          aria-label="Zoom in"
          disabled={zoom >= 1.5}
          onClick={() => setZoom((value) => Math.min(1.5, value + 0.1))}
        >
          +
        </button>
        <span className="canvas-control-divider" />
        <button type="button" onClick={fit} aria-label="Fit workflow">
          Fit <span aria-hidden="true">⌗</span>
        </button>
      </div>
      <button
        type="button"
        className="canvas-minimap"
        aria-label="Fit workflow using minimap"
        onClick={fit}
        title="Fit workflow"
      >
        <svg
          width="116"
          height="82"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          aria-hidden="true"
        >
          {edges.map(({ edge, source, target }) => (
            <path
              key={edge.id}
              d={`M${source.x} ${source.y} L${target.x} ${target.y}`}
              fill="none"
              stroke="#a9bfc7"
              strokeWidth="12"
            />
          ))}
          {document.nodes.map((node) => (
            <rect
              key={node.id}
              x={node.x - 30}
              y={node.y - 30}
              width="60"
              height="60"
              rx="8"
              fill={selectedId === node.id ? "#087f89" : "#55b4bd"}
            />
          ))}
        </svg>
      </button>
    </section>
  );
}
