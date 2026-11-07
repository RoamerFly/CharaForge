use super::*;

fn directional_camera(view: &Value, override_camera: Option<&str>) -> String {
    if let Some(camera) = override_camera {
        return camera.to_owned();
    }
    let description = view["camera_description"].as_str().unwrap_or_default();
    let first = description.lines().next().unwrap_or_default().trim();
    let is_edit = [
        "仅",
        "只",
        "修正",
        "纠正",
        "删除",
        "补回",
        "改为",
        "返修",
        "去掉",
        "保持首候选",
    ]
    .iter()
    .any(|word| first.contains(word));
    if first.is_empty() || is_edit {
        let position = match view["id"].as_str() {
            Some("F") => "空间后侧",
            Some("B") => "空间前侧",
            Some("L") => "空间右侧",
            Some("R") => "空间左侧",
            _ => "对应方向的参考机位",
        };
        return format!(
            "相机位于{position}，沿参考图的视线进入空间。门窗、固定家具和相邻通路保持图中的位置。"
        );
    }
    first.to_owned()
}

pub(super) fn load(root: &Path, issues: &mut Vec<String>) -> Result<Vec<Scene>, String> {
    let base = root.join("公共素材/场景");
    let index = match read_json(&base.join("other/场景清单.json")) {
        Ok(v) => v,
        Err(e) => {
            issues.push(e);
            return Ok(Vec::new());
        }
    };
    let metadata = read_json(&base.join("other/空间支撑条件.json")).unwrap_or(Value::Null);
    let mut scenes = Vec::new();
    for group in index["scene_groups"].as_array().into_iter().flatten() {
        for space in group["subspaces"].as_array().into_iter().flatten() {
            let id = string(space, "id");
            let result = (|| -> Result<Scene, String> {
                let manifest_path = resolve(
                    root,
                    &base,
                    space["manifest"]
                        .as_str()
                        .ok_or("场景没有 manifest 路径。")?,
                )?;
                let manifest = read_json(&manifest_path)?;
                let bible = read_text(&manifest_path.parent().unwrap().join("scene_bible.md"))?;
                let heading = bible.lines().find_map(|l| {
                    l.strip_prefix("## 当前局部设计")
                        .map(|s| format!("当前局部设计{s}"))
                });
                let local = &metadata[&id];
                let layout_matches = local["source"]
                    .as_str()
                    .and_then(|source| resolve(root, root, source).ok())
                    .and_then(|source| fs::read(source).ok())
                    .is_some_and(|bytes| {
                        local["source_sha256"].as_str()
                            == Some(format!("{:x}", Sha256::digest(bytes)).as_str())
                    });
                if !layout_matches {
                    issues.push(format!("{id} 的固定布局或支撑记录待刷新。"));
                }
                let prompt = local["prompt"]
                    .as_str()
                    .filter(|_| layout_matches)
                    .map(str::to_owned)
                    .or_else(|| {
                        heading
                            .map(|h| section(&bible, &h))
                            .filter(|s| !s.is_empty())
                    })
                    .unwrap_or_else(|| {
                        bible
                            .lines()
                            .skip_while(|l| l.trim().starts_with('#') || l.trim().is_empty())
                            .take_while(|l| !l.starts_with('#'))
                            .collect::<Vec<_>>()
                            .join("\n")
                            .trim()
                            .into()
                    });
                let mut views = Vec::new();
                for view in manifest["views"].as_array().into_iter().flatten() {
                    let view_id = string(view, "id");
                    let direction_metadata = &local["views"][&view_id];
                    let expected_hash = direction_metadata["sha256"]
                        .as_str()
                        .filter(|_| layout_matches)
                        .or_else(|| view["sha256"].as_str());
                    match image(root,&base,view["file"].as_str().unwrap_or_default(),&format!("仅固定 {id} 空间的布局和材质，{}，相机朝 {}；不继承人物或动作，不镜像房间。",string(view,"name"),string(view,"look_direction"))) {
                        Ok(reference) => {
                            let matches = fs::read(&reference.path).ok().is_some_and(|bytes| {
                                expected_hash == Some(format!("{:x}", Sha256::digest(bytes)).as_str())
                            });
                            if !matches {
                                issues.push(format!("{id} {view_id} 的方向参考与登记哈希不符。"));
                                continue;
                            }
                            let camera = directional_camera(view, direction_metadata["camera"].as_str().filter(|_|layout_matches));
                            views.push(SceneView { id:view_id, direction:string(view,"look_direction"), camera, reference });
                        }
                        Err(e)=>issues.push(e),
                    }
                }
                if views.len() != 4 {
                    issues.push(format!("{id} 有效方向参考为 {} / 4。", views.len()));
                }
                let capabilities = local["capabilities"]
                    .as_array()
                    .filter(|_| layout_matches)
                    .map(|a| {
                        a.iter()
                            .filter_map(Value::as_str)
                            .map(str::to_owned)
                            .collect()
                    })
                    .unwrap_or_default();
                Ok(Scene {
                    id: id.clone(),
                    name: string(space, "name"),
                    category: string(group, "category"),
                    group: string(group, "name"),
                    prompt,
                    capabilities,
                    views,
                    status: if manifest["user_verified"] == true {
                        "user_verified"
                    } else {
                        "design_reference"
                    }
                    .into(),
                    version: hash(&format!("{bible}{manifest}{local}")),
                })
            })();
            match result {
                Ok(scene) => scenes.push(scene),
                Err(e) => issues.push(format!("{id}：{e}")),
            }
        }
    }
    Ok(scenes)
}
