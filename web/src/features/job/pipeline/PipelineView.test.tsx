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

it("keeps duplicate step and execution IDs editable with their own profiles and columns", () => {
  const duplicate: Step[] = [
    {
      id: "build",
      executions: [
        {
          id: "same",
          uses: "builder",
          provider: "codex",
          profiles: { codex: { model: "builder-model", effort: "low" } },
        },
      ],
    },
    {
      id: "build",
      type: "review",
      onChanges: "build",
      executions: [
        {
          id: "code",
          uses: "reviewer",
          provider: "codex",
          profiles: { codex: { model: "review-model", effort: "high" } },
        },
        { id: "code", uses: "security" },
      ],
    },
  ];
  const geometry = layout(duplicate);
  expect(geometry.nodes.map((n) => [n.stepIndex, n.execIndex])).toEqual([
    [0, 0],
    [1, 0],
    [1, 1],
  ]);
  expect(geometry.edges[0]).toMatchObject({ fromIndex: 0, toIndex: 1 });
  const html = renderToStaticMarkup(<PipelineView steps={duplicate} invalidSteps={["build"]} />);
  expect(html).toContain("builder-model");
  expect(html).toContain("review-model");
  expect(html).toContain("security");
  expect(html.match(/pipeline-invalid/g)).toHaveLength(3);
});
