import { describe, expect, it } from "vitest";
import { composePrompt, EMPTY_PROMPT } from "./cloud-generation";

describe("cloud prompt composition", () => {
  it("combines only populated modules in their declared order", () => {
    expect(composePrompt({ ...EMPTY_PROMPT, identity: "  actor  ", scene: "room", pose: "sitting" }, []))
      .toBe("actor\n\nsitting\n\nroom");
  });
  it("keeps reference order and includes purposes exactly once", () => {
    const refs = [{ path: "C:/face.png", purpose: " face " }, { path: "C:/costume.png", purpose: "" }, { path: "C:/pose.png", purpose: "pose only" }];
    expect(composePrompt({ ...EMPTY_PROMPT, identity: "person" }, refs))
      .toBe("person\nReference image 1: face\nReference image 3: pose only");
  });
  it("does not inject filesystem paths into the prompt", () => {
    expect(composePrompt(EMPTY_PROMPT, [{ path: "C:/private/image.png", purpose: "" }])).toBe("");
  });
});
