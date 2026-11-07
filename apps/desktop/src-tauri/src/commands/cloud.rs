use super::character_workspace::target_root;
use crate::{
    character_workspace::CharacterTarget,
    cloud::{self, codex, profiles, GenerateRequest, GenerationResult, Reference},
    ipc::{core_error, failure, normalize_request_id, success, IpcEnvelope},
    state::AppState,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};
use tauri::{AppHandle, Manager, State};

fn reply<T>(kind: &str, id: String, result: Result<T, String>) -> IpcEnvelope<T> {
    match result {
        Ok(value) => success(kind, id, value),
        Err(error) => failure(
            kind,
            id.clone(),
            core_error(&id, "CLOUD_GENERATION_ERROR", &error, None, false),
        ),
    }
}
#[tauri::command]
pub fn generation_api_profiles(
    state: State<'_, AppState>,
    request_id: Option<String>,
) -> IpcEnvelope<Vec<profiles::Profile>> {
    reply(
        "cloud.profiles",
        normalize_request_id(request_id),
        state
            .database
            .lock()
            .map_err(|_| "无法读取渠道设置。".to_owned())
            .and_then(|db| profiles::list(&db)),
    )
}
#[tauri::command]
pub fn save_generation_api_profile(
    state: State<'_, AppState>,
    profile: profiles::SaveProfile,
    request_id: Option<String>,
) -> IpcEnvelope<Vec<profiles::Profile>> {
    reply(
        "cloud.profile.save",
        normalize_request_id(request_id),
        state
            .database
            .lock()
            .map_err(|_| "无法保存渠道设置。".to_owned())
            .and_then(|db| profiles::save(&db, profile)),
    )
}
#[tauri::command]
pub async fn probe_generation_subscription(
    app: AppHandle,
    request_id: Option<String>,
) -> IpcEnvelope<codex::Account> {
    let result = (|| {
        let state = app.state::<AppState>();
        let db = state.database.lock().map_err(|_| "无法读取代理设置。")?;
        let settings = crate::app_settings::load_app_settings(&db)?;
        Ok::<_, String>(crate::app_settings::proxy_url(&settings.network_proxy).unwrap_or_default())
    })();
    let result = match result {
        Ok(proxy) => tauri::async_runtime::spawn_blocking(move || codex::probe(&proxy))
            .await
            .map_err(|e| e.to_string()),
        Err(error) => Err(error),
    };
    reply(
        "cloud.subscription",
        normalize_request_id(request_id),
        result,
    )
}

pub(super) struct BusyGuard(pub(super) Arc<AtomicBool>);
impl Drop for BusyGuard {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

#[tauri::command]
pub async fn cloud_generate(
    app: AppHandle,
    request: GenerateRequest,
    character_target: Option<CharacterTarget>,
    request_id: Option<String>,
) -> IpcEnvelope<GenerationResult> {
    let id = normalize_request_id(request_id);
    let state = app.state::<AppState>();
    let running = Arc::clone(&state.cloud_generating);
    if running.swap(true, Ordering::AcqRel) {
        return reply(
            "cloud.generate",
            id,
            Err("已有 API / 订阅生图任务正在执行，请等待返回。".into()),
        );
    }
    let _guard = BusyGuard(running);
    let result = run_generation(&app, request, character_target).await;
    reply("cloud.generate", id, result)
}
pub(super) async fn run_generation(
    app: &AppHandle,
    request: GenerateRequest,
    target: Option<CharacterTarget>,
) -> Result<GenerationResult, String> {
    cloud::validate(&request)?;
    let (root, proxy, profile) = {
        let state = app.state::<AppState>();
        let root = target_root(&state, target.as_ref())?;
        let database = state.database.lock().map_err(|_| "无法读取渠道配置。")?;
        let settings = crate::app_settings::load_app_settings(&database)?;
        let profile = if request.channel == "codex-subscription" {
            None
        } else {
            Some(
                profiles::read(&database)?
                    .into_iter()
                    .find(|p| p.id == request.channel)
                    .ok_or("请选择已保存的 API 渠道。")?,
            )
        };
        (
            root,
            crate::app_settings::proxy_url(&settings.network_proxy).unwrap_or_default(),
            profile,
        )
    };
    let images = cloud::read_references(&request.references)?;
    let engine = if profile.is_some() { "api" } else { "codex" };
    let folder = crate::character_workspace::new_generation_folder(
        &root,
        target.as_ref().map(|x| x.name.as_str()),
        engine,
    )
    .map_err(|e| e.to_string())?;
    let references = cloud::snapshot(&images, &folder)?;
    let prompt = cloud::final_prompt(&request);
    let submitted_prompt = if profile.is_none() {
        format!(
            "{}\nRequested size: {}. Quality: {}. Size and quality are preferences.\n",
            prompt, request.size, request.quality
        )
    } else {
        prompt.clone()
    };
    let mut record = json!({
        "channel":request.channel,"profile":profile.as_ref().map(|p|json!({"name":p.name,"base_url":p.base_url,"model":p.model,"protocol":p.protocol})),
        "request":{"prompt":request.prompt,"prompt_parts":request.prompt_parts,"size":request.size,"quality":request.quality},
        "effective_prompt":prompt,"submitted_prompt":submitted_prompt,"references":references,"status":"已请求","requested_at":chrono::Utc::now().to_rfc3339()
    });
    write_record(&folder, &record)?;
    let output = if let Some(profile) = profile {
        cloud::api::generate(&profile, &request, &prompt, &images, &proxy)
            .await
            .map(|out| (out.bytes, out.metadata))
    } else {
        let paths = references
            .as_array()
            .unwrap()
            .iter()
            .map(|r| folder.join(r["snapshot"].as_str().unwrap()))
            .collect::<Vec<_>>();
        let folder = folder.clone();
        tauri::async_runtime::spawn_blocking(move || {
            codex::generate(&submitted_prompt, &paths, &folder, &proxy)
        })
        .await
        .map_err(|e| e.to_string())
        .and_then(|r| r)
    };
    let (bytes, metadata) = match output {
        Ok(value) => value,
        Err(error) => {
            record["status"] = json!("失败");
            record["error"] = json!(cloud::credentials::redact(&error, ""));
            write_record(&folder, &record)?;
            return Err(format!(
                "{error}\n请求记录保存在 {}",
                crate::path_utils::display_path(&folder)
            ));
        }
    };
    let extension = cloud::image_format(&bytes)?.1;
    let path = folder.join(format!("candidate.{extension}"));
    fs::write(&path, &bytes).map_err(|e| e.to_string())?;
    record["status"] = json!("待审核");
    record["output"] = json!({"file":path.file_name().unwrap().to_string_lossy(),"sha256":format!("{:x}",Sha256::digest(&bytes))});
    record["response"] = metadata;
    write_record(&folder, &record)?;
    crate::models::allow_asset_file(app, &path)?;
    Ok(GenerationResult {
        path: crate::path_utils::display_path(&path),
        output_dir: crate::path_utils::display_path(&folder),
        status: "待审核".into(),
    })
}
fn write_record(folder: &Path, record: &Value) -> Result<(), String> {
    let bytes = serde_json::to_vec_pretty(record).map_err(|e| e.to_string())?;
    fs::write(folder.join("request.json"), bytes).map_err(|e| format!("无法保存生图记录。{e}"))
}
#[tauri::command]
pub fn import_generation_reference(
    app: AppHandle,
    path: String,
    purpose: String,
    request_id: Option<String>,
) -> IpcEnvelope<Reference> {
    let result = (|| {
        let reference = Reference { path, purpose };
        let images = cloud::read_references(std::slice::from_ref(&reference))?;
        crate::models::allow_asset_file(&app, &images[0].path)?;
        Ok(Reference {
            path: crate::path_utils::display_path(&images[0].path),
            purpose: reference.purpose,
        })
    })();
    reply("cloud.reference", normalize_request_id(request_id), result)
}
