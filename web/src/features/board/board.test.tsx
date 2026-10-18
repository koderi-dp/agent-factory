import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { createDashboardRouter } from "@/router";
import {
  attemptSchema,
  executionSchema,
  jobSchema,
  keys,
  stateSchema,
  type RepoState,
} from "@/lib/api";
import { statusLabel, statusTone } from "@/ui/status-text";
import { compactLayout } from "@/features/job/pipeline/layout";
import { ActivityLine } from "./ActivityLine";
import { Board } from "./Board";
import {
  activityState,
  boardGroups,
  boardColumns,
  currentExecutions,
  frozenSteps,
  jobColumn,
  latestAttempt,
} from "./model";

const at = "2026-10-16T12:00:00Z";
const now = Date.parse(at) + 600_000;
const steps = [
  { id: "build", label: "Build", executions: [{ id: "builder" }] },
  { id: "review", label: "Review", executions: [{ id: "code" }, { id: "security" }] },
];
function job(overrides = {}) {
  return jobSchema.parse({
    id: "001-feature",
    seq: 1,
    title: "A <safe> title",
    pipeline_name: "full",
    frozen: { pipeline: steps },
    state: "running",
    pause_reason: null,
    note: null,
    current_step: "review",
    round: 1,
    returns_used: 0,
    port_base: null,
    branch: "feature",
    worktree: "/work",
    base_head: null,
    approved_head: null,
    created_at: at,
    updated_at: at,
    ...overrides,
  });
}
function execution(overrides = {}) {
  return executionSchema.parse({
    id: 1,
    job_id: "001-feature",
    step_id: "review",
    exec_id: "code",
    round: 1,
    type: "review",
    uses: "reviewer",
    provider: "codex",
    model: "model",
    effort: "low",
    state: "running",
    result: null,
    head_before: null,
    head_after: null,
    report_path: null,
    created_at: at,
    ...overrides,
  });
}
function attempt(overrides = {}) {
  return attemptSchema.parse({
    id: 10,
    execution_id: 1,
    n: 1,
    kind: "fresh",
    state: "running",
    session_id: null,
    wrapper_pid: null,
    wrapper_ctime: null,
    exit_code: null,
    error_class: null,
    error_detail: null,
    dir: "attempt",
    log_offset: 0,
    last_activity: "reading old.ts",
    last_activity_at: at,
    t_queued: at,
    t_launch: at,
    t_first_event: at,
    t_exit: null,
    t_validated: null,
    packet_bytes: null,
    usage: null,
    ...overrides,
  });
}
function snapshot(overrides = {}): RepoState {
  return stateSchema.parse({
    jobs: [job()],
    executions: [execution()],
    backlog: [],
    activity: [],
    last_event_id: 0,
    config: {
      version: 2,
      engine: ">=1",
      defaultPipeline: "full",
      pipelines: {},
      maxRounds: 3,
      merge: "verify",
      permissions: "auto",
      concurrency: { perJob: 4 },
      timeouts: { executionMinutes: 60 },
      sensitivePaths: [],
    },
    ...overrides,
  });
}

describe("board evidence and placement", () => {
  it("uses frozen steps and preserves distinct versions of a named pipeline", () => {
    const old = job({ id: "old", frozen: { pipeline: steps.slice(0, 1) } });
    const state = snapshot({ jobs: [job(), old, job({ id: "third" })] });
    const groups = boardGroups(state);
    expect(groups.map((g) => g.jobs.length)).toEqual([2, 1]);
    expect(groups[1].steps).toEqual(frozenSteps(old));
    expect(jobColumn(job())).toBe("step:review");
    expect(jobColumn(job({ state: "paused" }))).toBe("step:review");
    expect(jobColumn(job({ state: "needs_human" }))).toBe("step:review");
    expect(jobColumn(job({ state: "queued", current_step: null }))).toBe("step:build");
    for (const state of ["approved", "merged", "cancelled"])
      expect(jobColumn(job({ state }))).toBe(`state:${state}`);
  });
  it("keeps custom step names distinct from terminal state columns", () => {
    const custom = [{ id: "merged", executions: [{ id: "builder" }] }];
    const active = job({ current_step: "merged" });
    const done = job({ state: "merged" });
    expect(jobColumn(active, custom)).not.toBe(jobColumn(done, custom));
    const ids = boardColumns(custom, [active, done]).map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
  it("keeps a completed upstream round but discards downstream evidence after a rewind", () => {
    const build = execution({
      id: 2,
      step_id: "build",
      exec_id: "builder",
      state: "done",
      result: "READY",
    });
    const review = execution({ id: 3, round: 2 });
    expect(currentExecutions(steps, [build, review])).toEqual([build, review]);
    const rebuilt = { ...build, id: 4, round: 3, state: "running" as const, result: null };
    expect(currentExecutions(steps, [build, review, rebuilt])).toEqual([rebuilt]);
    expect(compactLayout(steps, [build, review, rebuilt]).map((s) => s.state)).toEqual([
      "running",
      "pending",
    ]);
  });
  it("aggregates parallel pending, running, completed, changes and failed states", () => {
    const code = execution({ state: "done", result: "PASS" });
    expect(compactLayout(steps, [code])[1].state).toBe("pending");
    const security = execution({ id: 2, exec_id: "security" });
    expect(compactLayout(steps, [code, security])[1].state).toBe("running");
    expect(
      compactLayout(steps, [code, { ...security, state: "done", result: "PASS" }])[1].state,
    ).toBe("done");
    expect(compactLayout(steps, [{ ...code, result: "CHANGES" }, security])[1].state).toBe(
      "changes",
    );
    expect(compactLayout(steps, [code, { ...security, state: "failed" }])[1].state).toBe("failed");
    expect(currentExecutions(steps, [{ ...code, state: "superseded" }])).toEqual([]);
  });
});

describe("activity lines", () => {
  it("maps retries by execution, overlays SSE activity, and warns at ten minutes", () => {
    const first = attempt();
    const retry = attempt({ id: 11, n: 2 });
    expect(latestAttempt(execution(), [retry, attempt({ execution_id: 2, n: 5 }), first])).toEqual(
      retry,
    );
    expect(
      activityState(execution(), retry, [{ attempt_id: 11, text: "running tests", at }], now),
    ).toMatchObject({ text: "running tests", quiet: true });
    expect(activityState(execution(), retry, [], now - 1).quiet).toBe(false);
    expect(activityState(execution({ state: "done" }), retry, [], now).quiet).toBe(false);
    expect(
      activityState(execution(), attempt({ last_activity_at: null, t_first_event: null }), [], now)
        .quiet,
    ).toBe(true);
  });
  it("renders current activity safely, then replaces it with the result and duration", () => {
    const running = renderToStaticMarkup(
      <ActivityLine
        execution={execution()}
        attempt={attempt()}
        activity={[{ attempt_id: 10, text: "reading <script>", at }]}
        now={now}
      />,
    );
    expect(running).toContain("reading &lt;script&gt;");
    expect(running).toContain("Quiet");
    const done = renderToStaticMarkup(
      <ActivityLine
        execution={execution({ state: "done", result: "PASS" })}
        attempt={attempt({ t_exit: "2026-10-16T12:02:00Z" })}
        activity={[]}
        now={now}
      />,
    );
    expect(done).toContain("PASS");
    expect(done).toContain("2m 0s");
    expect(done).not.toContain("reading");
  });
});

describe("dashboard rendering", () => {
  async function renderRoute(path: string, extraRepos: object[] = []) {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
    client.setQueryData(keys.overview, {
      repos: [
        {
          id: "alpha",
          name: "Alpha",
          path: "C:/alpha",
          status: "available",
          detail: null,
          counts: { running: 2, "needs-you": 3, queued: 4, merged: 1 },
          last_event_id: 5,
        },
        {
          id: "beta",
          name: "Beta",
          path: "C:/beta",
          status: "available",
          detail: null,
          counts: { running: 0, "needs-you": 2, queued: 0, merged: 0 },
          last_event_id: 0,
        },
        ...extraRepos,
      ],
    });
    client.setQueryData(keys.jobs(["queued", "running", "paused", "needs_human", "approved"]), {
      repos: [],
      jobs: [],
      truncated: false,
    });
    client.setQueryData(keys.jobs(["paused", "needs_human"]), {
      repos: [],
      jobs: [],
      truncated: false,
    });
    client.setQueryData(keys.activity(20), { repos: [], events: [], next: null });
    for (const repo of ["alpha", "beta"]) {
      client.setQueryData(
        keys.state(repo),
        snapshot({ jobs: [job({ title: `${repo} feature` })] }),
      );
      client.setQueryData(keys.job(repo, "001-feature"), {
        job: job(),
        executions: [],
        attempts: [],
      });
    }
    const router = createDashboardRouter(createMemoryHistory({ initialEntries: [path] }));
    await router.load();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    client.clear();
    return html;
  }
  it("renders the overview by default and sums needs-you counts across repos", async () => {
    const html = await renderRoute("/");
    expect(html).toContain("Factory overview");
    expect(html).toContain("5 need you");
    expect(html).toContain("Alpha · 3 need you");
    expect(html).toContain("Beta · 2 need you");
    expect(html).toContain("Connecting");
  });
  it("selects the board from the repo route and preserves shareable detail URLs", async () => {
    const html = await renderRoute("/repo/beta");
    expect(html).toContain("beta feature");
    expect(html).not.toContain("alpha feature");
    expect(html).toContain('href="/repo/beta/backlog"');
    expect(html).toContain('href="/repo/beta/settings"');
    expect(html).toMatch(/aria-current="page"[^>]*>Board/);
    const detail = await renderRoute("/repo/beta/job/001-feature?file=review.md");
    expect(detail).toContain('aria-label="Job views"');
    expect(detail).toContain('data-state="active"');
    expect(detail).toContain("Reports");
    expect(detail).toContain("001-feature");
    expect(detail).not.toContain("beta feature");
  });
  it("handles a removed repo without rendering another repo's board", async () => {
    const html = await renderRoute("/repo/removed");
    expect(html).toContain("Repository not found");
    expect(html).not.toContain("alpha feature");
  });
  it("renders frozen columns, parallel chips, pause details, and terminal jobs", () => {
    const client = new QueryClient();
    const state = snapshot({
      jobs: [
        job({
          state: "paused",
          pause_reason: "ports",
          note: "Port 5131 held by node.exe (pid 4242)",
        }),
        job({ id: "merged", state: "merged" }),
      ],
      executions: [
        execution(),
        execution({ id: 2, exec_id: "security", state: "done", result: "PASS" }),
      ],
      activity: [{ attempt_id: 10, text: "running npm test", at }],
    });
    client.setQueryData(keys.state("repo space"), state);
    for (const j of state.jobs)
      client.setQueryData(keys.job("repo space", j.id), {
        job: j,
        executions: state.executions,
        attempts: [attempt()],
      });
    const html = renderToStaticMarkup(
      <QueryClientProvider client={client}>
        <Board repo="repo space" name="Project" now={now} />
      </QueryClientProvider>,
    );
    expect(html).toContain('aria-label="Build"');
    expect(html).toContain('aria-label="Review"');
    expect(html).toContain("security (codex)");
    expect(html).toContain("PASS");
    expect(html).toContain("Paused");
    expect(html).toContain("pid 4242");
    expect(html).toContain("running npm test");
    expect(html).toContain("A &lt;safe&gt; title");
    expect(html).toContain("#/repo/repo%20space/job/001-feature");
    expect(html).not.toContain("Legacy");
    client.clear();
  });
  it("shows overview counts, unavailable repos and first-use instructions", async () => {
    const html = await renderRoute("/", [
      {
        id: "gone",
        name: "Gone",
        path: "C:/gone",
        status: "missing",
        detail: null,
        counts: { running: 0, "needs-you": 0, queued: 0, merged: 0 },
        last_event_id: 0,
      },
    ]);
    expect(html).toContain("3 need you");
    expect(html).toContain("Repository missing");
    expect(html).toContain("#/repo/alpha");
    expect(html).not.toContain("#/repo/gone");
    const empty = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
    empty.setQueryData(keys.overview, { repos: [] });
    const router = createDashboardRouter(createMemoryHistory({ initialEntries: ["/"] }));
    await router.load();
    const none = renderToStaticMarkup(
      <QueryClientProvider client={empty}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    empty.clear();
    expect(none).toContain("factory init");
  });
  it("maps factory statuses to semantic tones and explicit labels", () => {
    expect(statusLabel("needs_human")).toBe("Needs you");
    for (const state of ["PASS", "READY", "APPROVE", "merged", "connected"])
      expect(statusTone(state)).toBe("live");
    for (const state of ["paused", "needs_human", "CHANGES"])
      expect(statusTone(state)).toBe("warn");
    for (const state of ["BLOCKED", "ESCALATE", "failed", "disconnected"])
      expect(statusTone(state)).toBe("danger");
  });
});
