use crate::character_workspace::{self, CharacterLibrary, CharacterWorkspace, LIBRARY_SETTING};
use crate::ipc::{core_error, failure, normalize_request_id, success, IpcEnvelope};
use crate::state::AppState;
use std::path::{Path, PathBuf};
use tauri::State;

pub(super) fn saved_library(state: &AppState) -> Result<Option<PathBuf>, String> {
    let configured = {
        let database = state.database.lock().map_err(|_| "无法读取本地设置。")?;
        database
            .get_setting_string(LIBRARY_SETTING)
            .map_err(|error| error.to_string())?
    };
    if let Some(path) = configured.filter(|path| !path.trim().is_empty()) {
        return Ok(Some(PathBuf::from(path)));
    }
    let data_dir = Path::new(&state.data_dir);
    let app_dir = data_dir.parent().ok_or("无法定位软件所在目录。")?;
    character_workspace::portable_library(app_dir)
        .map(Some)
        .map_err(|error| format!("无法准备软件旁的生图项目，请选择可写目录。{error}"))
}

#[tauri::command]
pub fn character_library(
    state: State<'_, AppState>,
    directory: Option<String>,
    use_default: Option<bool>,
    request_id: Option<String>,
) -> IpcEnvelope<Option<CharacterLibrary>> {
    let id = normalize_request_id(request_id);
    let result = (|| -> Result<Option<CharacterLibrary>, String> {
        if use_default == Some(true) && directory.is_some() {
            return Err("请选择默认目录或自定义目录中的一种。".into());
        }
        let path = if use_default == Some(true) {
            let app_dir = Path::new(&state.data_dir)
                .parent()
                .ok_or("无法定位软件目录。")?;
            character_workspace::portable_library(app_dir).map_err(|error| error.to_string())?
        } else {
            match directory.as_ref() {
                Some(path) => PathBuf::from(path),
                None => saved_library(&state)?.ok_or("无法定位角色库。")?,
            }
        };
        let root = character_workspace::library_root(&path).map_err(|error| error.to_string())?;
        let library = character_workspace::list(&root).map_err(|error| error.to_string())?;
        if directory.is_some() || use_default == Some(true) {
            let database = state.database.lock().map_err(|_| "无法保存本地设置。")?;
            let setting = if use_default == Some(true) {
                ""
            } else {
                &library.root
            };
            database
                .set_setting_string(LIBRARY_SETTING, setting)
                .map_err(|error| error.to_string())?;
        }
        Ok(Some(library))
    })();
    match result {
        Ok(value) => success("character.library", id, value),
        Err(error) => failure(
            "character.library",
            id.clone(),
            core_error(&id, "CHARACTER_LIBRARY_UNAVAILABLE", &error, None, false),
        ),
    }
}

#[tauri::command]
pub fn create_character_workspace(
    state: State<'_, AppState>,
    library: String,
    name: String,
    request_id: Option<String>,
) -> IpcEnvelope<CharacterWorkspace> {
    let id = normalize_request_id(request_id);
    let result = (|| -> Result<CharacterWorkspace, String> {
        let root = saved_library(&state)?.ok_or("请先选择角色库。")?;
        if root != Path::new(&library) {
            return Err("角色库已切换，请刷新后再创建角色。".into());
        }
        let root = character_workspace::library_root(&root).map_err(|error| error.to_string())?;
        character_workspace::create(&root, &name).map_err(|error| error.to_string())
    })();
    match result {
        Ok(value) => success("character.create", id, value),
        Err(error) => failure(
            "character.create",
            id.clone(),
            core_error(&id, "CHARACTER_CREATE_FAILED", &error, None, false),
        ),
    }
}

pub(super) fn target_root(
    state: &AppState,
    target: Option<&crate::character_workspace::CharacterTarget>,
) -> Result<PathBuf, String> {
    let root = saved_library(state)?.ok_or("无法定位生图项目。")?;
    if target.is_some_and(|target| root != Path::new(&target.library)) {
        return Err("角色库已切换，请重新选择生图角色。".into());
    }
    character_workspace::library_root(&root).map_err(|error| error.to_string())
}
