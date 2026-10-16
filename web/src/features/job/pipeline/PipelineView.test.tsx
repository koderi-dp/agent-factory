import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PipelineView } from "./PipelineView";
import { layout } from "./layout";
import { executionSchema } from "@/lib/api";
import type { Step } from "@/features/board/model";
const steps: Step[] = [
  { id: "build", type: "task", executions: [{ id: "build", uses: "builder", provider: "codex" }] },
  {
    id: "review",
    type: "review",
    onChanges: "build",
    executions: [{ id: "code" }, { id: "security" }],
  },
];
it("keeps current-round evidence and labels the rewind without mutating input", () => {
  const row = executionSchema.parse({
    id: 2,
    job_id: "job",
    step_id: "build",
    exec_id: "build",
    round: 2,
    type: "task",
    uses: "builder",
    provider: "codex",
    model: "sol",
    effort: "medium",
    state: "running",
    result: null,
    head_before: null,
    head_after: null,
    report_path: null,
    created_at: "2026-10-16T12:00:00Z",
  });
  const rows = [
    row,
    {
      ...row,
      id: 3,
      step_id: "review",
      exec_id: "code",
      round: 1,
      state: "done" as const,
      result: "PASS",
    },
  ];
  const before = JSON.stringify([steps, rows]);
  const g = layout(steps, rows);
  expect(g.nodes[0].execution?.id).toBe(2);
  expect(g.nodes[1].execution).toBeUndefined();
  expect(g.groups[0].label).toBe("review · 0/2 done");
  expect(g.edges.find((e) => e.kind === "rework")?.label).toBe("CHANGES · round 2");
  expect(JSON.stringify([steps, rows])).toBe(before);
});
it("renders accessible nodes, SVG edges and validation highlighting", () => {
  const html = renderToStaticMarkup(<PipelineView steps={steps} invalidSteps={["review"]} />);
  expect(html).toContain('role="list"');
  expect(html.match(/role="listitem"/g)).toHaveLength(3);
  expect(html).toContain('aria-label="build: builder, pending"');
  expect(html).toContain("REVIEW");
  expect(html).toContain("pipeline-rework");
  expect(html).toContain("pipeline-invalid");
  expect(html).toContain("<svg");
});
