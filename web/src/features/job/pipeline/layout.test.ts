import { describe, expect, it } from "vitest";
import { layout, NODE_W, NODE_H, GAP_X, LOOP_GAP } from "./layout";
import type { Step } from "@/features/board/model";
const step = (id: string, n = 1, onChanges?: string): Step => ({
  id,
  onChanges,
  executions: Array.from({ length: n }, (_, i) => ({ id: `e${i}` })),
});
describe("pipeline layout (§13.6)", () => {
  it("Fast has two centred nodes, a flow and a rework edge", () => {
    const g = layout([step("build"), step("approve", 1, "build")]);
    expect(g.nodes.map((n) => [n.x, n.y, n.w, n.h])).toEqual([
      [0, 0, NODE_W, NODE_H],
      [NODE_W + GAP_X, 0, NODE_W, NODE_H],
    ]);
    expect(g.groups).toEqual([]);
    expect(g.edges.map((e) => e.kind)).toEqual(["flow", "rework"]);
    expect(g.edges[1].lane).toBe(g.height + LOOP_GAP);
  });
  it("Full connects group frames and centres single steps", () => {
    const g = layout([step("spec"), step("build"), step("review", 4, "build"), step("approve")]);
    expect(g.nodes).toHaveLength(7);
    expect(g.groups).toHaveLength(1);
    expect(g.edges[1]).toMatchObject({ from: "build", to: "review", kind: "flow" });
    expect(g.edges[2]).toMatchObject({ from: "review", to: "approve", kind: "flow" });
    expect(g.nodes[0].y).toBe((g.height - NODE_H) / 2);
    for (const node of g.nodes.filter((n) => n.step === "review")) {
      expect(node.y).toBeGreaterThan(g.groups[0].y);
      expect(node.y + node.h).toBeLessThanOrEqual(g.groups[0].y + g.groups[0].h);
    }
  });
  it("stacks sixteen reviewers without overlapping", () => {
    const g = layout([step("build"), step("review", 16)]);
    const nodes = g.nodes.filter((n) => n.step === "review");
    expect(nodes).toHaveLength(16);
    nodes.slice(1).forEach((n, i) => expect(n.y).toBeGreaterThan(nodes[i].y + NODE_H));
  });
  it("orders loops by length in distinct lanes", () => {
    const g = layout([step("build"), step("review", 2, "build"), step("approve", 1, "build")]);
    const loops = g.edges.filter((e) => e.kind === "rework");
    expect(loops.map((e) => e.from)).toEqual(["review", "approve"]);
    expect(loops.map((e) => e.lane)).toEqual([g.height + LOOP_GAP, g.height + 2 * LOOP_GAP]);
  });
  it("32 steps have a deterministic scroll width", () => {
    const steps = Array.from({ length: 32 }, (_, i) => step(`s${i}`));
    expect(layout(steps).width).toBe(32 * (NODE_W + GAP_X) - GAP_X);
    expect(layout(steps)).toEqual(layout(steps));
  });
  it("handles empty input", () => expect(layout([]).width).toBe(0));
});
