import { useState } from "react";
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { FolderOpen, ImagePlus, Loader2, Maximize2 } from "lucide-react";
import { isTauriRuntime, normalizePreviewPath } from "../lib/tauri";
import { ImagePreviewDialog } from "./ImagePreviewDialog";

export function GenerationPreview({ path, busy = false, onError }: { path: string | null; busy?: boolean; onError: (text: string) => void }) {
  const [full, setFull] = useState(false);
  const source = path && (isTauriRuntime() ? convertFileSrc(normalizePreviewPath(path)) : path);
  return (
    <section className="panel-card cloud-preview">
      <div className="cloud-preview-heading">
        <strong>{path ? "生成结果 · 待审核" : "图片预览"}</strong>
        {path && (
          <div className="generation-inline">
            <button className="ghost-button" type="button" onClick={() => void invoke("show_item_in_folder", { path }).catch((error) => onError(String(error)))}>
              <FolderOpen size={15} aria-hidden="true" /> 定位文件
            </button>
            <button className="ghost-button" type="button" onClick={() => setFull(true)}>
              <Maximize2 size={15} aria-hidden="true" /> 全屏
            </button>
          </div>
        )}
      </div>
      <div className="cloud-preview-canvas">
        {source ? <img src={source} alt="本次生成的待审核候选" onDoubleClick={() => setFull(true)} /> : !busy && <div className="cloud-preview-empty"><ImagePlus size={36} strokeWidth={1.2} aria-hidden="true" /><p>填写提示词，开始创作</p></div>}
        {busy && <div className="cloud-preview-wait" role="status"><Loader2 className="spin" size={22} aria-hidden="true" /><span>正在生成图片…</span></div>}
      </div>
      <ImagePreviewDialog source={full ? source : null} title="生成结果 · 待审核" onClose={() => setFull(false)} />
    </section>
  );
}
