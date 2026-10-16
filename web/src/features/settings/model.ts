import { ApiError, configSchema, type RepoState } from "@/lib/api";
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
  field: "provider" | "model" | "effort",
  value: string,
): Config {
  const next = structuredClone(config);
  const target = entries(next.pipelines[name][si])[ei];
  if (field === "provider") target.provider = value as "codex" | "claude";
  else
    target.profiles[target.provider] = {
      ...(target.profiles[target.provider] ?? { model: "", effort: "" }),
      [field]: value,
    };
  return next;
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
