use super::credentials;
use crate::database::Database;
use serde::{Deserialize, Serialize};

const SETTING: &str = "generation_api_profiles";
#[derive(Clone, Serialize, Deserialize)]
pub struct StoredProfile {
    pub id: String,
    pub name: String,
    pub base_url: String,
    pub model: String,
    pub protocol: String,
    pub encrypted_key: String,
}
#[derive(Serialize)]
pub struct Profile {
    pub id: String,
    pub name: String,
    pub base_url: String,
    pub model: String,
    pub protocol: String,
    pub has_key: bool,
}
#[derive(Deserialize)]
pub struct SaveProfile {
    pub id: Option<String>,
    pub name: String,
    pub base_url: String,
    pub model: String,
    pub protocol: String,
    pub key: String,
}
pub fn read(database: &Database) -> Result<Vec<StoredProfile>, String> {
    database
        .get_setting_string(SETTING)
        .map_err(|e| e.to_string())?
        .map(|value| {
            serde_json::from_str(&value).map_err(|_| "API 配置损坏，请检查本地设置。".into())
        })
        .unwrap_or(Ok(Vec::new()))
}
pub fn list(database: &Database) -> Result<Vec<Profile>, String> {
    Ok(read(database)?
        .iter()
        .map(|p| Profile {
            id: p.id.clone(),
            name: p.name.clone(),
            base_url: p.base_url.clone(),
            model: p.model.clone(),
            protocol: p.protocol.clone(),
            has_key: !p.encrypted_key.is_empty(),
        })
        .collect())
}
pub fn url(base: &str) -> Result<reqwest::Url, String> {
    let url = reqwest::Url::parse(base).map_err(|_| "请输入完整的 API 地址。")?;
    let local = matches!(
        url.host_str(),
        Some("127.0.0.1" | "localhost" | "[::1]" | "::1")
    );
    if (url.scheme() != "https" && !(local && url.scheme() == "http"))
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.host_str().is_none()
    {
        return Err("API 地址须使用 HTTPS，本机测试允许 localhost HTTP。".into());
    }
    Ok(url)
}
pub fn save(database: &Database, input: SaveProfile) -> Result<Vec<Profile>, String> {
    let base = input.base_url.trim().trim_end_matches('/').to_owned();
    url(&base)?;
    if input.name.trim().is_empty()
        || input.name.len() > 160
        || input.model.trim().is_empty()
        || input.model.len() > 160
        || !matches!(input.protocol.as_str(), "images" | "grok")
    {
        return Err("请填写渠道名称、模型和有效的请求协议。".into());
    }
    let mut profiles = read(database)?;
    let id = input.id.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    let existing = profiles.iter().find(|p| p.id == id);
    if existing.is_some_and(|p| p.base_url != base) && input.key.trim().is_empty() {
        return Err("更换 API 地址后请重新填写 Key。".into());
    }
    let encrypted_key = if input.key.trim().is_empty() {
        existing
            .map(|p| p.encrypted_key.clone())
            .ok_or("请输入 API Key。")?
    } else {
        if input.key.trim().len() > 4096 {
            return Err("API Key 过长。".into());
        }
        credentials::encrypt(input.key.trim())?
    };
    let profile = StoredProfile {
        id: id.clone(),
        name: input.name.trim().into(),
        base_url: base,
        model: input.model.trim().into(),
        protocol: input.protocol,
        encrypted_key,
    };
    match profiles.iter().position(|p| p.id == id) {
        Some(i) => profiles[i] = profile,
        None => profiles.push(profile),
    }
    database
        .set_setting_string(
            SETTING,
            &serde_json::to_string(&profiles).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
    list(database)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_remote_plaintext_and_embedded_credentials() {
        for base in [
            "http://example.org/v1",
            "https://key@example.org",
            "https://example.org?key=secret",
            "file:///x",
        ] {
            assert!(url(base).is_err());
        }
        assert!(url("http://127.0.0.1:19082/v1").is_ok());
        assert!(url("https://api.openai.com/v1").is_ok());
    }
}

#[cfg(all(test, windows))]
mod persistence_tests {
    use super::*;
    #[test]
    fn keys_are_encrypted_and_preserved_only_for_the_same_server() {
        let folder =
            std::env::temp_dir().join(format!("charaforge-profiles-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&folder).unwrap();
        let database = Database::open(folder.join("profiles.sqlite3")).unwrap();
        let profile = |id, base: &str, key: &str| SaveProfile {
            id,
            name: "Fixture".into(),
            base_url: base.into(),
            model: "gpt-image-2".into(),
            protocol: "images".into(),
            key: key.into(),
        };
        let first = save(
            &database,
            profile(None, "https://example.org/v1", "sk-test-secret"),
        )
        .unwrap();
        let id = first[0].id.clone();
        let stored = read(&database).unwrap()[0].encrypted_key.clone();
        assert!(!serde_json::to_string(&first)
            .unwrap()
            .contains("sk-test-secret"));
        assert!(!database
            .get_setting_string(SETTING)
            .unwrap()
            .unwrap()
            .contains("sk-test-secret"));
        save(
            &database,
            profile(Some(id.clone()), "https://example.org/v1", ""),
        )
        .unwrap();
        assert_eq!(read(&database).unwrap()[0].encrypted_key, stored);
        assert!(save(
            &database,
            profile(Some(id.clone()), "https://other.example/v1", "")
        )
        .is_err());
        assert_eq!(
            read(&database).unwrap()[0].base_url,
            "https://example.org/v1"
        );
        drop(database);
        assert!(folder
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("charaforge-profiles-"));
        std::fs::remove_dir_all(folder).unwrap();
    }
}
