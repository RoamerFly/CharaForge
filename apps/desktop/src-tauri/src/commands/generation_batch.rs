use super::{
    character_workspace::target_root,
    cloud::{run_generation, BusyGuard},
};
use crate::{
    character_workspace::CharacterTarget,
    generation_batch::{self as batch, Batch, JobInput},
    ipc::{core_error, failure, normalize_request_id, success, IpcEnvelope},
    state::AppState,
};
use std::sync::{atomic::Ordering, Arc};
use tauri::{AppHandle, Manager};
fn reply<T>(id: String, result: Result<T, String>) -> IpcEnvelope<T> {
    match result {
        Ok(v) => success("generation.batch", id, v),
        Err(e) => failure(
            "generation.batch",
            id.clone(),
            core_error(&id, "GENERATION_BATCH_ERROR", &e, None, false),
        ),
    }
}
fn guard(app: &AppHandle) -> Result<BusyGuard, String> {
    let running = Arc::clone(&app.state::<AppState>().cloud_generating);
    if running.swap(true, Ordering::AcqRel) {
        Err("已有生图请求正在执行，请等待当前图片返回。".into())
    } else {
        Ok(BusyGuard(running))
    }
}
#[tauri::command]
pub async fn generation_batch_create(
    app: AppHandle,
    character_target: CharacterTarget,
    variant: String,
    jobs: Vec<JobInput>,
    selection: Option<serde_json::Value>,
    request_id: Option<String>,
) -> IpcEnvelope<Batch> {
    let id = normalize_request_id(request_id);
    let result = async {
        let _guard = guard(&app)?;
        let root = target_root(&app.state::<AppState>(), Some(&character_target))?;
        let name = character_target.name;
        let output_root = root.clone();
        let (_, created) = tauri::async_runtime::spawn_blocking(move || {
            batch::create(&root, &name, variant, jobs, selection.unwrap_or_default())
        })
        .await
        .map_err(|e| e.to_string())??;
        batch::display(&output_root, created)
    }
    .await;
    reply(id, result)
}
#[tauri::command]
pub fn generation_batch_list(
    app: AppHandle,
    character_target: CharacterTarget,
    request_id: Option<String>,
) -> IpcEnvelope<Vec<Batch>> {
    let result = (|| {
        let read_guard = guard(&app).ok();
        let idle = read_guard.is_some();
        let state = app.state::<AppState>();
        let root = target_root(&state, Some(&character_target))?;
        let role = batch::child(&root, &character_target.name)?;
        let parent = role.join("other/批量生成");
        if !parent.exists() {
            return Ok(vec![]);
        }
        let parent = batch::child(&role, "other/批量生成")?;
        let mut batches = Vec::new();
        for entry in std::fs::read_dir(parent).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
                continue;
            }
            if !entry.path().join("batch.json").is_file() {
                continue;
            }
            let path = batch::folder(
                &root,
                &character_target.name,
                &entry.file_name().to_string_lossy(),
            )?;
            let b = batch::display(&root, batch::load(&path, idle)?)?;
            for job in &b.jobs {
                if let Some(r) = &job.result {
                    crate::models::allow_asset_file(&app, std::path::Path::new(&r.path))?;
                }
            }
            batches.push(b);
        }
        batches.sort_by(|a, b| b.created_at.cmp(&a.created_at));
        Ok(batches)
    })();
    reply(normalize_request_id(request_id), result)
}
#[tauri::command]
pub async fn generation_batch_next(
    app: AppHandle,
    character_target: CharacterTarget,
    batch_id: String,
    request_id: Option<String>,
) -> IpcEnvelope<Batch> {
    let id = normalize_request_id(request_id);
    let result = async {
        let _guard = guard(&app)?;
        let root = target_root(&app.state::<AppState>(), Some(&character_target))?;
        let path = batch::folder(&root, &character_target.name, &batch_id)?;
        let mut b = batch::load(&path, true)?;
        if let Some(i) = batch::next(&b)? {
            let request = batch::request(&path, &b.jobs[i])?;
            b.jobs[i].status = "running".into();
            b.jobs[i].attempts += 1;
            batch::save(&path, &b)?;
            match run_generation(&app, request, Some(character_target)).await {
                Ok(result) => {
                    b.jobs[i].result = Some(batch::relative_result(&root, result)?);
                    b.jobs[i].status = "succeeded".into();
                    b.jobs[i].error = None;
                }
                Err(e) => {
                    b.jobs[i].status = if [
                        "未知",
                        "超时",
                        "读取生图响应中断",
                        "生图请求失败",
                        "图片下载失败",
                        "下载生成图失败",
                        "没有可用图片数据",
                    ]
                    .iter()
                    .any(|marker| e.contains(marker))
                    {
                        "uncertain"
                    } else {
                        "failed"
                    }
                    .into();
                    b.jobs[i].error = Some(crate::cloud::credentials::redact(&e, ""));
                }
            }
            batch::save(&path, &b)?;
        }
        batch::display(&root, b)
    }
    .await;
    reply(id, result)
}
#[tauri::command]
pub fn generation_batch_update(
    app: AppHandle,
    character_target: CharacterTarget,
    batch_id: String,
    job_id: String,
    action: String,
    reason: String,
    prompt_parts: Option<std::collections::BTreeMap<String, String>>,
    request_id: Option<String>,
) -> IpcEnvelope<Batch> {
    let result = (|| {
        let _guard = guard(&app)?;
        let root = target_root(&app.state::<AppState>(), Some(&character_target))?;
        let path = batch::folder(&root, &character_target.name, &batch_id)?;
        let mut b = batch::load(&path, true)?;
        let i = b
            .jobs
            .iter()
            .position(|j| j.input.id == job_id)
            .ok_or("任务不存在。")?;
        match action.as_str() {
            "edit_prompt" => {
                batch::edit_prompt(&mut b.jobs[i], prompt_parts.ok_or("缺少提示词分区。")?)?
            }
            "retry" => batch::retry(&mut b.jobs[i])?,
            "cancel" => {
                if b.jobs[i].status != "queued" {
                    return Err("只能取消尚未发出的请求。".into());
                }
                b.jobs[i].status = "cancelled".into();
            }
            "pending" | "approved" | "repair" => batch::review(&root, &mut b, i, &action, &reason)?,
            _ => return Err("无效的批次操作。".into()),
        }
        batch::save(&path, &b)?;
        batch::display(&root, b)
    })();
    reply(normalize_request_id(request_id), result)
}
