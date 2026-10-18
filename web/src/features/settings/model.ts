import { ApiError, configSchema, type PipelinePreview, type RepoState } from "@/lib/api";
import type { Step } from "@/features/board/model";
export type Config = RepoState["config"];
export type Entry = Config["pipelines"][string][number];
export function entries(step: Entry) {
  return "parallel" in step ? step.parallel : [step];
}
export function parseDraft(text: string): { config?: Config; error?: string } {
  try {
    return { config: configSchema.parse(JSON.parse(text)) };
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error) };
  }
}
export function fieldErrors(error: unknown): { path: string; message: string }[] {
  if (!error) return [];
  if (error instanceof ApiError && Array.isArray(error.detail))
    return error.detail.map((item) => ({
      path: Array.isArray(item.loc) ? item.loc.join(".") : "config",
      message: String(item.msg),
    }));
  return [{ path: "config", message: error instanceof Error ? error.message : String(error) }];
}
export function invalidSteps(steps: Step[], name: string, errors: ReturnType<typeof fieldErrors>) {
  return steps
    .filter((step, i) =>
      errors.some(
        (error) =>
          error.path.startsWith(`pipelines.${name}.${i}.`) ||
          error.path === `pipelines.${name}.${i}` ||
          error.message.includes(`${name}/${step.id}:`) ||
          error.message.includes(`id: ${step.id}`) ||
          step.executions.some(
            (entry) =>
              error.message.includes(`id: ${entry.id}`) ||
              (!!entry.uses && error.message.includes(entry.uses)),
          ) ||
          (error.message.includes(`${name}: pipeline must end`) && i === steps.length - 1),
      ),
    )
    .map((step) => step.id);
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function rawPipelines(text: string) {
  try {
    return record(record(JSON.parse(text)).pipelines);
  } catch {
    return {};
  }
}
export function draftPipelineNames(text: string) {
  return Object.keys(rawPipelines(text));
}
/** Keep a usable diagram while individual fields are invalid; the API remains authoritative. */
export function previewDraft(
  text: string,
  name: string,
  roles: { name: string; provider: string; type: string }[],
): Step[] {
  const raw = rawPipelines(text)[name];
  if (!Array.isArray(raw)) return [];
  return raw.slice(0, 32).map((value, i) => {
    const step = record(value);
    const id = typeof step.id === "string" ? step.id : `invalid-${i}`;
    const rawEntries =
      Array.isArray(step.parallel) && step.parallel.length ? step.parallel : [step];
    return {
      id,
      label: typeof step.label === "string" ? step.label : undefined,
      onChanges: typeof step.onChanges === "string" ? step.onChanges : undefined,
      executions: rawEntries.slice(0, 16).map((value, j) => {
        const entry = record(value);
        const uses = typeof entry.uses === "string" ? entry.uses : "Invalid role";
        const provider = typeof entry.provider === "string" ? entry.provider : "Invalid provider";
        const profiles = Object.fromEntries(
          Object.entries(record(entry.profiles)).map(([key, value]) => {
            const profile = record(value);
            return [
              key,
              {
                model: typeof profile.model === "string" ? profile.model : "",
                effort: typeof profile.effort === "string" ? profile.effort : "",
              },
            ];
          }),
        );
        return {
          id: typeof entry.id === "string" ? entry.id : `${id}-${j}`,
          uses,
          provider,
          profiles,
          role: {
            type: roles.find((r) => r.name === uses && r.provider === provider)?.type ?? "task",
          },
        };
      }),
    };
  });
}

export function editExecution(
  config: Config,
  name: string,
  si: number,
  ei: number,
  field: "uses" | "provider" | "model" | "effort",
  value: string,
): Config {
  const next = structuredClone(config);
  const target = entries(next.pipelines[name][si])[ei];
  if (field === "uses") target.uses = value;
  else if (field === "provider") target.provider = value as "codex" | "claude" | "opencode";
  else
    target.profiles[target.provider] = {
      ...(target.profiles[target.provider] ?? { model: "", effort: "" }),
      [field]: value,
    };
  return next;
}

export const MAX_STAGES = 32;
export const MAX_GROUP_MEMBERS = 16;
export type StageKind = "task" | "review" | "approval";
type Single = Exclude<Entry, { parallel: unknown }>;
type Group = Extract<Entry, { parallel: unknown }>;
export const isGroup = (step: Entry): step is Group => "parallel" in step;

function takenIds(steps: Entry[]) {
  const taken = new Set<string>();
  for (const step of steps) {
    taken.add(step.id);
    for (const entry of entries(step)) taken.add(entry.id);
  }
  return taken;
}
function freshId(steps: Entry[], base: string) {
  const taken = takenIds(steps);
  let n = 2;
  while (taken.has(`${base}-${n}`)) n++;
  return `${base}-${n}`;
}
function stageBase(id: string) {
  return id.replace(/-\d+$/, "") || "stage";
}
/** Drop an onChanges that no longer names an earlier step. */
function pruneOnChanges(steps: Entry[]) {
  steps.forEach((step, i) => {
    if (!step.onChanges) return;
    const target = steps.findIndex((other) => other.id === step.onChanges);
    if (target >= i) delete step.onChanges;
  });
}

export function addStage(config: Config, name: string, at: number): Config {
  const steps = config.pipelines[name];
  if (!steps || steps.length >= MAX_STAGES) return config;
  const next = structuredClone(config);
  const list = next.pipelines[name];
  const source = entries(list[Math.min(Math.max(at, 0), list.length - 1)])[0];
  const stage: Single = {
    id: freshId(list, "stage"),
    uses: source.uses,
    provider: source.provider,
    profiles: structuredClone(source.profiles),
  };
  list.splice(at + 1, 0, stage);
  return next;
}
export function removeStage(config: Config, name: string, i: number): Config {
  const steps = config.pipelines[name];
  if (!steps || steps.length <= 1 || !steps[i]) return config;
  const next = structuredClone(config);
  const list = next.pipelines[name];
  const [removed] = list.splice(i, 1);
  if (list.some((step) => step.id === removed.id)) return next;
  for (const step of list) if (step.onChanges === removed.id) delete step.onChanges;
  return next;
}
export function moveStage(config: Config, name: string, i: number, delta: number): Config {
  const steps = config.pipelines[name];
  const j = i + delta;
  if (!steps || !steps[i] || j < 0 || j >= steps.length) return config;
  const next = structuredClone(config);
  const list = next.pipelines[name];
  [list[i], list[j]] = [list[j], list[i]];
  pruneOnChanges(list);
  return next;
}
export function setStageField(
  config: Config,
  name: string,
  i: number,
  field: "id" | "label" | "onChanges",
  value: string,
): Config {
  if (!config.pipelines[name]?.[i]) return config;
  const next = structuredClone(config);
  const list = next.pipelines[name];
  const step = list[i];
  if (field === "id") {
    const old = step.id;
    step.id = value;
    // A reference to an id another step still carries belongs to that step.
    if (!list.some((other) => other.id === old))
      for (const other of list) if (other.onChanges === old) other.onChanges = value;
  } else if (value === "") delete step[field];
  else step[field] = value;
  return next;
}
export function groupWithNext(config: Config, name: string, i: number): Config {
  const steps = config.pipelines[name];
  const first = steps?.[i];
  const second = steps?.[i + 1];
  if (!first || !second || isGroup(first) || isGroup(second)) return config;
  const next = structuredClone(config);
  const list = next.pipelines[name];
  const [a, b] = list.slice(i, i + 2) as [Single, Single];
  const member = (entry: Single, id: string): Group["parallel"][number] => ({
    id,
    uses: entry.uses,
    provider: entry.provider,
    profiles: entry.profiles,
    ...(entry.timeoutMinutes != null && { timeoutMinutes: entry.timeoutMinutes }),
    ...(entry.maxBudgetUsd != null && { maxBudgetUsd: entry.maxBudgetUsd }),
  });
  const group: Group = {
    id: a.id,
    parallel: [member(a, freshId(list, stageBase(a.id))), member(b, b.id)],
  };
  if (a.label) group.label = a.label;
  if (a.onChanges) group.onChanges = a.onChanges;
  list.splice(i, 2, group);
  return next;
}
export function addGroupMember(config: Config, name: string, i: number): Config {
  const step = config.pipelines[name]?.[i];
  if (!step || !isGroup(step) || step.parallel.length >= MAX_GROUP_MEMBERS) return config;
  const next = structuredClone(config);
  const list = next.pipelines[name];
  const group = list[i] as Group;
  const last = group.parallel[group.parallel.length - 1];
  group.parallel.push({ ...structuredClone(last), id: freshId(list, stageBase(last.id)) });
  return next;
}
export function removeGroupMember(config: Config, name: string, i: number, j: number): Config {
  const step = config.pipelines[name]?.[i];
  if (!step || !isGroup(step) || step.parallel.length <= 1 || !step.parallel[j]) return config;
  const next = structuredClone(config);
  const list = next.pipelines[name];
  const group = list[i] as Group;
  group.parallel.splice(j, 1);
  if (group.parallel.length === 1) {
    const only = group.parallel[0];
    const single: Single = { ...only, id: group.id };
    if (group.label) single.label = group.label;
    if (group.onChanges) single.onChanges = group.onChanges;
    list[i] = single;
  }
  return next;
}
export function ungroup(config: Config, name: string, i: number): Config {
  const step = config.pipelines[name]?.[i];
  if (!step || !isGroup(step)) return config;
  const next = structuredClone(config);
  const list = next.pipelines[name];
  const group = list[i] as Group;
  const singles: Single[] = group.parallel.map((member, j) => {
    const single: Single = { ...member };
    if (j === 0 && group.label) single.label = group.label;
    if (j === 0 && group.onChanges) single.onChanges = group.onChanges;
    return single;
  });
  list.splice(i, 1, ...singles);
  return next;
}

/** Each step's role type: from the matching validate preview, else from the resolved roles. */
export function stageKinds(
  steps: Entry[],
  preview: PipelinePreview | undefined,
  roles: { name: string; provider: string; type: string }[],
): (StageKind | undefined)[] {
  const kind = (value: string | undefined) =>
    value === "task" || value === "review" || value === "approval" ? value : undefined;
  const matches =
    !!preview &&
    preview.stages.length === steps.length &&
    preview.stages.every((stage, i) => stage.id === steps[i].id);
  return steps.map((step, i) => {
    if (matches) return kind(preview.stages[i].type);
    const first = entries(step)[0];
    return kind(roles.find((r) => r.name === first?.uses && r.provider === first.provider)?.type);
  });
}
export type FieldError = { path: string; message: string };
export function errorsAt(errors: FieldError[], path: string): string[] {
  return errors.filter((error) => error.path === path).map((error) => error.message);
}
export function errorsUnder(errors: FieldError[], prefix: string): string[] {
  return errors
    .filter((error) => error.path === prefix || error.path.startsWith(`${prefix}.`))
    .map((error) => error.message);
}

export type SettingsDraft = { text: string; base: string };
export function draftState(server: string, draft: SettingsDraft | undefined, kept: string) {
  const dirty = !!draft && draft.text !== draft.base;
  return {
    text: draft?.text ?? server,
    dirty,
    conflict: dirty && draft.base !== server && kept !== server,
  };
}

export const PIPELINE_NAME_PATTERN = /^[a-z0-9][a-z0-9-]{0,31}$/;

/** Why `name` cannot be a new pipeline's name, or undefined when it can. */
export function pipelineNameError(config: Config, name: string): string | undefined {
  if (!name) return "Enter a name for the new pipeline.";
  if (!PIPELINE_NAME_PATTERN.test(name))
    return "Use lowercase letters, digits and dashes, starting with a letter or digit, up to 32 characters.";
  if (Object.hasOwn(config.pipelines, name)) return `A pipeline named ${name} already exists.`;
  return undefined;
}

/** A new pipeline that starts as a copy of `source`, so it is valid from the first save. */
export function addPipeline(config: Config, name: string, source: string): Config {
  if (pipelineNameError(config, name) || !config.pipelines[source]) return config;
  const next = structuredClone(config);
  next.pipelines[name] = structuredClone(config.pipelines[source]);
  return next;
}

/** The default pipeline and the last remaining pipeline cannot be removed. */
export function canRemovePipeline(config: Config, name: string): boolean {
  return (
    name in config.pipelines &&
    name !== config.defaultPipeline &&
    Object.keys(config.pipelines).length > 1
  );
}

export function removePipeline(config: Config, name: string): Config {
  if (!canRemovePipeline(config, name)) return config;
  const next = structuredClone(config);
  delete next.pipelines[name];
  return next;
}

/** A copy of a library pipeline, added under its own name. */
export function addPipelineSteps(config: Config, name: string, steps: unknown[]): Config {
  if (pipelineNameError(config, name)) return config;
  const next = structuredClone(config);
  next.pipelines[name] = structuredClone(steps) as Config["pipelines"][string];
  return next;
}
