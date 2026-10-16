import type { Box, LayoutEdge } from "./layout";
export function PipelineEdge({
  edge,
  from,
  to,
  running,
  marker,
}: {
  edge: LayoutEdge;
  from: Box;
  to: Box;
  running: boolean;
  marker: string;
}) {
  const loop = edge.kind === "rework";
  const x1 = loop ? from.x + from.w / 2 : from.x + from.w,
    y1 = loop ? from.y + from.h : from.y + from.h / 2;
  const x2 = loop ? to.x + to.w / 2 : to.x,
    y2 = loop ? to.y + to.h : to.y + to.h / 2;
  const mid = (x1 + x2) / 2;
  const path = loop
    ? `M${x1},${y1} V${edge.lane} H${x2} V${y2}`
    : `M${x1},${y1} H${mid} V${y2} H${x2}`;
  return (
    <g
      className={`pipeline-edge ${loop ? "pipeline-rework" : running ? "pipeline-flow-running" : ""}`}
    >
      <path d={path} fill="none" markerEnd={`url(#${marker})`} />
      {loop && (
        <text x={mid} y={edge.lane! - 6} textAnchor="middle">
          {edge.label}
        </text>
      )}
    </g>
  );
}
