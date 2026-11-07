use super::*;
use std::collections::{HashMap, HashSet};

pub(super) fn load(root: &Path, issues: &mut Vec<String>) -> Result<Vec<Pose>, String> {
    let base = root.join("公共素材/姿势");
    let text = match read_text(&base.join("姿势库.md")) {
        Ok(t) => t,
        Err(e) => {
            issues.push(e);
            return Ok(Vec::new());
        }
    };
    let version = hash(&text);
    let meta = read_json(&base.join("other/姿势支撑条件.json")).unwrap_or(Value::Null);
    let reviews =
        read_json(&base.join("other/中性模特重生成/姿势验收记录.json")).unwrap_or(Value::Null);
    let mut poses = Vec::new();
    let mut category = String::new();
    let mut subcategory = String::new();
    let mut name = String::new();
    let mut id = String::new();
    let mut fields = HashMap::<String, String>::new();
    let mut seen = HashSet::new();
    let clean_title = |s: &str| s.split(['（', '(']).next().unwrap_or(s).trim().to_owned();
    let mut emit = |id: &str,
                    name: &str,
                    category: &str,
                    subcategory: &str,
                    fields: &HashMap<String, String>| {
        if id.is_empty() {
            return;
        }
        if !seen.insert(id.to_owned()) {
            issues.push(format!("姿势 ID 重复：{id}"));
            return;
        }
        let raw_reference = fields
            .get("参考图片")
            .map(String::as_str)
            .unwrap_or_default();
        let adopted = raw_reference.starts_with("中性模特参考图/");
        let reference = if adopted {
            match image(root, &base, raw_reference, "仅参考灰模的肢体动作、人物左右和接触关系；不得继承灰色材质、无五官头部或道具材质。") {
            Ok(r) => {
                let expected=format!("公共素材/姿势/{raw_reference}");
                let record=reviews[id].as_array().and_then(|a|a.iter().rev().find(|v|v["status"]=="accepted"&&v["binding_status"]!="withdrawn"&&v["image"].as_str()==Some(expected.as_str())));
                let verified=record.and_then(|v|v["image_sha256"].as_str()).is_some_and(|expected_hash|fs::read(&r.path).is_ok_and(|bytes|format!("{:x}",Sha256::digest(&bytes))==expected_hash));
                if verified {Some(r)} else {issues.push(format!("{id} 的参考图缺少匹配的验收哈希，暂不自动上传。"));None}
            }, Err(e) => { issues.push(e); None }
        }
        } else {
            None
        };
        let required = meta[id]["required_supports"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_else(|| vec!["unverified".into()]);
        let status = if reference.is_some() {
            "accepted"
        } else {
            "missing_reference"
        }
        .into();
        poses.push(Pose {
            id: id.into(),
            name: name.into(),
            category: category.into(),
            subcategory: subcategory.into(),
            prompt_zh: fields.get("姿势").cloned().unwrap_or_default(),
            prompt_en: fields.get("英文提示词参考").cloned().unwrap_or_default(),
            reference,
            required_supports: required,
            status,
            version: version.clone(),
        });
    };
    for line in text.lines().chain(std::iter::once("## END")) {
        if line.starts_with("## ")
            || line.starts_with("### ")
            || line
                .split_once(". ")
                .is_some_and(|(n, _)| n.parse::<u16>().is_ok())
        {
            emit(&id, &name, &category, &subcategory, &fields);
            id.clear();
            fields.clear();
            if let Some(s) = line.strip_prefix("### ") {
                subcategory = clean_title(s);
            } else if let Some(s) = line.strip_prefix("## ") {
                category = clean_title(s);
                subcategory.clear();
            } else {
                name = line.split_once(". ").unwrap().1.trim().into();
            }
        } else if let Some(s) = line.strip_prefix("<!-- pose_id: ") {
            id = s.trim_end_matches(" -->").trim().into();
        } else if let Some((key, value)) = line.split_once('：') {
            fields.insert(key.trim().into(), value.trim().into());
        }
    }
    Ok(poses)
}
