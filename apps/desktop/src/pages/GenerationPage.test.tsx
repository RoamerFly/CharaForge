import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { GenerationPage } from "./GenerationPage";

describe("generation entry", () => {
  it("opens on the API tab with two named accessible tabs", () => {
    const html = renderToStaticMarkup(<GenerationPage />);
    expect(html).toContain('role="tablist"');
    expect(html).toContain('aria-selected="true" aria-controls="generation-panel-cloud"');
    expect(html).toContain("API / 订阅生图");
    expect(html).toContain("本地 ComfyUI 生图");
    expect(html).toContain("添加参考图");
    expect(html).toContain("查看最终提示词");
    expect(html).not.toContain("Checkpoint");
  });
});
