import type { ProviderModels } from "@/lib/api";

export type ChoiceKind = "models" | "efforts";

/** Value of the "Custom…" entry; never a real model or effort. */
export const CUSTOM_CHOICE = "__custom__";

// Values the engine and its docs use for each provider. The lists are suggestions, not a
// whitelist: the dropdown also keeps the saved value and offers "Custom…", because the
// engine accepts any model name the provider's CLI does.
const CHOICES: Record<string, Record<ChoiceKind, readonly string[]>> = {
  claude: {
    models: ["opus", "sonnet", "haiku"],
    efforts: ["low", "medium", "high", "xhigh", "max"],
  },
  codex: {
    models: ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
    efforts: ["low", "medium", "high", "xhigh", "max"],
  },
  opencode: {
    models: ["opencode/deepseek-v4.1-flash-free"],
    efforts: ["low", "medium", "high"],
  },
};

export function choicesFor(provider: string, kind: ChoiceKind): readonly string[] {
  return CHOICES[provider]?.[kind] ?? [];
}

/** The list to show: known choices, plus the saved value when it is not one of them. */
export function choiceList(options: readonly string[], value: string): string[] {
  return value && !options.includes(value) ? [value, ...options] : [...options];
}

/** What the installed tool reports, when it reported anything. */
function reported(catalog: ProviderModels | undefined, provider: string) {
  const entry = catalog?.[provider];
  return entry && entry.source === "cli" && entry.models.length ? entry : undefined;
}

/** Models for a provider: the tool's own list when it answered, built-ins otherwise. */
export function modelChoices(catalog: ProviderModels | undefined, provider: string) {
  const entry = reported(catalog, provider);
  return {
    options: entry ? entry.models.map((model) => model.id) : [...choicesFor(provider, "models")],
    authoritative: !!entry,
  };
}

/** Efforts for a model: the ones that model supports when the tool says, else the provider's. */
export function effortChoices(
  catalog: ProviderModels | undefined,
  provider: string,
  model: string,
): string[] {
  const entry = reported(catalog, provider);
  const known = entry?.models.find((item) => item.id === model);
  if (known) return known.efforts;
  return entry ? entry.efforts : [...choicesFor(provider, "efforts")];
}

/** The effort to switch to when a newly picked model does not support the current one. */
export function replacementEffort(
  catalog: ProviderModels | undefined,
  provider: string,
  model: string,
  effort: string,
): string | undefined {
  const known = reported(catalog, provider)?.models.find((item) => item.id === model);
  if (!known || !effort || known.efforts.includes(effort)) return undefined;
  return known.defaultEffort ?? known.efforts[0];
}
