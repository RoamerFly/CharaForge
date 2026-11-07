import { useState } from "react";
import { Loader2, RefreshCw, Save, Plus, Settings2 } from "lucide-react";
import { invokeCore, isTauriRuntime } from "../lib/tauri";
import { EMPTY_PROFILE, type ApiProfile, type SubscriptionAccount } from "../lib/cloud-generation";

export function CloudChannelSettings({
  profiles, onProfiles, channel, onChannel, account, onAccount, disabled, onError,
}: {
  profiles: ApiProfile[]; onProfiles: (profiles: ApiProfile[]) => void;
  channel: string; onChannel: (channel: string) => void;
  account: SubscriptionAccount | null; onAccount: (account: SubscriptionAccount) => void;
  disabled: boolean; onError: (message: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ ...EMPTY_PROFILE });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const probe = async () => {
    setBusy(true);
    try {
      const response = await invokeCore<SubscriptionAccount>("probe_generation_subscription", "cloud.subscription");
      if (!response.payload) throw new Error(response.error?.message ?? "检测失败。");
      onAccount(response.payload);
    } catch (error) { onError(String(error)); } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true);
    try {
      const response = await invokeCore<ApiProfile[]>("save_generation_api_profile", "cloud.profile.save", {
        profile: { ...draft, id: draft.id || null },
      });
      if (!response.payload) throw new Error(response.error?.message ?? "保存渠道失败。");
      onProfiles(response.payload);
      onChannel(draft.id || response.payload[response.payload.length - 1].id);
      setDraft({ ...EMPTY_PROFILE });
      setEditing(false);
      setNotice("API 渠道已保存");
    } catch (error) { onError(String(error)); } finally { setBusy(false); }
  };
  return (
    <section className="cloud-channels">
      <div className="cloud-channel-toolbar">
      <label className="generation-field">
        <span>生图渠道</span>
        <select value={channel} disabled={disabled || busy} onChange={(e) => { onChannel(e.target.value); setNotice(""); }}>
          <option value="codex-subscription">Codex · 本机订阅</option>
          {profiles.map((p) => <option value={p.id} key={p.id}>{p.name} · {p.model}</option>)}
        </select>
      </label>
      <div className="generation-inline">
        <button type="button" className="icon-button" aria-label="新增 API" title="新增 API" disabled={disabled || busy || !isTauriRuntime()} onClick={() => { setDraft({ ...EMPTY_PROFILE }); setEditing(!editing); }}><Plus size={16} /></button>
        {profiles.some((p) => p.id === channel) && (
          <button type="button" className="icon-button" aria-label="编辑渠道" title="编辑渠道" disabled={disabled || busy} onClick={() => {
            setDraft({ ...EMPTY_PROFILE, ...profiles.find((p) => p.id === channel)! }); setEditing(true);
          }}><Settings2 size={16} /></button>
        )}
      </div>
      </div>
      {channel === "codex-subscription" && (
        <div className="cloud-account" aria-live="polite">
          <div className="generation-inline">
            <span className="cloud-account-name" title={account?.name}>{account?.logged_in ? account.name : "本机 Codex 登录"}</span>
            {account?.logged_in && <span className="cloud-plan">{account.plan}</span>}
            <button type="button" className="icon-button" aria-label="检测登录" title="重新检测本机登录" disabled={disabled || busy || !isTauriRuntime()} onClick={() => void probe()}>
              {busy ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
            </button>
          </div>
          {!account?.logged_in && <small>{account?.message ?? "正在检测本机登录…"}</small>}
          {account?.logged_in && <details className="cloud-login-details"><summary>登录详情</summary><small>{account.message}</small><small className="cloud-path">{account.home} · config.toml {account.config_exists ? "已发现" : "未发现"} · auth.json {account.auth_file_exists ? "已发现" : "由系统凭据管理"}</small></details>}
        </div>
      )}
      {editing && (
        <fieldset disabled={disabled || busy} className="cloud-profile-form">
          <legend>{draft.id ? "编辑 API 渠道" : "新增 API 渠道"}</legend>
          <label className="generation-field"><span>渠道名称</span><input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <label className="generation-field"><span>API 地址（含 /v1）</span><input value={draft.base_url} onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} /></label>
          <label className="generation-field"><span>模型</span><input value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} /></label>
          <label className="generation-field"><span>请求协议</span><select value={draft.protocol} onChange={(e) => setDraft({ ...draft, protocol: e.target.value })}>
            <option value="images">OpenAI Images 兼容接口</option><option value="grok">Grok JSON 参考图接口</option>
          </select></label>
          <label className="generation-field"><span>API Key{draft.id ? "（留空保留）" : ""}</span><input type="password" autoComplete="new-password" value={draft.key} onChange={(e) => setDraft({ ...draft, key: e.target.value })} /></label>
          <div className="generation-inline">
            <button type="button" className="primary-button" disabled={!draft.name.trim() || !draft.model.trim() || !draft.base_url.trim()} onClick={() => void save()}><Save size={14} /> 保存渠道</button>
            <button type="button" className="ghost-button" onClick={() => { setEditing(false); setDraft({ ...EMPTY_PROFILE }); }}>取消</button>
          </div>
        </fieldset>
      )}
      {notice && <small role="status">{notice}</small>}
    </section>
  );
}
