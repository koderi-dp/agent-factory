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
    models: ["gpt-6.1-sol", "gpt-6-luna", "gpt-6-astra"],
    efforts: ["low", "medium", "high"],
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
