pub mod api;
#[cfg(test)]
mod api_tests;
pub mod codex;
pub mod credentials;
pub mod profiles;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

#[derive(Clone, Deserialize, Serialize)]
pub struct Reference {
    pub path: String,
    pub purpose: String,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct GenerateRequest {
    pub channel: String,
    pub prompt: String,
    #[serde(default)]
    pub prompt_parts: std::collections::BTreeMap<String, String>,
    pub size: String,
    pub quality: String,
    pub references: Vec<Reference>,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct GenerationResult {
    pub path: String,
    pub output_dir: String,
    pub status: String,
}

pub struct InputImage {
    pub path: PathBuf,
    pub mime: &'static str,
    pub bytes: Vec<u8>,
    pub purpose: String,
}

pub fn image_format(bytes: &[u8]) -> Result<(&'static str, &'static str), String> {
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Ok(("image/png", "png"));
    }
    if bytes.starts_with(b"\xff\xd8\xff") {
        return Ok(("image/jpeg", "jpg"));
    }
    if bytes.starts_with(b"RIFF") && bytes.get(8..12) == Some(b"WEBP") {
        return Ok(("image/webp", "webp"));
    }
    Err("图片须为 PNG、JPEG 或 WebP。".into())
}
pub fn read_references(refs: &[Reference]) -> Result<Vec<InputImage>, String> {
    if refs.len() > 8 {
        return Err("每次最多上传 8 张参考图。".into());
    }
    let mut total = 0u64;
    refs.iter()
        .map(|r| {
            if !Path::new(&r.path).is_absolute() || r.purpose.len() > 2000 {
                return Err("请添加本机图片的完整路径，用途说明最多 2000 字节。".into());
            }
            let path = crate::path_utils::canonicalize_for_user(&r.path)
                .map_err(|_| "参考图已不存在，请重新添加。")?;
            let metadata = std::fs::metadata(&path).map_err(|e| e.to_string())?;
            total += metadata.len();
            if !metadata.is_file() || metadata.len() > 25 * 1024 * 1024 || total > 64 * 1024 * 1024
            {
                return Err("单张参考图最多 25 MiB，全部最多 64 MiB。".into());
            }
            let bytes = std::fs::read(&path).map_err(|e| e.to_string())?;
            let (mime, _) = image_format(&bytes)?;
            Ok(InputImage {
                path,
                mime,
                bytes,
                purpose: r.purpose.clone(),
            })
        })
        .collect()
}
pub fn final_prompt(request: &GenerateRequest) -> String {
    let mut text = request.prompt.trim().to_owned();
    for (i, reference) in request.references.iter().enumerate() {
        if !reference.purpose.trim().is_empty() {
            text.push_str(&format!(
                "\nReference image {}: {}",
                i + 1,
                reference.purpose.trim()
            ));
        }
    }
    text
}
pub fn snapshot(images: &[InputImage], folder: &Path) -> Result<serde_json::Value, String> {
    let mut refs = Vec::new();
    for (i, image) in images.iter().enumerate() {
        let extension = image_format(&image.bytes)?.1;
        let filename = format!("reference-{:02}.{extension}", i + 1);
        let path = folder.join(&filename);
        std::fs::write(&path, &image.bytes).map_err(|e| e.to_string())?;
        refs.push(serde_json::json!({"original_path":image.path,"snapshot":filename,"sha256":format!("{:x}",Sha256::digest(&image.bytes)),"purpose":image.purpose}));
    }
    Ok(serde_json::Value::Array(refs))
}
pub fn validate(request: &GenerateRequest) -> Result<(), String> {
    if request.prompt.trim().is_empty() || request.prompt.len() > 50000 {
        return Err("请填写提示词，长度最多 50000 字节。".into());
    }
    if !matches!(request.quality.as_str(), "auto" | "low" | "medium" | "high") {
        return Err("图片质量无效。".into());
    }
    if !matches!(
        request.size.as_str(),
        "auto" | "1024x1024" | "1024x1536" | "1536x1024"
    ) {
        return Err("图片尺寸无效。".into());
    }
    Ok(())
}
