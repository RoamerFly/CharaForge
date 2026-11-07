//! Filesystem layout shared with the character image project.

use crate::path_utils::{canonicalize_for_user, display_path};
use serde::Serialize;
use serde_json::json;
use std::ffi::OsStr;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

#[derive(Clone, serde::Deserialize)]
pub struct CharacterTarget {
    pub library: String,
    pub name: String,
}

pub const LIBRARY_SETTING: &str = "generation_character_library";
const REFERENCE: &str = "参考图";
const SOURCES: &str = "素材来源";
const GALLERY: &str = "图集";
const OTHER: &str = "other";
const PENDING: &str = "待审核";
const APPEARANCE: &str = "外貌提示词-准确.md";
const CONFIG: &str = "角色配置.json";

#[derive(Debug, Serialize)]
pub struct CharacterLibrary {
    pub root: String,
    pub roles: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct CharacterWorkspace {
    pub name: String,
    pub path: String,
}

fn validate_name(name: &str) -> io::Result<()> {
    let first_segment = name.split('.').next().unwrap_or_default().trim_end();
    let device = first_segment.to_ascii_uppercase();
    let numbered_device = ["COM", "LPT"].iter().any(|prefix| {
        device.strip_prefix(prefix).is_some_and(|suffix| {
            matches!(
                suffix,
                "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
            )
        })
    });
    if name.is_empty()
        || name != name.trim()
        || name.ends_with('.')
        || name.starts_with('.')
        || name.encode_utf16().count() > 80
        || name
            .chars()
            .any(|c| c.is_control() || "\\/:*?\"<>|".contains(c))
        || matches!(
            device.as_str(),
            "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
        )
        || numbered_device
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "请输入有效的角色名称，最多 80 个字符，不能包含路径符号或 Windows 保留名称。",
        ));
    }
    Ok(())
}

pub fn portable_library(app_dir: &Path) -> io::Result<PathBuf> {
    let app_dir = canonicalize_for_user(app_dir)?;
    let project = if app_dir.join("角色工坊库").is_dir() || !app_dir.join("生图项目").is_dir()
    {
        app_dir.join("角色工坊库")
    } else {
        app_dir.join("生图项目")
    };
    let mut directories = vec![
        project.clone(),
        project.join("公共素材"),
        project.join("角色库"),
        project.join("待删除"),
        project.join("Temp"),
        project.join("生图指引"),
    ];
    for category in ["姿势", "场景", "面部表情", "配件", "质量约束", "原动漫脸型"]
    {
        directories.push(project.join("公共素材").join(category));
    }
    for directory in directories {
        match fs::create_dir(&directory) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error),
        }
        let parent = directory
            .parent()
            .expect("app-relative directory has parent");
        direct_child(parent, &directory.file_name().unwrap().to_string_lossy())?;
    }
    canonicalize_for_user(project.join("角色库"))
}

pub fn library_root(path: &Path) -> io::Result<PathBuf> {
    if !path.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "请先选择角色库目录。",
        ));
    }
    // Accept the project root as well as its existing character-library child.
    let root = canonicalize_for_user(path)?;
    let child = root.join("角色库");
    let root = if child.is_dir() {
        canonicalize_for_user(child)?
    } else {
        root
    };
    if !root.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "角色库必须是已有目录。",
        ));
    }
    Ok(root)
}

fn direct_child(parent: &Path, name: &str) -> io::Result<PathBuf> {
    let path = canonicalize_for_user(parent.join(name))?;
    if !path.is_dir() || path.parent() != Some(parent) || path.file_name() != Some(OsStr::new(name))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "角色目录或子目录指向了其他位置。",
        ));
    }
    Ok(path)
}

pub fn list(root: &Path) -> io::Result<CharacterLibrary> {
    let mut roles = Vec::new();
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if validate_name(&name).is_ok()
            && entry.file_type()?.is_dir()
            && direct_child(root, &name).is_ok()
            && (entry.path().join(CONFIG).is_file() || entry.path().join(GALLERY).is_dir())
        {
            roles.push(name);
        }
    }
    roles.sort();
    Ok(CharacterLibrary {
        root: display_path(root),
        roles,
    })
}

pub fn create(root: &Path, name: &str) -> io::Result<CharacterWorkspace> {
    validate_name(name)?;
    let role = root.join(name);
    fs::create_dir(&role).map_err(|error| {
        if error.kind() == io::ErrorKind::AlreadyExists {
            io::Error::new(error.kind(), "这个角色目录已存在，请从列表选择。")
        } else {
            error
        }
    })?;
    let mut directories = vec![role.clone()];
    let mut files = Vec::new();
    let setup = (|| -> io::Result<()> {
        for relative in [
            REFERENCE,
            SOURCES,
            "素材来源/动漫+游戏参考",
            "素材来源/真人cosplay参考",
            GALLERY,
            OTHER,
            "other/待审核",
            "other/待返修",
            "other/备选",
            "other/generate",
        ] {
            let path = role.join(relative);
            fs::create_dir(&path)?;
            directories.push(path);
        }
        let appearance = format!(
            "# {name}｜外貌提示词-准确\n\n## 身份与主脸\n\n待确认。\n\n## 衣装与配件\n\n待补充。\n\n## 默认上传参考图\n\n待补充，请使用相对于角色目录的路径。\n"
        );
        let config = json!({
            "version": 2,
            "role_id": name,
            "display_name": name,
            "aliases": [],
            "paths": {
                "gallery": GALLERY,
                "references": REFERENCE,
                "sources": SOURCES,
                "other": OTHER
            },
            "appearance_prompt": APPEARANCE,
            "default_references": [],
            "variants": {}
        });
        for (filename, contents) in [
            (APPEARANCE, appearance),
            (CONFIG, serde_json::to_string_pretty(&config)?),
        ] {
            let path = role.join(filename);
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)?;
            files.push(path);
            writeln!(file, "{}", contents.trim_end())?;
        }
        Ok(())
    })();
    if let Err(error) = setup {
        for file in files.into_iter().rev() {
            let _ = fs::remove_file(file);
        }
        for directory in directories.into_iter().rev() {
            let _ = fs::remove_dir(directory);
        }
        return Err(error);
    }
    Ok(CharacterWorkspace {
        name: name.to_owned(),
        path: display_path(&role),
    })
}

pub fn pending_folder(root: &Path, name: Option<&str>) -> io::Result<PathBuf> {
    let role = match name {
        Some(name) => {
            validate_name(name)?;
            direct_child(root, name)?
        }
        None => root
            .parent()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "无法定位生图项目。"))?
            .to_path_buf(),
    };
    for (parent, child) in [(&role, OTHER), (&role.join(OTHER), PENDING)] {
        match fs::create_dir(parent.join(child)) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error),
        }
        direct_child(parent, child)?;
    }
    Ok(role.join(OTHER).join(PENDING))
}

pub fn new_candidate_folder(root: &Path, name: Option<&str>) -> io::Result<PathBuf> {
    new_generation_folder(root, name, "comfy")
}

pub fn new_generation_folder(root: &Path, name: Option<&str>, engine: &str) -> io::Result<PathBuf> {
    let pending = pending_folder(root, name)?;
    let output = pending.join(format!("{engine}-{}", uuid::Uuid::new_v4()));
    fs::create_dir(&output)?;
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn creates_project_layout_and_preserves_existing_roles() {
        let root = std::env::temp_dir().join(format!("charaforge-test-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        let root = canonicalize_for_user(root).unwrap();
        let name = "侠隐纲手-冷玉豪情";
        let result = create(&root, name).unwrap();
        let role = Path::new(&result.path);
        for directory in [
            "参考图",
            "图集",
            "素材来源/真人cosplay参考",
            "素材来源/动漫+游戏参考",
            "other/待返修",
            "other/备选",
        ] {
            assert!(role.join(directory).is_dir());
        }
        let config = fs::read(role.join(CONFIG)).unwrap();
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&config).unwrap()["paths"]["references"],
            REFERENCE
        );
        assert_eq!(
            create(&root, name).unwrap_err().kind(),
            io::ErrorKind::AlreadyExists
        );
        assert_eq!(fs::read(role.join(CONFIG)).unwrap(), config);
        assert_eq!(list(&root).unwrap().roles, vec![name]);
        let output = new_candidate_folder(&root, Some(name)).unwrap();
        assert_eq!(output.parent(), Some(role.join("other/待审核").as_path()));
        assert_ne!(output, new_candidate_folder(&root, Some(name)).unwrap());
        assert_eq!(fs::read_dir(role.join(GALLERY)).unwrap().count(), 0);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn portable_project_is_relative_to_the_executable_and_reusable() {
        let root =
            std::env::temp_dir().join(format!("charaforge-portable-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        let app_dir = canonicalize_for_user(&root).unwrap();
        let library = portable_library(&app_dir).unwrap();
        assert_eq!(library, app_dir.join("角色工坊库/角色库"));
        for category in ["姿势", "场景", "面部表情", "配件", "质量约束", "原动漫脸型"]
        {
            assert!(app_dir.join("角色工坊库/公共素材").join(category).is_dir());
        }
        for name in ["待删除", "Temp", "生图指引"] {
            assert!(app_dir.join("角色工坊库").join(name).is_dir());
        }
        create(&library, "默认角色").unwrap();
        assert_eq!(portable_library(&app_dir).unwrap(), library);
        let unassigned = new_candidate_folder(&library, None).unwrap();
        assert_eq!(
            unassigned.parent(),
            Some(app_dir.join("角色工坊库/other/待审核").as_path())
        );
        assert_eq!(list(&library).unwrap().roles, vec!["默认角色"]);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_traversal_and_windows_device_names() {
        for name in [
            "",
            ".",
            "..",
            "../test",
            "sub\\role",
            "x:y",
            "x. ",
            "CON",
            "lpt2.png",
            "COM¹",
            "CONIN$",
        ] {
            assert!(validate_name(name).is_err(), "{name}");
        }
    }
}
