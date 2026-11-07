use super::*;

fn prompts(text: &str, config: &Value, variant: &str) -> (String, String, String) {
    let binding = config["prompt_bindings"][variant]
        .as_object()
        .or_else(|| config["prompt_bindings"]["default"].as_object());
    if let Some(binding) = binding {
        let field = |key: &str| -> String {
            binding
                .get(key)
                .and_then(Value::as_array)
                .map(|selectors| {
                    selectors
                        .iter()
                        .map(|selector| {
                            let body =
                                section(text, selector["section"].as_str().unwrap_or_default());
                            let mut selected = if let Some(prefix) = selector["prefix"].as_str() {
                                prefixed(&body, prefix)
                            } else {
                                fenced_or_plain(&body)
                            };
                            if let Some(start) = selector["start_at"].as_str() {
                                selected = selected
                                    .find(start)
                                    .map(|i| selected[i..].to_owned())
                                    .unwrap_or_default();
                            }
                            if let Some(end) = selector["end_before"].as_str() {
                                selected = selected
                                    .split(end)
                                    .next()
                                    .unwrap_or_default()
                                    .trim()
                                    .to_owned();
                            }
                            selected
                        })
                        .filter(|s| !s.is_empty())
                        .collect::<Vec<_>>()
                        .join("\n\n")
                })
                .unwrap_or_default()
        };
        return (field("identity"), field("outfit"), field("quality"));
    }
    // Compatibility with existing documents. Text continues to come from the Markdown.
    let body = section(text, "可直接复用的外貌提示词");
    if !body.is_empty() {
        return (
            prefixed(&body, "人物身份、面部与妆发："),
            prefixed(&body, "衣装、鞋袜与配饰："),
            [
                prefixed(&body, "角色特定约束："),
                section(text, "质量约束与核验"),
            ]
            .into_iter()
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join("\n\n"),
        );
    }
    (String::new(), String::new(), String::new())
}

pub(super) fn load(
    root: &Path,
    roles: &Path,
    issues: &mut Vec<String>,
) -> Result<Vec<Character>, String> {
    let mut characters = Vec::new();
    let listed = crate::character_workspace::list(roles).map_err(|e| e.to_string())?;
    for name in listed.roles {
        let role = roles.join(&name);
        let config_path = role.join("角色配置.json");
        let config = match read_json(&config_path) {
            Ok(v) => v,
            Err(e) => {
                issues.push(e);
                continue;
            }
        };
        if matches!(
            config["resource_kind"].as_str(),
            Some("collection" | "source_only")
        ) {
            continue;
        }
        let prompt_path = config["appearance_prompt"]
            .as_str()
            .unwrap_or("外貌提示词-准确.md");
        let text = resolve(root, &role, prompt_path).and_then(|p| read_text(&p));
        let text = match text {
            Ok(t) => t,
            Err(e) => {
                issues.push(e);
                String::new()
            }
        };
        let mut options: Vec<(String, Value)> = match &config["variants"] {
            Value::Object(map) if !map.is_empty() => {
                map.iter().map(|(k, v)| (k.clone(), v.clone())).collect()
            }
            Value::Array(values) if !values.is_empty() => values
                .iter()
                .map(|v| (string(v, "name"), v.clone()))
                .collect(),
            _ => vec![("default".to_owned(), Value::Null)],
        };
        options.sort_by(|a, b| a.0.cmp(&b.0));
        let mut variants = Vec::new();
        for (id, option) in options {
            let mut problems = Vec::new();
            let (identity, outfit, quality) = prompts(&text, &config, &id);
            if identity.is_empty() {
                problems.push("缺少固定身份提示词绑定".into());
            }
            if outfit.is_empty() {
                problems.push("缺少衣装提示词绑定".into());
            }
            let paths = if option["face"].is_string() || option["full_body"].is_string() {
                [option["face"].as_str(), option["full_body"].as_str()]
                    .into_iter()
                    .flatten()
                    .collect::<Vec<_>>()
            } else {
                option["default_references"]
                    .as_array()
                    .or_else(|| config["default_references"].as_array())
                    .map(|a| a.iter().filter_map(Value::as_str).collect())
                    .unwrap_or_default()
            };
            let mut references = Vec::new();
            for (i, path) in paths.iter().enumerate() {
                let purpose = if paths.len() == 1 {
                    "本造型的全身与可见身份锚点；未固定被遮挡的面部。"
                } else if i == 0 {
                    "仅固定同一人物的身份、五官与妆发，不继承姿势和背景。"
                } else {
                    "仅固定本造型衣装、配饰、鞋袜及身体比例，不继承姿势和背景。"
                };
                match image(root, &role, path, purpose) {
                    Ok(r) => {
                        if !references
                            .iter()
                            .any(|prior: &ImageReference| prior.path == r.path)
                        {
                            references.push(r);
                        }
                    }
                    Err(e) => problems.push(e),
                }
            }
            if references.is_empty() {
                problems.push("没有有效默认参考图".into());
            }
            variants.push(Variant {
                name: if id == "default" {
                    "默认造型".into()
                } else {
                    id.clone()
                },
                id,
                identity,
                outfit,
                quality,
                references,
                issues: problems,
                version: hash(&format!("{}{}", text, config)),
            });
        }
        characters.push(Character {
            id: config["role_id"].as_str().unwrap_or(&name).into(),
            name,
            variants,
        });
    }
    Ok(characters)
}
