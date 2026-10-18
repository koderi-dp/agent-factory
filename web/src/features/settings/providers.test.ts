import { describe, expect, it } from "vitest";
import type { ProviderModels } from "@/lib/api";
import {
  choiceList,
  choicesFor,
  effortChoices,
  modelChoices,
  replacementEffort,
} from "./providers";

describe("provider choices", () => {
  it("lists models and efforts for each provider and none for an unknown one", () => {
    expect(choicesFor("claude", "models")).toContain("opus");
    expect(choicesFor("claude", "efforts")).toEqual(["low", "medium", "high", "xhigh", "max"]);
    expect(choicesFor("codex", "models")).toContain("gpt-6-astra");
    expect(choicesFor("opencode", "efforts")).toContain("high");
    expect(choicesFor("nobody", "models")).toEqual([]);
  });
  it("keeps a saved value that is not a known choice, first, without duplicating a known one", () => {
    const models = choicesFor("claude", "models");
    expect(choiceList(models, "my-model")).toEqual(["my-model", ...models]);
    expect(choiceList(models, "opus")).toEqual([...models]);
    expect(choiceList(models, "")).toEqual([...models]);
  });
});

const catalog: ProviderModels = {
  codex: {
    source: "cli",
    detail: null,
    efforts: ["low", "medium", "ultra"],
    models: [
      { id: "gpt-a", efforts: ["low", "medium", "ultra"], defaultEffort: "low" },
      { id: "gpt-b", efforts: ["low", "medium"], defaultEffort: "medium" },
    ],
  },
  opencode: { source: "builtin", detail: "opencode is not installed.", efforts: [], models: [] },
};

describe("choices from what the tools report", () => {
  it("uses the tool's own models when it answered and the built-ins when it did not", () => {
    expect(modelChoices(catalog, "codex")).toEqual({
      options: ["gpt-a", "gpt-b"],
      authoritative: true,
    });
    const fallback = modelChoices(catalog, "opencode");
    expect(fallback.authoritative).toBe(false);
    expect(fallback.options).toEqual([...choicesFor("opencode", "models")]);
    expect(modelChoices(undefined, "claude").options).toContain("opus");
  });
  it("narrows efforts to the picked model, then to the provider, then to the built-ins", () => {
    expect(effortChoices(catalog, "codex", "gpt-b")).toEqual(["low", "medium"]);
    expect(effortChoices(catalog, "codex", "unknown")).toEqual(["low", "medium", "ultra"]);
    expect(effortChoices(catalog, "opencode", "x")).toEqual([...choicesFor("opencode", "efforts")]);
    expect(effortChoices(undefined, "claude", "opus")).toEqual([
      "low",
      "medium",
      "high",
      "xhigh",
      "max",
    ]);
  });
  it("proposes an effort only when the new model does not support the current one", () => {
    expect(replacementEffort(catalog, "codex", "gpt-b", "ultra")).toBe("medium");
    expect(replacementEffort(catalog, "codex", "gpt-b", "low")).toBeUndefined();
    expect(replacementEffort(catalog, "codex", "unknown", "ultra")).toBeUndefined();
    expect(replacementEffort(undefined, "codex", "gpt-b", "ultra")).toBeUndefined();
    expect(replacementEffort(catalog, "opencode", "x", "high")).toBeUndefined();
  });
});
