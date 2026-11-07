import { useEffect, useMemo, useState } from "react";
import { Copy, Loader2, Play } from "lucide-react";
import { invokeCore, isTauriRuntime } from "../lib/tauri";
import { composePrompt, EMPTY_PROMPT, PROMPT_PARTS, type ApiProfile, type CloudResult, type GenerationReference, type SubscriptionAccount } from "../lib/cloud-generation";
import type { CharacterTarget } from "./CharacterWorkspacePanel";
import { CloudChannelSettings } from "./CloudChannelSettings";
import { GenerationReferences } from "./GenerationReferences";
import { GenerationPreview } from "./GenerationPreview";
import { GenerationResourceControls } from "./GenerationResourceControls";
import { GenerationBatchDialog } from "./GenerationBatchDialog";
import { useGenerationBatch } from "../lib/generation-batch";

export function CloudGenerationPanel({ target, onBusy }: { target: CharacterTarget | null; onBusy: (busy: boolean) => void }) {
  const [profiles, setProfiles] = useState<ApiProfile[]>([]);
  const [account, setAccount] = useState<SubscriptionAccount | null>(null);
  const [channel, setChannel] = useState("codex-subscription");
  const [parts, setParts] = useState({ ...EMPTY_PROMPT });
  const [references, setReferences] = useState<GenerationReference[]>([]);
  const [size, setSize] = useState("auto");
  const [quality, setQuality] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [result, setResult] = useState<CloudResult | null>(null);
  const batch = useGenerationBatch(target, onBusy, setError);
  const working = busy || batch.running;
  const prompt = useMemo(() => composePrompt(parts, []), [parts]);
  const finalPrompt = useMemo(() => composePrompt(parts, references), [parts, references]);

  useEffect(() => {
    if (!isTauriRuntime()) return;
    let active = true;
    void invokeCore<ApiProfile[]>("generation_api_profiles", "cloud.profiles").then((r) => {
      if (active) { setProfiles(r.payload ?? []); if (r.error) setError(r.error.message); }
    });
    void invokeCore<SubscriptionAccount>("probe_generation_subscription", "cloud.subscription").then((r) => {
      if (active) { setAccount(r.payload ?? null); if (r.error) setError(r.error.message); }
    });
    return () => { active = false; };
  }, []);

  const generate = async () => {
    if (working || !prompt.trim()) return;
    setBusy(true); onBusy(true); setError(""); setNotice("");
    try {
      const response = await invokeCore<CloudResult>("cloud_generate", "cloud.generate", {
        request: { channel, prompt, prompt_parts: parts, references, size, quality },
        characterTarget: target,
      });
      if (!response.payload) throw new Error(response.error?.message ?? "没有收到图片结果。");
      setResult(response.payload);
      setNotice("图片已保存到待审核目录");
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); onBusy(false); }
  };
  return (
    <div className="cloud-generation-grid">
      <section className="panel-card generation-controls cloud-configuration" aria-label="API 和订阅生图配置">
        <CloudChannelSettings profiles={profiles} onProfiles={setProfiles} channel={channel} onChannel={setChannel} account={account} onAccount={setAccount} disabled={working} onError={setError} />
        <GenerationResourceControls target={target} parts={parts} setParts={setParts} references={references} setReferences={setReferences} disabled={working} onError={setError} onPlan={async (jobs,variant,selection) => { setError(""); await batch.create(jobs,variant,channel,size,quality,selection); setNotice(`已建立 ${jobs.length} 项批次，可在批次与审核中逐项查看后执行。`); }} />
        <GenerationBatchDialog batches={batch.batches} current={batch.current} running={working} onSelect={batch.setActiveId} onRun={b=>void batch.run(b)} onPause={batch.pause} onUpdate={(b,j,a,r,parts)=>void batch.update(b,j,a,r,parts)} onError={setError}/>
        <div className="cloud-prompt-fields">
          {PROMPT_PARTS.filter(([key]) => ["identity", "pose", "scene"].includes(key)).map(([key, title]) => (
            <label key={key} className="generation-field"><span>{title}</span>
              <textarea aria-label={title} rows={3} disabled={working} value={parts[key]} onChange={(e) => setParts({ ...parts, [key]: e.target.value })} />
            </label>
          ))}
        </div>
        <details className="studio-disclosure">
          <summary>表情、镜头与质量约束{[parts.expression, parts.camera, parts.quality].some((value) => value.trim()) && <span className="disclosure-filled">已填写</span>}</summary>
          <div className="cloud-prompt-fields">
            {PROMPT_PARTS.filter(([key]) => ["expression", "camera", "quality"].includes(key)).map(([key, title]) => (
              <label key={key} className="generation-field"><span>{title}</span><textarea aria-label={title} rows={2} disabled={working} value={parts[key]} onChange={(e) => setParts({ ...parts, [key]: e.target.value })} /></label>
            ))}
          </div>
        </details>
        <GenerationReferences references={references} onChange={setReferences} disabled={working} onError={setError} />
        <details className="studio-disclosure">
          <summary>输出设置</summary>
        <div className="generation-grid">
          <label className="generation-field"><span>画幅尺寸</span><select value={size} disabled={busy} onChange={(e) => setSize(e.target.value)}>
            <option value="auto">自动</option><option value="1024x1536">竖图 1024 × 1536</option><option value="1536x1024">横图 1536 × 1024</option><option value="1024x1024">方图 1024 × 1024</option>
          </select></label>
          <label className="generation-field"><span>质量</span><select value={quality} disabled={busy} onChange={(e) => setQuality(e.target.value)}>
            <option value="auto">自动</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option>
          </select></label>
        </div>
        {channel === "codex-subscription" && <small>尺寸和质量通过提示词表达，实际结果以 Codex 返回为准。</small>}
        {profiles.find((p) => p.id === channel)?.protocol === "grok" && <small>Grok JSON 渠道发送提示词与参考图片，尺寸和质量按服务商默认值。</small>}
        </details>
        <details className="studio-disclosure">
          <summary>查看最终提示词</summary>
          <pre className="cloud-final-prompt">{finalPrompt || "填写上方提示词后显示"}</pre>
        </details>
        {error && <p role="alert" className="cloud-error">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        <div className="cloud-submit-bar">
          <button type="button" className="ghost-button" disabled={!finalPrompt} onClick={() => void navigator.clipboard.writeText(finalPrompt).then(() => setNotice("最终提示词已复制")).catch(() => setError("复制失败，请展开最终提示词手动复制。"))}><Copy size={15} /> 复制最终提示词</button>
          <button type="button" className="primary-button" disabled={working || !prompt.trim() || !isTauriRuntime() || (channel === "codex-subscription" && !account?.logged_in)} onClick={() => void generate()}>
            {busy ? <Loader2 size={16} className="spin" /> : <Play size={16} />} {busy ? "正在生成…" : "生成图片"}
          </button>
        </div>
      </section>
      <GenerationPreview path={result?.path ?? batch.current?.jobs.filter(j=>j.result).at(-1)?.result?.path ?? null} busy={working} onError={setError} />
    </div>
  );
}
