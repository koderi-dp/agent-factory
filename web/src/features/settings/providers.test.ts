import { describe, expect, it } from "vitest";
import { choiceList, choicesFor } from "./providers";

describe("provider choices", () => {
  it("lists models and efforts for each provider and none for an unknown one", () => {
    expect(choicesFor("claude", "models")).toContain("opus");
    expect(choicesFor("claude", "efforts")).toEqual(["low", "medium", "high", "xhigh", "max"]);
    expect(choicesFor("codex", "models")).toContain("gpt-6.1-sol");
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
