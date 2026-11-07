use super::character_workspace::target_root;
use crate::{
    character_workspace::CharacterTarget,
    generation_catalog::{self, Catalog},
    ipc::{core_error, failure, normalize_request_id, success, IpcEnvelope},
    state::AppState,
};
use tauri::{AppHandle, Manager};

#[tauri::command]
pub async fn generation_catalog(
    app: AppHandle,
    character_target: Option<CharacterTarget>,
    request_id: Option<String>,
) -> IpcEnvelope<Catalog> {
    let id = normalize_request_id(request_id);
    let result = async {
        let root = {
            let state = app.state::<AppState>();
            target_root(&state, character_target.as_ref())?
        };
        let catalog = tauri::async_runtime::spawn_blocking(move || generation_catalog::load(&root))
            .await
            .map_err(|e| e.to_string())??;
        for reference in catalog
            .characters
            .iter()
            .flat_map(|r| r.variants.iter())
            .flat_map(|v| v.references.iter())
            .chain(catalog.poses.iter().filter_map(|p| p.reference.as_ref()))
            .chain(
                catalog
                    .scenes
                    .iter()
                    .flat_map(|s| s.views.iter())
                    .map(|v| &v.reference),
            )
        {
            crate::models::allow_asset_file(&app, std::path::Path::new(&reference.path))?;
        }
        Ok::<_, String>(catalog)
    }
    .await;
    match result {
        Ok(value) => success("generation.catalog", id, value),
        Err(error) => failure(
            "generation.catalog",
            id.clone(),
            core_error(&id, "GENERATION_CATALOG_ERROR", &error, None, false),
        ),
    }
}
