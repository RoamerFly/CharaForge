import { useEffect, useRef, useState, type FormEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { FolderOpen, FolderPlus, Loader2, Settings2, X } from "lucide-react";
import { invokeCore, isTauriRuntime } from "../lib/tauri";

export type CharacterTarget = { library: string; name: string };
type CharacterLibrary = { root: string; roles: string[] };

export function CharacterWorkspacePanel({
  target,
  onTarget,
  disabled,
}: {
  target: CharacterTarget | null;
  onTarget: (target: CharacterTarget | null) => void;
  disabled: boolean;
}) {
  const [library, setLibrary] = useState<CharacterLibrary | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const createDialog = useRef<HTMLDialogElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const desktop = isTauriRuntime();

  useEffect(() => {
    if (!desktop) return;
    let active = true;
    setBusy(true);
    void invokeCore<CharacterLibrary | null>("character_library", "character.library").then(
      ({ payload, error }) => {
        if (!active) return;
        setLibrary(payload ?? null);
        setError(error?.message ?? "");
        setBusy(false);
      },
    );
    return () => { active = false; };
  }, [desktop]);

  const chooseLibrary = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const directory = await open({ directory: true, title: "选择角色工坊库或角色库目录" });
      if (typeof directory !== "string") return;
      const response = await invokeCore<CharacterLibrary>("character_library", "character.library", { directory });
      if (!response.payload) throw new Error(response.error?.message ?? "无法读取角色库。");
      setLibrary(response.payload);
      onTarget(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const useDefault = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await invokeCore<CharacterLibrary>("character_library", "character.library", { useDefault: true });
      if (!response.payload) throw new Error(response.error?.message ?? "无法打开默认生图项目。");
      setLibrary(response.payload);
      onTarget(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!library || busy || disabled || !name.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await invokeCore<{ name: string }>("create_character_workspace", "character.create", {
        library: library.root, name: name.trim(),
      });
      if (!response.payload) throw new Error(response.error?.message ?? "无法创建角色。");
      const created = response.payload.name;
      setLibrary({ ...library, roles: [...new Set([...library.roles, created])].sort() });
      onTarget({ library: library.root, name: created });
      setName("");
      setNotice(`已创建 ${created}`);
      createDialog.current?.close();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const showFolder = async (folder: string) => {
    if (!target) return;
    try {
      await invoke("show_item_in_folder", { path: [target.library, target.name, folder].join("/") });
    } catch (cause) {
      setError(String(cause));
    }
  };

  return (
    <section className="character-workspace" aria-label="角色与项目">
      <div className="character-toolbar">
            <label className="character-target-field">
              <span>角色</span>
              <select
                disabled={!library || disabled || busy}
                value={target?.name ?? ""}
                onChange={(event) => {
                  setNotice("");
                  setError("");
                  onTarget(event.target.value && library ? { library: library.root, name: event.target.value } : null);
                }}
              >
                <option value="">{busy ? "正在加载…" : "未指定角色"}</option>
                {library?.roles.map((role) => <option value={role} key={role}>{role}</option>)}
              </select>
            </label>
            <button type="button" className="ghost-button" disabled={!library || disabled || busy} onClick={() => { setError(""); createDialog.current?.showModal(); nameInput.current?.focus(); }}>
              <FolderPlus size={15} aria-hidden="true" />新建角色
            </button>
          {target && (
            <div className="character-folder-actions">
              <button type="button" className="ghost-button" title="打开素材来源" onClick={() => void showFolder("素材来源")}>
                <FolderOpen size={15} aria-hidden="true" /> 素材
              </button>
              <button type="button" className="ghost-button" title="打开参考图" onClick={() => void showFolder("参考图")}>
                <FolderOpen size={15} aria-hidden="true" /> 参考图
              </button>
            </div>
          )}
        <details className="character-project-menu">
          <summary><Settings2 size={15} aria-hidden="true" /> 项目目录</summary>
          <div className="character-project-popover">
            <strong>角色工坊库</strong>
            <span className="character-library-path">{library?.root ?? (desktop ? "正在准备项目" : "请在桌面版选择项目")}</span>
            <div className="generation-inline">
              <button type="button" className="ghost-button" disabled={!desktop || disabled || busy} onClick={() => void chooseLibrary()}>更换目录</button>
              <button type="button" className="ghost-button" disabled={!desktop || disabled || busy} onClick={() => void useDefault()}>使用软件旁的目录</button>
            </div>
            <small>候选图保存到{target ? `「${target.name}」` : "项目"}的 other / 待审核。</small>
          </div>
        </details>
      </div>
      {(error || notice) && <div id="character-workspace-feedback">
        {error && <p className="character-workspace-error" role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
      </div>}
      <dialog ref={createDialog} className="studio-dialog" aria-labelledby="new-character-title">
        <form onSubmit={(event) => void create(event)}>
          <div className="studio-dialog-heading"><h2 id="new-character-title">新建角色</h2><button type="button" className="icon-button" aria-label="关闭新建角色" disabled={busy} onClick={() => createDialog.current?.close()}><X size={18} /></button></div>
          <label className="generation-field" htmlFor="new-generation-character"><span>角色名称</span>
            <input ref={nameInput} id="new-generation-character" type="text" value={name} maxLength={80} disabled={disabled || busy} onChange={(event) => { setName(event.target.value); setError(""); }} />
          </label>
          <p className="studio-dialog-hint">自动准备素材来源、参考图和图集目录。</p>
          {error && <p className="character-workspace-error" role="alert">{error}</p>}
          <div className="studio-dialog-actions"><button type="button" className="ghost-button" disabled={busy} onClick={() => createDialog.current?.close()}>取消</button><button type="submit" className="primary-button" disabled={!name.trim() || disabled || busy}>{busy && <Loader2 size={15} className="spin" />}创建角色</button></div>
        </form>
      </dialog>
    </section>
  );
}
