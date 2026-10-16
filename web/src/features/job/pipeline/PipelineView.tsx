import { useId, useMemo, useRef } from "react";
import { layout, LOOP_GAP } from "./layout";
import { PipelineNode } from "./PipelineNode";
import { PipelineEdge } from "./PipelineEdge";
import { GroupFrame } from "./GroupFrame";
import type { Step, Execution, Attempt } from "@/features/board/model";
import type { ActivityEvent } from "@/lib/api";
import { latestAttempt } from "@/features/board/model";

export function PipelineView({
  steps,
  executions = [],
  attempts = [],
  activity = [],
  paused,
  invalidSteps = [],
  onOpen,
}: {
  steps: Step[];
  executions?: Execution[];
  attempts?: Attempt[];
  activity?: ActivityEvent[];
  paused?: string | null;
  invalidSteps?: string[];
  onOpen?: (id: string) => void;
}) {
  const geometry = useMemo(() => layout(steps, executions), [steps, executions]);
  const ref = useRef<HTMLDivElement>(null),
    marker = useId().replaceAll(":", "");
  const boxes = steps.map(
    (_, i) =>
      geometry.groups.find((g) => g.stepIndex === i) ??
      geometry.nodes.find((n) => n.stepIndex === i)!,
  );
  const loops = geometry.edges.filter((e) => e.kind === "rework").length;
  return (
    <div className="overflow-x-auto pb-3">
      <div
        ref={ref}
        role="list"
        aria-label="Pipeline executions"
        className="relative"
        style={{ width: geometry.width, height: geometry.height + loops * LOOP_GAP + 20 }}
        onKeyDown={(event) => {
          if (!["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"].includes(event.key)) return;
          const buttons = [...ref.current!.querySelectorAll<HTMLButtonElement>("button")];
          const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
          if (index < 0) return;
          event.preventDefault();
          buttons[
            (index +
              (event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1) +
              buttons.length) %
              buttons.length
          ]?.focus();
        }}
      >
        {geometry.groups.map((group) => (
          <GroupFrame key={group.stepIndex} group={group} />
        ))}
        <svg
          aria-hidden
          className="pointer-events-none absolute inset-0 h-full w-full overflow-visible"
        >
          <defs>
            <marker
              id={marker}
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-muted-foreground)" />
            </marker>
          </defs>
          {geometry.edges.map((edge) => (
            <PipelineEdge
              key={`${edge.kind}:${edge.fromIndex}:${edge.toIndex}`}
              edge={edge}
              from={boxes[edge.fromIndex]}
              to={boxes[edge.toIndex]}
              running={geometry.nodes.some(
                (n) => n.stepIndex === edge.toIndex && n.execution?.state === "running",
              )}
              marker={marker}
            />
          ))}
        </svg>
        {geometry.nodes.map((node) => (
          <div role="listitem" key={`${node.stepIndex}:${node.execIndex}`}>
            <PipelineNode
              node={node}
              step={steps[node.stepIndex]}
              attempts={attempts}
              activity={
                activity.find(
                  (a) =>
                    a.attempt_id ===
                    (node.execution && latestAttempt(node.execution, attempts))?.id,
                )?.text
              }
              paused={
                node.execution && ["pending", "running", "stopped"].includes(node.execution.state)
                  ? paused
                  : undefined
              }
              invalid={invalidSteps.includes(node.step)}
              onOpen={onOpen}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
