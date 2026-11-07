//! Read authoritative library documents on demand. No recursive image scan is used.
mod characters;
mod poses;
mod scenes;

use crate::path_utils::{canonicalize_for_user, display_path};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Component, Path, PathBuf},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ImageReference {
    pub path: String,
    pub purpose: String,
}
#[derive(Debug, Serialize)]
pub struct Variant {
    pub id: String,
    pub name: String,
    pub identity: String,
    pub outfit: String,
    pub quality: String,
    pub references: Vec<ImageReference>,
    pub issues: Vec<String>,
    pub version: String,
}
#[derive(Debug, Serialize)]
pub struct Character {
    pub id: String,
    pub name: String,
    pub variants: Vec<Variant>,
}
#[derive(Debug, Serialize)]
pub struct Pose {
    pub id: String,
    pub name: String,
    pub category: String,
    pub subcategory: String,
    pub prompt_zh: String,
    pub prompt_en: String,
    pub reference: Option<ImageReference>,
    pub required_supports: Vec<String>,
    pub status: String,
    pub version: String,
}
#[derive(Debug, Serialize)]
pub struct SceneView {
    pub id: String,
    pub direction: String,
    pub camera: String,
    pub reference: ImageReference,
}
#[derive(Debug, Serialize)]
pub struct Scene {
    pub id: String,
    pub name: String,
    pub category: String,
    pub group: String,
    pub prompt: String,
    pub capabilities: Vec<String>,
    pub views: Vec<SceneView>,
    pub status: String,
    pub version: String,
}
#[derive(Debug, Serialize)]
pub struct Catalog {
    pub root: String,
    pub characters: Vec<Character>,
    pub poses: Vec<Pose>,
    pub scenes: Vec<Scene>,
    pub issues: Vec<String>,
}
pub(super) fn read_text(path: &Path) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|e| format!("无法读取 {}：{e}", display_path(path)))?;
    if bytes.len() > 4 * 1024 * 1024 {
        return Err("资源文本超过 4 MiB。".into());
    }
    String::from_utf8(bytes)
        .map(|s| s.trim_start_matches('\u{feff}').to_owned())
        .map_err(|_| format!("资源不是 UTF-8：{}", display_path(path)))
}
pub(super) fn read_json(path: &Path) -> Result<Value, String> {
    serde_json::from_str(&read_text(path)?)
        .map_err(|e| format!("资源 JSON 格式错误 {}：{e}", display_path(path)))
}
pub(super) fn hash(text: &str) -> String {
    format!("{:x}", Sha256::digest(text.as_bytes()))
}
pub(super) fn string(value: &Value, key: &str) -> String {
    value[key].as_str().unwrap_or_default().to_owned()
}

/// Resolve relative bindings, including cross-outfit faces, but never leave this workshop.
pub(super) fn resolve(root: &Path, base: &Path, relative: &str) -> Result<PathBuf, String> {
    let portable = relative.replace('\\', "/");
    if portable.is_empty()
        || portable.contains(':')
        || Path::new(&portable).is_absolute()
        || portable.split('/').any(|p| p == "人工姿势图参考")
    {
        return Err(format!("资源必须使用库内相对路径：{relative}"));
    }
    let mut path = base.to_path_buf();
    for component in Path::new(&portable).components() {
        match component {
            Component::Normal(p) => path.push(p),
            Component::ParentDir => {
                path.pop();
            }
            Component::CurDir => {}
            _ => return Err("资源路径无效。".into()),
        }
    }
    let actual = canonicalize_for_user(&path).map_err(|e| format!("资源不存在 {relative}：{e}"))?;
    if !actual.starts_with(root) || !actual.is_file() {
        return Err(format!("资源越出工坊库或不是文件：{relative}"));
    }
    Ok(actual)
}
pub(super) fn image(
    root: &Path,
    base: &Path,
    relative: &str,
    purpose: &str,
) -> Result<ImageReference, String> {
    let path = resolve(root, base, relative)?;
    match path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "png" | "jpg" | "jpeg" | "webp" => {}
        _ => return Err(format!("不支持的参考图片：{relative}")),
    }
    Ok(ImageReference {
        path: display_path(&path),
        purpose: purpose.into(),
    })
}

/// Exact heading selection; a sibling/higher heading ends a section.
pub(super) fn section(text: &str, title: &str) -> String {
    let mut active = false;
    let mut level = 0;
    let mut lines = Vec::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') {
            let n = trimmed.chars().take_while(|c| *c == '#').count();
            let heading = trimmed[n..].trim();
            if active && n <= level {
                break;
            }
            if !active && heading == title {
                active = true;
                level = n;
                continue;
            }
        }
        if active {
            lines.push(line);
        }
    }
    lines.join("\n").trim().to_owned()
}
pub(super) fn fenced_or_plain(text: &str) -> String {
    let mut in_fence = false;
    let mut blocks = Vec::new();
    for line in text.lines() {
        if line.trim_start().starts_with("```") {
            in_fence = !in_fence;
            continue;
        }
        if in_fence {
            blocks.push(line);
        }
    }
    if blocks.is_empty() {
        text.trim().to_owned()
    } else {
        blocks.join("\n").trim().to_owned()
    }
}
pub(super) fn prefixed(text: &str, prefix: &str) -> String {
    text.lines()
        .filter_map(|line| line.trim().strip_prefix(prefix))
        .map(str::trim)
        .collect::<Vec<_>>()
        .join("\n")
}
pub fn load(character_root: &Path) -> Result<Catalog, String> {
    let parent = character_root.parent().ok_or("无法定位工坊库根。")?;
    let root = canonicalize_for_user(parent).map_err(|e| e.to_string())?;
    let mut issues = Vec::new();
    let characters = characters::load(&root, character_root, &mut issues)?;
    let poses = poses::load(&root, &mut issues)?;
    let scenes = scenes::load(&root, &mut issues)?;
    Ok(Catalog {
        root: display_path(&root),
        characters,
        poses,
        scenes,
        issues,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn markdown_sections_do_not_leak_next_outfit() {
        let md = "# x\n## 身份\n```text\n身份正文\n```\n## 原皮\n原皮\n## 时装\n时装";
        assert_eq!(fenced_or_plain(&section(md, "身份")), "身份正文");
        assert_eq!(section(md, "原皮"), "原皮");
    }
    #[test]
    fn relative_references_reject_absolute_and_manual_uploads() {
        let root = std::env::temp_dir();
        for invalid in [
            "C:/secret.png",
            "/tmp/secret.png",
            "../人工姿势图参考/a.png",
            "人工姿势图参考/x.png",
        ] {
            assert!(resolve(&root, &root, invalid).is_err());
        }
    }
    #[test]
    #[ignore = "requires an explicit workshop fixture root"]
    fn actual_library_contract() {
        let root = std::env::var("WORKSHOP_CATALOG_ROOT").expect("explicit fixture root");
        let catalog = load(Path::new(&root)).expect("catalog should parse");
        assert_eq!(catalog.poses.len(), 122);
        assert_eq!(catalog.scenes.len(), 77);
        assert!(
            catalog
                .scenes
                .iter()
                .all(|scene| scene.views.len() == 4 && !scene.prompt.is_empty()),
            "{:?}",
            catalog.issues
        );
        let dock = catalog
            .scenes
            .iter()
            .find(|scene| scene.id == "S01-DOCK")
            .unwrap();
        assert!(!dock
            .capabilities
            .iter()
            .any(|support| support == "mirror" || support == "water"));
        let hall = catalog
            .scenes
            .iter()
            .find(|scene| scene.id == "S38-HALL")
            .unwrap();
        assert!(hall.prompt.contains("六面投射"));
        assert!(!hall
            .capabilities
            .iter()
            .any(|support| support == "mirror" || support == "seat"));
        assert!(catalog
            .characters
            .iter()
            .any(|c| c.name == "侠隐纲手" && c.variants.len() == 2));
        assert!(catalog.characters.iter().all(|c| c.name != "多人组合角色"));
        let problems = catalog
            .characters
            .iter()
            .flat_map(|c| {
                c.variants
                    .iter()
                    .map(move |v| format!("{}/{}: {:?}", c.name, v.name, v.issues))
            })
            .collect::<Vec<_>>();
        assert!(
            catalog
                .characters
                .iter()
                .all(|c| c.variants.iter().all(|v| v.issues.is_empty())),
            "{}",
            problems.join("\n")
        );
        if let Ok(path) = std::env::var("WORKSHOP_CATALOG_OUTPUT") {
            fs::write(path, serde_json::to_vec_pretty(&catalog).unwrap()).unwrap();
        }
        println!(
            "characters={}, variants={}, poses={}, adopted={}, scenes={}, warnings={:?}",
            catalog.characters.len(),
            catalog
                .characters
                .iter()
                .map(|c| c.variants.len())
                .sum::<usize>(),
            catalog.poses.len(),
            catalog
                .poses
                .iter()
                .filter(|p| p.reference.is_some())
                .count(),
            catalog.scenes.len(),
            catalog.issues
        );
    }
}
