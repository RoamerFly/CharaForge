import { describe, expect, it } from "vitest";
import { EMPTY_PROMPT } from "./cloud-generation";
import { reconcilePrompts, type PromptBindings } from "./resource-prompts";

const binding = (pose: string, text = pose): PromptBindings => ({
  identity: { key: "character-a/outfit-1", text: "identity" },
  pose: { key: pose, text },
  scene: { key: "room-a/F", text: "room" },
  quality: { key: "character-a/quality", text: "quality" },
});
describe("resource prompt drafts", () => {
  it("restores edited text when a resource is selected again", () => {
    const drafts = new Map<string, string>();
    const a = binding("pose-a");
    const initial = reconcilePrompts(EMPTY_PROMPT, {}, a, drafts);
    const edited = { ...initial, pose: "custom pose a", expression: "smile" };
    const b = binding("pose-b");
    const switched = reconcilePrompts(edited, a, b, drafts);
    expect(switched.pose).toBe("pose-b");
    const restored = reconcilePrompts(switched, b, a, drafts);
    expect(restored.pose).toBe("custom pose a");
    expect(restored.expression).toBe("smile");
  });
  it("refreshes untouched source text while keeping explicit edits", () => {
    const drafts = new Map<string, string>();
    const old = binding("pose-a", "old text");
    const fresh = binding("pose-a", "new text");
    expect(reconcilePrompts({ ...EMPTY_PROMPT, pose: "old text" }, old, fresh, drafts).pose).toBe("new text");
    expect(reconcilePrompts({ ...EMPTY_PROMPT, pose: "my edit" }, old, fresh, drafts).pose).toBe("my edit");
  });
  it("does not carry an identity draft into a different character", () => {
    const drafts = new Map<string, string>();
    const a = binding("pose-a");
    const b = { ...a, identity: { key: "character-b/outfit-1", text: "different face" } };
    const switched = reconcilePrompts({ ...EMPTY_PROMPT, identity: "edited face a" }, a, b, drafts);
    expect(switched.identity).toBe("different face");
    expect(reconcilePrompts(switched, b, a, drafts).identity).toBe("edited face a");
  });
});
