import { useCallback, useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { Activity, Cpu, ImagePlus, SlidersHorizontal, LoaderCircle, FolderCog } from "lucide-react";
import type { AppSettings, ComfyModelList, ComfyStatus, KohyaEnvironment, KohyaStatus } from "@anime-pic-manage/shared-types";
import { invokeCore } from "../lib/tauri";

export function ComfySettingsPanel({
  settings,
  onChange,
}: {
  settings: AppSettings;
  onChange: (patch: Partial<AppSettings>) => void;
}) {
  const [status, setStatus] = useState<ComfyStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [models, setModels] = useState<ComfyModelList | null>(null);

  const check = useCallback(async () => {
    setChecking(true);
    const response = await invokeCore<ComfyStatus>("comfy_status", "comfy.status");
    if (response.payload) {
      setStatus(response.payload);
      if (response.payload.running) {
        const list = await invokeCore<ComfyModelList>("comfy_models", "comfy.models");
        setModels(list.payload ?? null);
      }
    }
    setChecking(false);
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  const pickDirectory = async () => {
    const selected = await open({
      directory: true,
      multiple: false,
      title: "选择 ComfyUI 安装目录（含 ComfyUI 与 python 的文件夹）",
    });
    if (typeof selected === "string" && selected.trim()) {
      onChange({ comfy_root: selected.trim() });
    }
  };

  const statusText = !status
    ? "尚未检测"
    : !status.configured
      ? "未配置目录"
      : !status.installed
        ? status.error ?? "目录无效"
        : status.running
          ? `运行中${status.version ? ` · v${status.version}` : ""}${
              status.device ? ` · ${status.device}` : ""
            }${status.vram_gb ? ` · ${status.vram_gb} GB 显存` : ""}`
          : `已停止${status.version ? ` · v${status.version}` : ""}`;

  return (
    <section className="settings-section">
      <div className="settings-heading">
        <div className="settings-heading-icon">
          <ImagePlus size={19} />
        </div>
        <div>
          <h3>AI 生图（ComfyUI）</h3>
        </div>
      </div>

      <div className="settings-field">
        <div>
          <strong>安装目录</strong>
          <small>选择包含 ComfyUI 与 python 的目录。</small>
        </div>
      </div>
      <div className="generation-inline settings-path-control">
        <input
          type="text"
          className="settings-text-input"
          aria-label="ComfyUI 安装目录"
          value={settings.comfy_root}
          onChange={(event) => onChange({ comfy_root: event.target.value })}
          placeholder="ComfyUI 安装目录"
        />
        <button type="button" className="ghost-button" onClick={() => void pickDirectory()}>
          <FolderCog size={15} /> 选择目录
        </button>
      </div>

      <div className="settings-field">
        <div>
          <strong>端口</strong>
          <small>默认 8188。</small>
        </div>
        <input
          type="number"
          min={1024}
          max={65535}
          className="settings-text-input"
          value={settings.comfy_port}
          onChange={(event) =>
            onChange({ comfy_port: Number(event.target.value) })
          }
        />
      </div>

      <div className="settings-field">
        <div>
          <strong>低显存模式（--lowvram）</strong>
          <small>显存 ≤ 8GB 建议开启。</small>
        </div>
        <label className="switch-control">
          <input
            type="checkbox"
            checked={settings.comfy_low_vram}
            onChange={(event) =>
              onChange({ comfy_low_vram: event.target.checked })
            }
          />
          <span>{settings.comfy_low_vram ? "已启用" : "已关闭"}</span>
        </label>
      </div>

      <div className="settings-field">
        <div>
          <strong>随应用自动启动</strong>
          <small>进入本地生图页签时启动。</small>
        </div>
        <label className="switch-control">
          <input
            type="checkbox"
            checked={settings.comfy_auto_start}
            onChange={(event) =>
              onChange({ comfy_auto_start: event.target.checked })
            }
          />
          <span>{settings.comfy_auto_start ? "已启用" : "已关闭"}</span>
        </label>
      </div>

      <div className="settings-field settings-field-stacked">
        <div>
          <strong>当前状态</strong>
          <small>{statusText}</small>
        </div>
        <div className="generation-inline">
          <button
            type="button"
            className="ghost-button"
            onClick={() => void check()}
            disabled={checking}
          >
            {checking ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Activity size={15} />
            )}
            检测
          </button>
          {models && (
            <span className="muted">
              发现 {models.checkpoints.length} 个 checkpoint、{models.loras.length} 个 LoRA
            </span>
          )}
        </div>
      </div>

      <details className="studio-disclosure"><summary>许可证说明</summary><div className="settings-field-note">
        ComfyUI 采用 GPL-3.0 许可，本应用只调用你本机已安装的实例，不打包、不修改它的代码；生图与训练所需的模型与 LoRA 许可证由各自上游决定。
      </div></details>
    </section>
  );
}

export function LoraTrainingSettingsPanel({
  settings,
  onChange,
}: {
  settings: AppSettings;
  onChange: (patch: Partial<AppSettings>) => void;
}) {
  const [status, setStatus] = useState<KohyaStatus | null>(null);
  const [environment, setEnvironment] = useState<KohyaEnvironment | null>(null);
  const [checking, setChecking] = useState(false);
  const [probing, setProbing] = useState(false);

  const check = useCallback(async () => {
    setChecking(true);
    const response = await invokeCore<KohyaStatus>("kohya_status", "kohya.status");
    setStatus(response.payload ?? null);
    setChecking(false);
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  const probe = async () => {
    setProbing(true);
    const response = await invokeCore<KohyaEnvironment>(
      "kohya_probe_environment",
      "kohya.probe",
    );
    setEnvironment(response.payload ?? null);
    setProbing(false);
  };

  const pickPath = async (key: keyof AppSettings, mode: "directory" | "file") => {
    const selected = await open({
      directory: mode === "directory",
      multiple: false,
      title:
        mode === "directory"
          ? key === "lora_trainer_root"
            ? "选择 kohya_ss 或 sd-scripts 目录"
            : "选择 LoRA 输出目录"
          : key === "lora_trainer_python"
            ? "选择训练用的 python.exe"
            : "选择训练底模（.safetensors）",
    });
    if (typeof selected === "string" && selected.trim()) {
      onChange({ [key]: selected.trim() } as Partial<AppSettings>);
    }
  };

  const statusText = !status
    ? "尚未检测"
    : !status.configured
      ? status.error ?? "未配置"
      : !status.installed
        ? status.error ?? "找不到可用的 Python"
        : `已就绪 · ${status.python_source} · ${status.trainers.join(" / ")}`;

  const environmentText = !environment
    ? null
    : environment.error
      ? `环境异常：${environment.error}`
      : `Python ${environment.python_version ?? "?"} · torch ${environment.torch_version ?? "未安装"}${
          environment.cuda_available
            ? ` · CUDA ${environment.cuda_version ?? "?"} ${environment.device_name ?? ""}`
            : " · 仅 CPU"
        }${environment.bitsandbytes_version ? ` · bitsandbytes ${environment.bitsandbytes_version}` : ""}`;

  return (
    <section className="settings-section">
      <div className="settings-heading">
        <div className="settings-heading-icon">
          <SlidersHorizontal size={19} />
        </div>
        <div>
          <h3>LoRA 训练（kohya / sd-scripts）</h3>
        </div>
      </div>

      <div className="settings-field settings-field-stacked">
        <div>
          <strong>训练器目录</strong>
          <small>
            选择含 train_network.py 或 sdxl_train_network.py 的训练器目录。
          </small>
        </div>
        <div className="generation-inline">
          <input
            type="text"
            className="settings-text-input settings-text-input-wide"
            value={settings.lora_trainer_root}
            placeholder="E:\\path\\to\\kohya_ss"
            onChange={(event) => onChange({ lora_trainer_root: event.target.value })}
          />
          <button
            type="button"
            className="ghost-button"
            onClick={() => void pickPath("lora_trainer_root", "directory")}
          >
            选择目录
          </button>
        </div>
      </div>

      <div className="settings-field settings-field-stacked">
        <div>
          <strong>训练用 Python</strong>
          <small>留空使用训练器 venv 或系统 Python。</small>
        </div>
        <div className="generation-inline">
          <input
            type="text"
            className="settings-text-input settings-text-input-wide"
            value={settings.lora_trainer_python}
            placeholder="留空自动探测"
            onChange={(event) => onChange({ lora_trainer_python: event.target.value })}
          />
          <button
            type="button"
            className="ghost-button"
            onClick={() => void pickPath("lora_trainer_python", "file")}
          >
            选择文件
          </button>
        </div>
      </div>

      <div className="settings-field settings-field-stacked">
        <div>
          <strong>训练底模</strong>
          <small>
            必须是 <code>.safetensors</code>，且与要训练的 LoRA 家族一致（SD1.5 的 LoRA 不能配 SDXL 底模）。
          </small>
        </div>
        <div className="generation-inline">
          <input
            type="text"
            className="settings-text-input settings-text-input-wide"
            value={settings.lora_trainer_base_model}
            placeholder="E:\\models\\anime.safetensors"
            onChange={(event) => onChange({ lora_trainer_base_model: event.target.value })}
          />
          <button
            type="button"
            className="ghost-button"
            onClick={() => void pickPath("lora_trainer_base_model", "file")}
          >
            选择文件
          </button>
        </div>
      </div>

      <div className="settings-field settings-field-stacked">
        <div>
          <strong>LoRA 输出目录</strong>
          <small>留空则输出到训练集同级的 <code>lora-models</code>。</small>
        </div>
        <div className="generation-inline">
          <input
            type="text"
            className="settings-text-input settings-text-input-wide"
            value={settings.lora_trainer_output_dir}
            placeholder="留空使用默认目录"
            onChange={(event) => onChange({ lora_trainer_output_dir: event.target.value })}
          />
          <button
            type="button"
            className="ghost-button"
            onClick={() => void pickPath("lora_trainer_output_dir", "directory")}
          >
            选择目录
          </button>
        </div>
      </div>

      <div className="settings-field settings-field-stacked">
        <div>
          <strong>训练器状态</strong>
          <small>{statusText}</small>
          {environmentText && <small>{environmentText}</small>}
        </div>
        <div className="generation-inline">
          <button
            type="button"
            className="ghost-button"
            onClick={() => void check()}
            disabled={checking}
          >
            {checking ? <LoaderCircle size={15} className="spin" /> : <Activity size={15} />}
            检测训练器
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={() => void probe()}
            disabled={probing || !status?.installed}
          >
            {probing ? <LoaderCircle size={15} className="spin" /> : <Cpu size={15} />}
            检查 torch / CUDA
          </button>
        </div>
      </div>

      <details className="studio-disclosure"><summary>许可证说明</summary><div className="settings-field-note">
        kohya_ss 与 sd-scripts 是独立项目（各有自己的许可证），本应用只调用它们、不打包也不修改；
        训练产生的 LoRA 版权与使用限制由训练者与底模许可证共同决定。
      </div></details>
    </section>
  );
}

/**
 * Character recognizer inventory: pick the active model and install extra
 * ones from the shipped catalog (the Worker performs the download).
 */
