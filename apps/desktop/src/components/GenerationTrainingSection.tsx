import { useCallback, useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { AlertCircle, FolderOpen, ImagePlus, Loader2 } from "lucide-react";
import type {
  IdentitySampleSummary,
  LoraCropMode,
  LoraExportCandidates,
  LoraExportResult,
  LoraQualityPreset,
} from "@anime-pic-manage/shared-types";
import { invokeCore, isTauriRuntime } from "../lib/tauri";
import { TrainingPanel } from "./TrainingPanel";

export function GenerationTrainingSection({
  busy,
  runExportAction,
  onNotice: setNotice,
  onError: setError,
}: {
  busy: string | null;
  runExportAction: (action: () => Promise<void>) => Promise<void>;
  onNotice: (notice: string | null) => void;
  onError: (error: string | null) => void;
}) {
  const [candidates, setCandidates] = useState<IdentitySampleSummary[]>([]);
  const [datasetDir, setDatasetDir] = useState("");
  const [exportIdentity, setExportIdentity] = useState("");
  const [exportTrigger, setExportTrigger] = useState("");
  const [cropMode, setCropMode] = useState<LoraCropMode>("person");
  const [qualityPreset, setQualityPreset] = useState<LoraQualityPreset>("pony");
  const [exportResolution, setExportResolution] = useState(1024);
  const [maxImages, setMaxImages] = useState(40);
  const [repeats, setRepeats] = useState(10);
  const [keepTokens, setKeepTokens] = useState(4);
  const [exportResult, setExportResult] = useState<LoraExportResult | null>(null);
  const [trainDatasetToml, setTrainDatasetToml] = useState("");
  const [trainOutputName, setTrainOutputName] = useState("");

  const refreshCandidates = useCallback(async () => {
    const response = await invokeCore<LoraExportCandidates>(
      "get_lora_export_candidates",
      "dataset.candidates",
    );
    if (!response.payload) return;
    setCandidates(response.payload.identities);
    setDatasetDir((current) => current || response.payload!.default_output_dir);
    const first = response.payload.identities[0];
    if (first) {
      setExportIdentity((current) => current || first.identity_id);
      setExportTrigger(
        (current) => current || first.label_name.toLowerCase().replace(/ /g, "_"),
      );
    }
  }, []);

  useEffect(() => {
    if (!isTauriRuntime()) return;
    void refreshCandidates();
  }, [refreshCandidates]);

  const pickDatasetDir = async () => {
    const selected = await open({
      directory: true,
      multiple: false,
      title: "选择 LoRA 训练集输出目录",
    });
    if (typeof selected === "string" && selected.trim()) {
      setDatasetDir(selected.trim());
    }
  };

  const runExport = () =>
    runExportAction(async () => {
      if (!exportIdentity) {
        setError("请先选择一个有标注样本的角色。");
        return;
      }
      setExportResult(null);
      setNotice("正在导出训练集并调用 WD14 打标，首次运行需要下载打标模型…");
      const response = await invokeCore<LoraExportResult>(
        "export_lora_dataset",
        "dataset.export",
        {
          options: {
            identity_id: exportIdentity,
            trigger: exportTrigger,
            output_dir: datasetDir,
            crop_mode: cropMode,
            resolution: exportResolution,
            max_images: maxImages,
            quality_preset: qualityPreset,
            repeats,
            keep_tokens: keepTokens,
            shuffle_caption: true,
            drop_character_tags: true,
          },
        },
      );
      if (response.error || !response.payload) {
        setNotice(null);
        setError(response.error?.message ?? "训练集导出失败。");
        return;
      }
      setExportResult(response.payload);
      setTrainDatasetToml(response.payload.dataset_toml);
      setTrainOutputName(
        response.payload.trigger ||
          response.payload.train_dir.split(/[\\/]/).filter(Boolean).pop() ||
          "lora",
      );
      setNotice(
        `训练集已导出：保留 ${response.payload.kept} 张，跳过 ${response.payload.skipped} 张。`,
      );
    });

  return (
    <>
      <section className="panel-card generation-dataset">
        <div className="generation-results-heading">
          <strong>训练集导出（角色 LoRA）</strong>
          <small>
            从人工矫正后的标注里导出该角色的训练数据：按人物框裁剪 → 去重 → WD14
            打标 → 生成 kohya 配置
          </small>
        </div>

        {candidates.length === 0 ? (
          <div className="banner-callout banner-info">
            <AlertCircle size={16} />
            <span>
              还没有已标注的角色样本。请先在「角色识别」里完成识别与人工矫正，再回来导出。
            </span>
          </div>
        ) : (
          <>
            <div className="generation-grid">
              <label className="generation-field">
                <span>角色（按标注样本数排序）</span>
                <select
                  value={exportIdentity}
                  onChange={(event) => {
                    setExportIdentity(event.target.value);
                    const picked = candidates.find(
                      (item) => item.identity_id === event.target.value,
                    );
                    if (picked) {
                      setExportTrigger(picked.label_name.toLowerCase().replace(/ /g, "_"));
                    }
                  }}
                >
                  {candidates.map((item) => (
                    <option key={item.identity_id} value={item.identity_id}>
                      {item.display_name}（{item.image_count} 张，矫正 {item.manual_count}）
                    </option>
                  ))}
                </select>
              </label>
              <label className="generation-field">
                <span>触发词</span>
                <input
                  type="text"
                  value={exportTrigger}
                  onChange={(event) => setExportTrigger(event.target.value)}
                  placeholder="例如 character_alpha"
                />
              </label>
              <label className="generation-field">
                <span>裁剪方式</span>
                <select
                  value={cropMode}
                  onChange={(event) => setCropMode(event.target.value as LoraCropMode)}
                >
                  <option value="person">按人物框（推荐，自动去背景杂物）</option>
                  <option value="head">头部特写</option>
                  <option value="bust">半身</option>
                  <option value="large">人物框 + 少量环境</option>
                  <option value="none">整图不裁剪</option>
                </select>
              </label>
              <label className="generation-field">
                <span>质量标签前缀（按基座选择）</span>
                <select
                  value={qualityPreset}
                  onChange={(event) =>
                    setQualityPreset(event.target.value as LoraQualityPreset)
                  }
                >
                  <option value="pony">Pony：score_9,score_8_up,score_7_up</option>
                  <option value="illustrious">Illustrious：masterpiece,best quality…</option>
                  <option value="sd15">SD1.5：masterpiece,best quality</option>
                  <option value="none">不加前缀</option>
                </select>
              </label>
            </div>

            <div className="generation-grid">
              <label className="generation-field">
                <span>训练分辨率</span>
                <select
                  value={exportResolution}
                  onChange={(event) => setExportResolution(Number(event.target.value))}
                >
                  <option value={512}>512（SD1.5，6GB 显存最稳）</option>
                  <option value={768}>768（SDXL 省显存）</option>
                  <option value={1024}>1024（SDXL 标准）</option>
                </select>
              </label>
              <label className="generation-field">
                <span>最多导出张数 / repeats</span>
                <div className="generation-inline">
                  <input
                    type="number"
                    min={1}
                    max={200}
                    value={maxImages}
                    onChange={(event) => setMaxImages(Number(event.target.value))}
                  />
                  <input
                    type="number"
                    min={1}
                    max={100}
                    value={repeats}
                    onChange={(event) => setRepeats(Number(event.target.value))}
                  />
                </div>
              </label>
              <label className="generation-field">
                <span>keep_tokens（保护前缀+触发词）</span>
                <input
                  type="number"
                  min={0}
                  max={20}
                  value={keepTokens}
                  onChange={(event) => setKeepTokens(Number(event.target.value))}
                />
              </label>
              <label className="generation-field">
                <span>输出目录</span>
                <div className="generation-inline">
                  <input
                    type="text"
                    value={datasetDir}
                    onChange={(event) => setDatasetDir(event.target.value)}
                  />
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => void pickDatasetDir()}
                  >
                    <FolderOpen size={15} /> 选择
                  </button>
                </div>
              </label>
            </div>

            <div className="generation-actions">
              <button
                className="primary-button"
                onClick={() => void runExport()}
                disabled={busy !== null}
              >
                {busy === "export" ? (
                  <Loader2 size={16} className="spin" />
                ) : (
                  <ImagePlus size={16} />
                )}
                导出训练集
              </button>
              {exportResult && (
                <button
                  className="ghost-button"
                  onClick={() =>
                    void invokeCore<void>("show_item_in_folder", "system.explorer.show", {
                      path: exportResult.train_dir,
                    })
                  }
                >
                  <FolderOpen size={15} /> 打开训练集目录
                </button>
              )}
              {exportResult && (
                <span className="generation-progress-text">
                  {exportResult.kept} 张已导出 · {exportResult.skipped} 张被跳过 · 触发词{" "}
                  <code>{exportResult.trigger}</code>
                </span>
              )}
            </div>

            {exportResult && (
              <div className="generation-caption-preview">
                <strong>caption 抽样（可直接检查打标质量）</strong>
                {exportResult.samples.map((sample) => (
                  <div key={sample.image} className="generation-caption-row">
                    <code>{sample.image.split(/[\\/]/).pop()}</code>
                    <span>{sample.caption}</span>
                  </div>
                ))}
                {exportResult.skip_reasons.length > 0 && (
                  <small>
                    跳过示例：
                    {exportResult.skip_reasons
                      .slice(0, 3)
                      .map((item) => `${item.path.split(/[\\/]/).pop()}（${item.reason}）`)
                      .join("；")}
                  </small>
                )}
              </div>
            )}
          </>
        )}
      </section>

      <TrainingPanel
        datasetToml={trainDatasetToml}
        outputName={trainOutputName}
        onNotice={setNotice}
        onError={setError}
      />

    </>
  );
}
