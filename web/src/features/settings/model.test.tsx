import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError, configSchema } from "@/lib/api";
import { PipelineView } from "@/features/job/pipeline/PipelineView";
import { PipelineEditor } from "./PipelineEditor";
import {
  draftPipelineNames,
  addGroupMember,
  addPipeline,
  addPipelineSteps,
  addStage,
  canRemovePipeline,
  draftState,
  editExecution,
  errorsAt,
  errorsUnder,
  groupWithNext,
  moveStage,
  removeGroupMember,
  removeStage,
  setStageField,
  stageKinds,
  ungroup,
  entries,
  fieldErrors,
  invalidSteps,
  parseDraft,
  pipelineNameError,
  previewDraft,
  removePipeline,
} from "./model";

const config = configSchema.parse({
  version: 2,
  engine: ">=1.0,<2",
  defaultPipeline: "fast",
  pipelines: {
    fast: [
      {
        id: "build",
        uses: "builder",
        provider: "codex",
        profiles: {
          codex: { model: "sol", effort: "medium" },
          claude: { model: "opus", effort: "high" },
        },
      },
      {
        id: "approve",
        uses: "approver",
        provider: "codex",
        profiles: { codex: { model: "sol", effort: "medium" } },
        onChanges: "build",
      },
    ],
  },
  maxRounds: 3,
  merge: "verify",
  permissions: "auto",
  concurrency: { perJob: 4 },
  timeouts: { executionMinutes: 60 },
  sensitivePaths: [],
});
const roles = [
  { name: "builder", provider: "codex", type: "task" },
  { name: "approver", provider: "codex", type: "approval" },
];

describe("pipeline edit operations", () => {
  const review = (id: string) => ({
    id,
    uses: "reviewer",
    provider: "codex" as const,
    profiles: { codex: { model: "sol", effort: "low" } },
  });
  const reviews = (() => {
    const next = structuredClone(config);
    next.pipelines.fast = [review("a"), review("b"), review("c")];
    return next;
  })();
  it("adds with a unique id, refuses at 32 and removal of the last stage", () => {
    const added = addStage(config, "fast", 0);
    expect(added.pipelines.fast.map((s) => s.id)).toEqual(["build", "stage-2", "approve"]);
    expect(addStage(added, "fast", 0).pipelines.fast[1].id).toBe("stage-3");
    let full = config;
    while (full.pipelines.fast.length < 32) full = addStage(full, "fast", 0);
    expect(addStage(full, "fast", 0)).toBe(full);
    const single = { ...config, pipelines: { fast: [config.pipelines.fast[0]] } };
    expect(removeStage(single, "fast", 0)).toBe(single);
  });
  it("keeps onChanges consistent on remove, move and rename", () => {
    expect(removeStage(config, "fast", 0).pipelines.fast[0].onChanges).toBeUndefined();
    expect(moveStage(config, "fast", 1, -1).pipelines.fast[0].onChanges).toBeUndefined();
    expect(setStageField(config, "fast", 0, "id", "make").pipelines.fast[1].onChanges).toBe("make");
    expect(setStageField(config, "fast", 1, "onChanges", "").pipelines.fast[1]).not.toHaveProperty(
      "onChanges",
    );
  });
  it("groups, grows, shrinks and ungroups review stages", () => {
    const grouped = groupWithNext(reviews, "fast", 0);
    const group = grouped.pipelines.fast[0];
    expect(group.id).toBe("a");
    expect("parallel" in group && group.parallel.map((m) => m.id)).toEqual(["a-2", "b"]);
    const grown = addGroupMember(grouped, "fast", 0);
    expect(entries(grown.pipelines.fast[0]).map((m) => m.id)).toEqual(["a-2", "b", "b-2"]);
    const shrunk = removeGroupMember(removeGroupMember(grown, "fast", 0, 2), "fast", 0, 0);
    expect(shrunk.pipelines.fast[0]).toMatchObject({ id: "a", uses: "reviewer" });
    expect("parallel" in shrunk.pipelines.fast[0]).toBe(false);
    expect(ungroup(grown, "fast", 0).pipelines.fast.map((s) => s.id)).toEqual([
      "a-2",
      "b",
      "b-2",
      "c",
    ]);
  });
  it("derives kinds and finds field errors by path", () => {
    expect(stageKinds(config.pipelines.fast, undefined, roles)).toEqual(["task", "approval"]);
    expect(stageKinds(config.pipelines.fast, undefined, [])).toEqual([undefined, undefined]);
    const errors = [
      { path: "pipelines.fast.0.id", message: "duplicate" },
      { path: "pipelines.fast.0.parallel.1.id", message: "other" },
    ];
    expect(errorsAt(errors, "pipelines.fast.0.id")).toEqual(["duplicate"]);
    expect(errorsUnder(errors, "pipelines.fast.0")).toEqual(["duplicate", "other"]);
    expect(errorsUnder(errors, "pipelines.fast.1")).toEqual([]);
  });
});

describe("pipeline create and remove", () => {
  it("rejects names that are empty, malformed or taken, and accepts a fresh one", () => {
    expect(pipelineNameError(config, "")).toMatch(/Enter a name/);
    expect(pipelineNameError(config, "Docs")).toMatch(/lowercase/);
    expect(pipelineNameError(config, "-docs")).toMatch(/lowercase/);
    expect(pipelineNameError(config, "a".repeat(33))).toMatch(/32 characters/);
    expect(pipelineNameError(config, "fast")).toMatch(/already exists/);
    expect(pipelineNameError(config, "docs-only")).toBeUndefined();
  });
  it("starts a new pipeline as an independent copy of the source", () => {
    const next = addPipeline(config, "docs", "fast");
    expect(Object.keys(next.pipelines)).toEqual(["fast", "docs"]);
    expect(next.pipelines.docs).toEqual(config.pipelines.fast);
    entries(next.pipelines.docs[0])[0].profiles.codex.model = "changed";
    expect(entries(next.pipelines.fast[0])[0].profiles.codex.model).toBe("sol");
    expect(addPipeline(config, "fast", "fast")).toBe(config);
    expect(addPipeline(config, "docs", "missing")).toBe(config);
  });
  it("adds a library pipeline as an independent copy under its own name", () => {
    const steps = structuredClone(config.pipelines.fast);
    const next = addPipelineSteps(config, "shared", steps);
    expect(next.pipelines.shared).toEqual(steps);
    steps[0].id = "changed";
    expect(next.pipelines.shared[0].id).toBe("build");
    expect(addPipelineSteps(config, "fast", steps)).toBe(config);
  });
  it("keeps the default pipeline and the last pipeline", () => {
    const two = addPipeline(config, "docs", "fast");
    expect(canRemovePipeline(two, "docs")).toBe(true);
    expect(canRemovePipeline(two, "fast")).toBe(false);
    expect(canRemovePipeline(config, "fast")).toBe(false);
    expect(Object.keys(removePipeline(two, "docs").pipelines)).toEqual(["fast"]);
    expect(removePipeline(two, "fast")).toBe(two);
  });
});

describe("pipeline editor choices", () => {
  const catalog = {
    codex: {
      source: "cli" as const,
      detail: null,
      efforts: ["low", "medium", "ultra"],
      models: [{ id: "sol-2", efforts: ["low", "medium", "ultra"], defaultEffort: "low" }],
    },
  };
  const available = {
    codex: [
      { name: "builder", type: "task", description: "", custom: false },
      { name: "docs-writer", type: "task", description: "", custom: true },
      { name: "approver", type: "approval", description: "", custom: false },
    ],
  };
  const render = (props = {}) =>
    renderToStaticMarkup(
      <PipelineEditor
        config={config}
        name="fast"
        errors={[]}
        roles={roles}
        catalog={catalog}
        availableRoles={available}
        onChange={() => {}}
        {...props}
      />,
    );
  it("lists the available roles with their type, and marks the repository's own", () => {
    const html = render();
    expect(html).toContain("builder · task");
    expect(html).toContain("docs-writer · task · custom");
    expect(html).toContain("approver · approval");
  });
  it("offers the model's own efforts, and says when a saved value is not in the tool's list", () => {
    const html = render();
    expect(html).toContain("ultra");
    expect(html).toContain("Not in this tool&#x27;s model list on this machine.");
  });
  it("falls back to built-in choices and says why when a tool could not be read", () => {
    const html = render({
      catalog: {
        codex: { source: "builtin", detail: "codex is not installed.", efforts: [], models: [] },
      },
      availableRoles: undefined,
    });
    expect(html).toContain("Built-in choices. codex is not installed.");
    expect(html).toContain("builder");
  });
});

describe("settings draft and preview", () => {
  it("switches provider without changing either profile and edits only the selected one", () => {
    const switched = editExecution(config, "fast", 0, 0, "provider", "claude");
    const changed = editExecution(switched, "fast", 0, 0, "model", "sonnet");
    const restored = editExecution(changed, "fast", 0, 0, "provider", "codex");
    expect(restored.pipelines.fast[0]).toMatchObject({
      provider: "codex",
      profiles: {
        codex: { model: "sol", effort: "medium" },
        claude: { model: "sonnet", effort: "high" },
      },
    });
    expect(entries(config.pipelines.fast[0])[0].profiles.claude.model).toBe("opus");
  });
  it("does not silently create a profile on provider switch; editing supplies explicit values", () => {
    const switched = editExecution(config, "fast", 1, 0, "provider", "claude");
    expect(entries(switched.pipelines.fast[1])[0].profiles.claude).toBeUndefined();
    const changed = editExecution(switched, "fast", 1, 0, "model", "opus");
    expect(entries(changed.pipelines.fast[1])[0].profiles.claude).toEqual({
      model: "opus",
      effort: "",
    });
  });
  it("projects edited profiles and role kinds into the shared read-only diagram", () => {
    const steps = previewDraft(
      JSON.stringify(editExecution(config, "fast", 0, 0, "model", "new-model")),
      "fast",
      roles,
    );
    const html = renderToStaticMarkup(<PipelineView steps={steps} />);
    expect(html).toContain("new-model");
    expect(html).toContain("APPROVAL");
    expect(html).toContain("CHANGES");
  });
  it("keeps invalid nodes visible and highlights API field and semantic errors", () => {
    const raw = structuredClone(config) as unknown as {
      pipelines: { fast: Record<string, unknown>[] };
    };
    raw.pipelines.fast[0].provider = "unknown";
    const text = JSON.stringify(raw);
    expect(parseDraft(text).error).toBeTruthy();
    expect(draftPipelineNames(text)).toEqual(["fast"]);
    const steps = previewDraft(text, "fast", roles);
    const errors = fieldErrors(
      new ApiError(422, [
        { loc: ["pipelines", "fast", 0, "SingleStep", "provider"], msg: "Invalid provider" },
        { loc: [], msg: "fast/approve: onChanges must name an earlier task" },
      ]),
    );
    const invalid = invalidSteps(steps, "fast", errors);
    expect(invalid).toEqual(["build", "approve"]);
    expect(renderToStaticMarkup(<PipelineView steps={steps} invalidSteps={invalid} />)).toContain(
      "pipeline-invalid",
    );
  });
  it("handles incomplete JSON, missing ids and empty parallel groups without crashing", () => {
    expect(previewDraft("{", "fast", [])).toEqual([]);
    const steps = previewDraft('{"pipelines":{"fast":[{"parallel":[]}]}}', "fast", []);
    expect(() => renderToStaticMarkup(<PipelineView steps={steps} />)).not.toThrow();
  });
});
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { createDashboardRouter } from "@/router";
import { keys } from "@/lib/api";

async function renderSettingsRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  client.setQueryData(keys.overview, {
    repos: [
      {
        id: "alpha",
        name: "Alpha",
        path: "C:/alpha",
        status: "available",
        detail: null,
        counts: { running: 0, "needs-you": 0, queued: 1 },
      },
    ],
  });
  client.setQueryData(keys.state("alpha"), {
    config,
    jobs: [],
    executions: [],
    activity: [],
    last_event_id: 0,
    backlog: [
      {
        index: 0,
        key: "item-key",
        title: "Feature <script>",
        details: "Keep the details",
        priority: "normal",
        status: "ready",
      },
    ],
  });
  client.setQueryData(["roles", "alpha", config], {
    roles: [
      {
        name: "builder",
        provider: "codex",
        type: "task",
        source: "factory/roles/builder.md",
        status: "ejected; built-in has changed since",
        ejectedFrom: "builder@1.0#12345678",
        outdated: true,
        sha256: "12345678",
      },
    ],
  });
  client.setQueryData(["machine-settings"], {
    concurrency: { global: 6 },
    cli: { codex: "C:/Codex/codex.exe", claude: null },
    ports: { range: "5100-5999" },
  });
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

describe("session 3.5 routes", () => {
  it("renders backlog actions, details, pipeline selector and keyboard reorder controls", async () => {
    const html = await renderSettingsRoute("/repo/alpha/backlog");
    expect(html).toContain("Feature &lt;script&gt;");
    expect(html).toContain("Keep the details");
    expect(html).toContain('draggable="true"');
    expect(html).toContain("Move Feature &lt;script&gt; up");
    expect(html).toContain("Start");
    expect(html).toContain("Remove");
    expect(html).toContain("Add item");
    expect(html).toContain('<option selected="">fast</option>');
  });
  it("renders repository editor, preview and role provenance", async () => {
    const html = await renderSettingsRoute("/repo/alpha/settings");
    expect(html).toContain("Config JSON");
    expect(html).toContain("build provider");
    expect(html).toContain("approve model");
    expect(html).toContain("Pipeline preview");
    expect(html).toContain("factory/roles/builder.md");
    expect(html).toContain("ejected; built-in has changed since");
    expect(html).not.toContain("coming in the next");
  });
  it("renders machine fields independently from repository settings", async () => {
    const html = await renderSettingsRoute("/settings");
    expect(html).toContain("Global concurrency");
    expect(html).toContain("C:/Codex/codex.exe");
    expect(html).toContain("5100-5999");
    expect(html).not.toContain("Config JSON");
  });
});

it("preserves unsaved edits across SSE updates, with reload and keep-editing choices", () => {
  const draft = { text: "local edits", base: "original" };
  expect(draftState("original", draft, "")).toEqual({
    text: "local edits",
    dirty: true,
    conflict: false,
  });
  expect(draftState("changed elsewhere", draft, "")).toEqual({
    text: "local edits",
    dirty: true,
    conflict: true,
  });
  expect(draftState("changed elsewhere", draft, "changed elsewhere").conflict).toBe(false);
  expect(draftState("another change", draft, "changed elsewhere").conflict).toBe(true);
  expect(draftState("changed elsewhere", undefined, "")).toEqual({
    text: "changed elsewhere",
    dirty: false,
    conflict: false,
  });
});
