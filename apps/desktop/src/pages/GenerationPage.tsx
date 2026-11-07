import { lazy, Suspense, useRef, useState, type KeyboardEvent } from "react";
import { Cloud, Cpu } from "lucide-react";
import { CharacterWorkspacePanel, type CharacterTarget } from "../components/CharacterWorkspacePanel";
import { CloudGenerationPanel } from "../components/CloudGenerationPanel";
const ComfyGenerationPanel = lazy(() => import("../components/ComfyGenerationPanel").then((module) => ({ default: module.ComfyGenerationPanel })));

type GenerationTab = "cloud" | "local";

export function GenerationPage() {
  const [tab, setTab] = useState<GenerationTab>("cloud");
  const [target, setTarget] = useState<CharacterTarget | null>(null);
  const [cloudBusy, setCloudBusy] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [localVisited, setLocalVisited] = useState(false);
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);
  const busy = cloudBusy || localBusy;
  const choose = (next: GenerationTab) => {
    setTab(next);
    if (next === "local") setLocalVisited(true);
  };
  const onTabKey = (event: KeyboardEvent, index: number) => {
    if (busy || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index;
    choose(next === 0 ? "cloud" : "local");
    tabs.current[next]?.focus();
  };
  return (
    <div className="page-stack generation-page">
      <CharacterWorkspacePanel target={target} onTarget={setTarget} disabled={busy} />
      <div className="generation-tabs" role="tablist" aria-label="生图方式">
        {(["cloud", "local"] as const).map((name, index) => (
          <button key={name} type="button" role="tab" id={"generation-tab-" + name}
            aria-selected={tab === name} aria-controls={"generation-panel-" + name}
            tabIndex={tab === name ? 0 : -1} disabled={busy}
            ref={(element) => { tabs.current[index] = element; }}
            onClick={() => choose(name)} onKeyDown={(event) => onTabKey(event, index)}>
            {name === "cloud" ? <Cloud size={17} aria-hidden="true" /> : <Cpu size={17} aria-hidden="true" />}
            {name === "cloud" ? "API / 订阅生图" : "本地 ComfyUI 生图"}
          </button>
        ))}
      </div>
      <div id="generation-panel-cloud" role="tabpanel" aria-labelledby="generation-tab-cloud" hidden={tab !== "cloud"}>
        <CloudGenerationPanel target={target} onBusy={setCloudBusy} />
      </div>
      <div id="generation-panel-local" role="tabpanel" aria-labelledby="generation-tab-local" hidden={tab !== "local"}>
        {localVisited && <Suspense fallback={<p role="status">正在加载本地生图…</p>}><ComfyGenerationPanel target={target} onBusy={setLocalBusy} /></Suspense>}
      </div>
    </div>
  );
}
