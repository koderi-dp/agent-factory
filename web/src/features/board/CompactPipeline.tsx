import { compactLayout } from "@/features/job/pipeline/layout";
import { StatusDot } from "@/ui/status";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/ui/primitives/tooltip";
import type { Execution, Step } from "./model";

const symbols = { pending: "·", running: "↻", done: "✓", changes: "!", failed: "×" };

export function CompactPipeline({ steps, executions }: { steps: Step[]; executions: Execution[] }) {
  return (
    <TooltipProvider>
      <ol aria-label="Pipeline progress" className="flex gap-1 overflow-x-auto py-1">
        {compactLayout(steps, executions).map((step) => (
          <li key={step.id} className="shrink-0">
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  aria-label={`${step.label}: ${step.state}`}
                  className="flex items-center gap-1 rounded-sm p-1 font-mono text-[10px] text-muted-foreground"
                >
                  <StatusDot
                    state={step.state}
                    className={step.state === "running" ? "status-pulse" : undefined}
                  />
                  <span aria-hidden>{symbols[step.state]}</span>
                </button>
              </TooltipTrigger>
              <TooltipContent>
                {step.label} · {step.state}
              </TooltipContent>
            </Tooltip>
          </li>
        ))}
      </ol>
    </TooltipProvider>
  );
}
