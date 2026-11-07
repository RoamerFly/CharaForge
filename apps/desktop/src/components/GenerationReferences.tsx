import { useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { ArrowDown, ArrowUp, ImagePlus, X } from "lucide-react";
import { invokeCore, isTauriRuntime, normalizePreviewPath } from "../lib/tauri";
import type { GenerationReference } from "../lib/cloud-generation";

export function GenerationReferences({
  references, onChange, disabled, onError,
}: {
  references: GenerationReference[]; onChange: (refs: GenerationReference[]) => void;
  disabled: boolean; onError: (message: string) => void;
}) {
  const [pathText, setPathText] = useState("");
  const [adding, setAdding] = useState(false);
  const append = async (paths: string[]) => {
    const next = [...references];
    for (const path of paths) {
      if (next.some((reference) => reference.path === path)) continue;
      if (next.length >= 8) throw new Error("每次最多上传 8 张参考图。");
      const response = await invokeCore<GenerationReference>("import_generation_reference", "cloud.reference", { path, purpose: "" });
      if (!response.payload) throw new Error(response.error?.message ?? "无法读取参考图片。");
      if (!next.some((reference) => reference.path === response.payload!.path)) next.push(response.payload);
    }
    onChange(next);
  };
  const addPath = async () => {
    setAdding(true);
    try {
      await append([pathText.trim().replace(/^"|"$/g, "")]);
      setPathText("");
    } catch (error) { onError(error instanceof Error ? error.message : String(error)); }
    finally { setAdding(false); }
  };
  const add = async () => {
    setAdding(true);
    try {
      const selected = await open({ multiple: true, title: "添加生图参考图片", filters: [{ name: "图片", extensions: ["png", "jpg", "jpeg", "webp"] }] });
      if (!selected) return;
      await append(typeof selected === "string" ? [selected] : selected);
    } catch (error) { onError(error instanceof Error ? error.message : String(error)); }
    finally { setAdding(false); }
  };
  const move = (index: number, offset: number) => {
    const next = [...references];
    [next[index], next[index + offset]] = [next[index + offset], next[index]];
    onChange(next);
  };
  return (
    <div className="cloud-reference-editor">
      <div className="generation-inline">
        <strong>参考图</strong>
        <button className="ghost-button" type="button" disabled={disabled || adding || !isTauriRuntime()} onClick={() => void add()}>
          <ImagePlus size={15} aria-hidden="true" /> 添加参考图
        </button>
        <small>{references.length} / 8 · 按列表顺序上传</small>
      </div>
      <details className="reference-path-disclosure"><summary>按路径添加</summary><label className="generation-field">
        <span>或粘贴本机图片路径</span>
        <div className="generation-inline">
          <input value={pathText} disabled={disabled || adding} onChange={(event) => setPathText(event.target.value)} />
          <button type="button" className="ghost-button" disabled={disabled || adding || !pathText.trim() || !isTauriRuntime()} onClick={() => void addPath()}>添加路径</button>
        </div>
      </label></details>
      {references.map((reference, index) => (
        <div className="cloud-reference-row" key={reference.path}>
          <img src={convertFileSrc(normalizePreviewPath(reference.path))} alt={"参考图 " + (index + 1)} />
          <label className="generation-field">
            <span title={reference.path}>{index + 1}. {reference.path.split(/[\\/]/).pop()}</span>
            <input value={reference.purpose} disabled={disabled} placeholder="用途，例如身份主脸、衣装、动作"
              onChange={(e) => onChange(references.map((r, i) => i === index ? { ...r, purpose: e.target.value } : r))} />
          </label>
          <div className="generation-inline">
            <button type="button" className="ghost-button" aria-label={"上移参考图 " + (index + 1)} disabled={disabled || index === 0} onClick={() => move(index, -1)}><ArrowUp size={14} /></button>
            <button type="button" className="ghost-button" aria-label={"下移参考图 " + (index + 1)} disabled={disabled || index === references.length - 1} onClick={() => move(index, 1)}><ArrowDown size={14} /></button>
            <button type="button" className="ghost-button" aria-label={"移除参考图 " + (index + 1)} disabled={disabled} onClick={() => onChange(references.filter((_, i) => i !== index))}><X size={14} /></button>
          </div>
        </div>
      ))}
    </div>
  );
}
