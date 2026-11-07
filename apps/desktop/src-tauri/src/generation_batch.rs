//! Durable, one-request-at-a-time generation. Never retry an ambiguous submission.
use crate::cloud::{self, GenerateRequest, GenerationResult};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Component, Path, PathBuf},
};

#[derive(Clone, Serialize, Deserialize)]
pub struct JobInput {
    pub id: String,
    pub pose_name: String,
    pub scene_name: String,
    pub pose_category: String,
    pub scene_category: String,
    pub request: GenerateRequest,
    pub resources: Value,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Job {
    pub input: JobInput,
    pub status: String,
    pub attempts: u32,
    pub error: Option<String>,
    pub result: Option<GenerationResult>,
    pub review: String,
    pub review_reason: String,
    pub promoted: Option<String>,
    pub reference_hashes: Vec<String>,
    #[serde(default)]
    pub original_references: Vec<String>,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Batch {
    pub schema: u32,
    pub id: String,
    pub character: String,
    pub variant: String,
    pub created_at: String,
    pub jobs: Vec<Job>,
    #[serde(default)]
    pub selection: Value,
}
fn error(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn safe_id(id: &str) -> bool {
    !id.is_empty() && id.len() < 100 && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
}
pub fn child(parent: &Path, relative: &str) -> Result<PathBuf, String> {
    let portable = relative.replace('\\', "/");
    if portable.is_empty()
        || portable.contains(':')
        || Path::new(&portable)
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err("队列路径无效。".into());
    }
    let base = crate::path_utils::canonicalize_for_user(parent).map_err(error)?;
    let path = crate::path_utils::canonicalize_for_user(base.join(portable)).map_err(error)?;
    if !path.starts_with(&base) {
        return Err("队列文件超出角色目录。".into());
    }
    Ok(path)
}
pub fn folder(root: &Path, character: &str, id: &str) -> Result<PathBuf, String> {
    if !safe_id(id) {
        return Err("批次编号无效。".into());
    }
    child(root, &format!("{character}/other/批量生成/{id}"))
}
pub fn save(path: &Path, batch: &Batch) -> Result<(), String> {
    let bytes = serde_json::to_vec_pretty(batch).map_err(error)?;
    let temp = path.join("batch.json.tmp");
    fs::write(&temp, bytes).map_err(error)?;
    fs::rename(temp, path.join("batch.json")).map_err(error)
}
pub fn load(path: &Path, idle: bool) -> Result<Batch, String> {
    let file = child(path, "batch.json")?;
    if fs::metadata(&file).map_err(error)?.len() > 16 * 1024 * 1024 {
        return Err("批次记录过大。".into());
    }
    let mut batch: Batch =
        serde_json::from_slice(&fs::read(file).map_err(error)?).map_err(error)?;
    if batch.schema != 1 {
        return Err("不支持的批次版本。".into());
    }
    if idle && recover(&mut batch) {
        save(path, &batch)?;
    }
    Ok(batch)
}
pub fn recover(batch: &mut Batch) -> bool {
    let mut changed = false;
    for job in &mut batch.jobs {
        if job.status == "running" {
            job.status = "uncertain".into();
            job.error = Some(
                "上次请求中断，是否已扣费或完成尚未确认。请查看请求记录，确认后手动重试。".into(),
            );
            changed = true;
        }
    }
    changed
}
pub fn create(
    root: &Path,
    character: &str,
    variant: String,
    inputs: Vec<JobInput>,
    selection: Value,
) -> Result<(PathBuf, Batch), String> {
    if inputs.is_empty() || inputs.len() > 500 {
        return Err("单批须为 1–500 项。".into());
    }
    let role = child(root, character)?;
    let mut ids = std::collections::HashSet::new();
    for input in &inputs {
        cloud::validate(&input.request)?;
        if !safe_id(&input.id) || !ids.insert(input.id.clone()) {
            return Err("任务编号重复或无效。".into());
        }
        if [
            &input.pose_name,
            &input.scene_name,
            &input.pose_category,
            &input.scene_category,
        ]
        .iter()
        .any(|x| x.trim().is_empty() || x.len() > 500)
        {
            return Err("任务缺少姿势或场景名称。".into());
        }
    }
    let parent = role.join("other/批量生成");
    fs::create_dir_all(&parent).map_err(error)?;
    let parent = child(&role, "other/批量生成")?;
    let id = format!("batch-{}", chrono::Utc::now().format("%Y%m%d%H%M%S%9f"));
    let path = parent.join(&id);
    fs::create_dir(&path).map_err(error)?;
    fs::create_dir(path.join("inputs")).map_err(error)?;
    let mut jobs = Vec::new();
    for mut input in inputs {
        let images = cloud::read_references(&input.request.references)?;
        let mut hashes = Vec::new();
        let mut original_references = Vec::new();
        for (reference, image) in input.request.references.iter_mut().zip(images) {
            let hash = format!("{:x}", Sha256::digest(&image.bytes));
            let relative = format!("inputs/{hash}.{}", cloud::image_format(&image.bytes)?.1);
            let target = path.join(&relative);
            if !target.exists() {
                fs::write(target, &image.bytes).map_err(error)?;
            }
            let original = match root
                .parent()
                .and_then(|parent| image.path.strip_prefix(parent).ok())
            {
                Some(relative) => relative.to_string_lossy().replace('\\', "/"),
                None => crate::path_utils::display_path(&image.path),
            };
            original_references.push(original);
            reference.path = relative;
            hashes.push(hash);
        }
        jobs.push(Job {
            input,
            status: "queued".into(),
            attempts: 0,
            error: None,
            result: None,
            review: "pending".into(),
            review_reason: String::new(),
            promoted: None,
            reference_hashes: hashes,
            original_references,
        });
    }
    let batch = Batch {
        schema: 1,
        id,
        character: character.into(),
        variant,
        created_at: chrono::Utc::now().to_rfc3339(),
        jobs,
        selection,
    };
    save(&path, &batch)?;
    Ok((path, batch))
}
pub fn next(batch: &Batch) -> Result<Option<usize>, String> {
    if batch
        .jobs
        .iter()
        .any(|j| j.status == "uncertain" || j.status == "running")
    {
        return Err("请先处理结果未确认的任务，避免重复扣费。".into());
    }
    Ok(batch.jobs.iter().position(|j| j.status == "queued"))
}
pub fn request(path: &Path, job: &Job) -> Result<GenerateRequest, String> {
    let mut request = job.input.request.clone();
    if request.references.len() != job.reference_hashes.len() {
        return Err("参考图快照记录不完整。".into());
    }
    for (r, hash) in request.references.iter_mut().zip(&job.reference_hashes) {
        let file = child(path, &r.path)?;
        if format!("{:x}", Sha256::digest(fs::read(&file).map_err(error)?)) != *hash {
            return Err("参考图快照已变化，请重新建立批次。".into());
        }
        r.path = crate::path_utils::display_path(&file);
    }
    Ok(request)
}
pub fn relative_result(root: &Path, result: GenerationResult) -> Result<GenerationResult, String> {
    let relative = |s: &str| -> Result<String, String> {
        let file = crate::path_utils::canonicalize_for_user(s).map_err(error)?;
        Ok(file
            .strip_prefix(root)
            .map_err(error)?
            .to_string_lossy()
            .replace('\\', "/"))
    };
    Ok(GenerationResult {
        path: relative(&result.path)?,
        output_dir: relative(&result.output_dir)?,
        status: result.status,
    })
}
pub fn display(root: &Path, mut batch: Batch) -> Result<Batch, String> {
    for job in &mut batch.jobs {
        if let Some(ref mut result) = job.result {
            result.path = crate::path_utils::display_path(&child(root, &result.path)?);
            result.output_dir = crate::path_utils::display_path(&child(root, &result.output_dir)?);
        }
        if let Some(ref mut promoted) = job.promoted {
            *promoted = crate::path_utils::display_path(&child(root, promoted)?);
        }
    }
    Ok(batch)
}
pub fn retry(job: &mut Job) -> Result<(), String> {
    if !matches!(job.status.as_str(), "failed" | "uncertain" | "cancelled") {
        return Err("只能手动重试失败、未确认或取消的任务。".into());
    }
    job.status = "queued".into();
    job.error = None;
    Ok(())
}

pub fn edit_prompt(
    job: &mut Job,
    parts: std::collections::BTreeMap<String, String>,
) -> Result<(), String> {
    if job.status != "queued" {
        return Err("只有未执行的任务可以修改提示词。".into());
    }
    let order = [
        "identity",
        "pose",
        "expression",
        "scene",
        "camera",
        "quality",
    ];
    if parts.keys().any(|key| !order.contains(&key.as_str())) {
        return Err("提示词分区无效。".into());
    }
    let mut request = job.input.request.clone();
    request.prompt = order
        .iter()
        .filter_map(|key| parts.get(*key))
        .map(|text| text.trim())
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n");
    request.prompt_parts = parts;
    cloud::validate(&request)?;
    job.input.request = request;
    if !job.input.resources.is_object() {
        job.input.resources = serde_json::json!({});
    }
    job.input.resources["prompt_edited"] = serde_json::json!(true);
    Ok(())
}
fn filename(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_control() || "\\/:*?\"<>|".contains(c) {
                '-'
            } else {
                c
            }
        })
        .take(36)
        .collect::<String>()
        .trim()
        .trim_matches('.')
        .to_owned()
}
pub fn review(
    root: &Path,
    batch: &mut Batch,
    index: usize,
    status: &str,
    reason: &str,
) -> Result<(), String> {
    if !matches!(status, "pending" | "approved" | "repair") {
        return Err("无效的审核标签。".into());
    }
    if status == "repair" && reason.trim().is_empty() {
        return Err("请填写返修原因。".into());
    }
    let job = batch.jobs.get_mut(index).ok_or("任务不存在。")?;
    if job.status != "succeeded" {
        return Err("只有生成成功的图片可以审核。".into());
    }
    if job.review == "approved" && status != "approved" {
        return Err("已入图集的图片需在图库中进行后续返修，批次不撤回正式图。".into());
    }
    if status == "approved" && job.promoted.is_none() {
        let source = child(root, &job.result.as_ref().ok_or("缺少图片结果。")?.path)?;
        let gallery = child(root, &format!("{}/图集", batch.character))?;
        let name = [
            &job.input.pose_category,
            &job.input.pose_name,
            &batch.variant,
            &job.input.scene_category,
            &job.input.scene_name,
        ]
        .into_iter()
        .map(|s| filename(s))
        .collect::<Vec<_>>()
        .join("_");
        let extension = source
            .extension()
            .and_then(|s| s.to_str())
            .ok_or("图片扩展名无效。")?;
        // Never replace an existing adopted image, even if a prior batch has the same labels.
        let mut sequence = 1u32;
        let target = loop {
            let file = gallery.join(format!("{name}_{sequence:03}.{extension}"));
            if !file.exists() {
                break file;
            }
            sequence += 1;
        };
        let mut output = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&target)
            .map_err(error)?;
        std::io::copy(&mut fs::File::open(source).map_err(error)?, &mut output).map_err(error)?;
        job.promoted = Some(
            target
                .strip_prefix(root)
                .map_err(error)?
                .to_string_lossy()
                .replace('\\', "/"),
        );
    }
    job.review = status.into();
    job.review_reason = reason.trim().into();
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    fn batch() -> Batch {
        let input = JobInput {
            id: "job-0001".into(),
            pose_name: "站立".into(),
            scene_name: "客厅".into(),
            pose_category: "通用".into(),
            scene_category: "室内".into(),
            request: GenerateRequest {
                channel: "test".into(),
                prompt: "p".into(),
                prompt_parts: Default::default(),
                size: "auto".into(),
                quality: "auto".into(),
                references: vec![],
            },
            resources: Value::Null,
        };
        Batch {
            schema: 1,
            id: "batch-test".into(),
            character: "test".into(),
            variant: "test".into(),
            created_at: String::new(),
            jobs: vec![Job {
                input,
                status: "running".into(),
                attempts: 1,
                error: None,
                result: None,
                review: "pending".into(),
                review_reason: String::new(),
                promoted: None,
                reference_hashes: vec![],
                original_references: vec![],
            }],
            selection: Value::Null,
        }
    }
    #[test]
    fn interrupted_submission_requires_explicit_retry() {
        let mut b = batch();
        assert!(recover(&mut b));
        assert_eq!(b.jobs[0].status, "uncertain");
        assert!(next(&b).is_err());
        retry(&mut b.jobs[0]).unwrap();
        assert_eq!(next(&b).unwrap(), Some(0));
        b.jobs[0].status = "failed".into();
        assert_eq!(next(&b).unwrap(), None);
    }
    #[test]
    fn reject_paths_before_access() {
        for s in ["../a", "C:/a", "/a", "a/../b"] {
            assert!(child(Path::new("."), s).is_err());
        }
    }

    #[test]
    fn prompt_edit_is_limited_to_unsubmitted_jobs() {
        let mut b = batch();
        let parts = std::collections::BTreeMap::from([
            ("identity".into(), "fixed identity".into()),
            ("pose".into(), "edited pose".into()),
        ]);
        assert!(edit_prompt(&mut b.jobs[0], parts.clone()).is_err());
        b.jobs[0].status = "queued".into();
        edit_prompt(&mut b.jobs[0], parts).unwrap();
        assert_eq!(
            b.jobs[0].input.request.prompt,
            "fixed identity\n\nedited pose"
        );
        assert_eq!(b.jobs[0].input.resources["prompt_edited"], true);
        let previous = b.jobs[0].input.request.prompt.clone();
        assert!(edit_prompt(&mut b.jobs[0], Default::default()).is_err());
        assert_eq!(b.jobs[0].input.request.prompt, previous);
    }
    #[test]
    fn reference_snapshots_survive_library_move_and_detect_changes() {
        let temp = std::env::temp_dir().join(format!("charaforge-batch-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(temp.join("角色库/test/other")).unwrap();
        fs::create_dir_all(temp.join("角色库/test/图集")).unwrap();
        let root = crate::path_utils::canonicalize_for_user(temp.join("角色库")).unwrap();
        let input_image = temp.join("source.png");
        fs::write(&input_image, b"\x89PNG\r\n\x1a\nfixture").unwrap();
        let mut input = batch().jobs.remove(0).input;
        input.request.references = vec![cloud::Reference {
            path: input_image.to_string_lossy().into(),
            purpose: "identity".into(),
        }];
        let (_, created) = create(
            &root,
            "test",
            "测试角色".into(),
            vec![input],
            serde_json::json!({"random_seed":42}),
        )
        .unwrap();
        let moved = temp.with_extension("moved");
        fs::rename(&temp, &moved).unwrap();
        let root = crate::path_utils::canonicalize_for_user(moved.join("角色库")).unwrap();
        let path = folder(&root, "test", &created.id).unwrap();
        let mut loaded = load(&path, true).unwrap();
        let request = request(&path, &loaded.jobs[0]).unwrap();
        assert_eq!(loaded.selection["random_seed"], 42);
        assert_eq!(loaded.jobs[0].original_references, ["source.png"]);
        assert!(Path::new(&request.references[0].path).starts_with(&moved));
        let output = root.join("test/other/candidate.png");
        fs::write(&output, b"\x89PNG\r\n\x1a\ncandidate").unwrap();
        loaded.jobs[0].result = Some(
            relative_result(
                &root,
                GenerationResult {
                    path: output.to_string_lossy().into(),
                    output_dir: output.parent().unwrap().to_string_lossy().into(),
                    status: "待审核".into(),
                },
            )
            .unwrap(),
        );
        loaded.jobs[0].status = "succeeded".into();
        assert!(review(&root, &mut loaded, 0, "repair", "").is_err());
        review(&root, &mut loaded, 0, "repair", "手指异常").unwrap();
        assert_eq!(fs::read_dir(root.join("test/图集")).unwrap().count(), 0);
        review(&root, &mut loaded, 0, "approved", "").unwrap();
        save(&path, &loaded).unwrap();
        let gallery = loaded.jobs[0].promoted.clone().unwrap();
        assert!(gallery.contains("测试角色"));
        let bytes = fs::read(child(&root, &gallery).unwrap()).unwrap();
        assert_eq!(bytes, b"\x89PNG\r\n\x1a\ncandidate");
        review(&root, &mut loaded, 0, "approved", "").unwrap();
        assert_eq!(fs::read_dir(root.join("test/图集")).unwrap().count(), 1);
        fs::write(&request.references[0].path, b"changed").unwrap();
        assert!(super::request(&path, &loaded.jobs[0]).is_err());
        // The test owns this random, verified temporary directory only.
        assert!(moved.starts_with(std::env::temp_dir()));
        fs::remove_dir_all(moved).unwrap();
    }
}
