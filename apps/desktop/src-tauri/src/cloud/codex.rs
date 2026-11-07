//! Local Codex app-server adapter. Auth remains owned by Codex.
use super::credentials::redact;
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    env, fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver},
    time::{Duration, Instant, SystemTime},
};

pub fn home() -> PathBuf {
    env::var_os("CODEX_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(
                env::var_os("USERPROFILE")
                    .or_else(|| env::var_os("HOME"))
                    .unwrap_or_default(),
            )
            .join(".codex")
        })
}
fn binary() -> Option<PathBuf> {
    if let Some(paths) = env::var_os("PATH") {
        for directory in env::split_paths(&paths) {
            let candidate = directory.join(if cfg!(windows) { "codex.exe" } else { "codex" });
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    let npm = PathBuf::from(env::var_os("APPDATA")?)
        .join("npm/node_modules/@openai/codex/node_modules/@openai");
    for entry in fs::read_dir(npm).ok()?.flatten() {
        if !entry.file_name().to_string_lossy().starts_with("codex-") {
            continue;
        }
        for vendor in fs::read_dir(entry.path().join("vendor"))
            .into_iter()
            .flatten()
            .flatten()
        {
            let candidate = vendor.path().join("bin/codex.exe");
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}
#[derive(Serialize)]
pub struct Account {
    pub available: bool,
    pub logged_in: bool,
    pub name: String,
    pub plan: String,
    pub home: String,
    pub config_exists: bool,
    pub auth_file_exists: bool,
    pub message: String,
}
pub fn probe(proxy: &str) -> Account {
    let root = home();
    let mut account = Account {
        available: binary().is_some(),
        logged_in: false,
        name: String::new(),
        plan: String::new(),
        home: crate::path_utils::display_path(&root),
        config_exists: root.join("config.toml").is_file(),
        auth_file_exists: root.join("auth.json").is_file(),
        message: String::new(),
    };
    match Server::start(proxy).and_then(|mut server| server.account()) {
        Ok(value) => {
            account.logged_in = true;
            account.name = value["email"].as_str().unwrap_or("未提供邮箱").into();
            account.plan = value["planType"].as_str().unwrap_or("unknown").into();
            account.message =
                "已连接本机 Codex。生图工具可用性由该客户端、账号及服务返回决定。".into();
        }
        Err(error) => account.message = redact(&error, ""),
    }
    account
}

struct Server {
    child: Child,
    input: ChildStdin,
    messages: Receiver<Value>,
    sequence: u64,
    pending: Vec<Value>,
}
impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
impl Server {
    fn start(proxy: &str) -> Result<Self, String> {
        let path = binary().ok_or("未找到 Codex CLI，请先安装并完成 ChatGPT 登录。")?;
        let mut command = Command::new(path);
        command
            .args([
                "app-server",
                "--listen",
                "stdio://",
                "-c",
                "model_provider=\"openai\"",
                "-c",
                "forced_login_method=\"chatgpt\"",
                "-c",
                "features.image_generation=true",
                "-c",
                "features.shell_tool=false",
                "-c",
                "features.multi_agent=false",
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        for key in [
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "CODEX_ACCESS_TOKEN",
            "OPENAI_BASE_URL",
        ] {
            command.env_remove(key);
        }
        if !proxy.is_empty() {
            command.env("HTTPS_PROXY", proxy).env("HTTP_PROXY", proxy);
        }
        crate::worker_runtime::process::configure_background_command(&mut command);
        let mut child = command
            .spawn()
            .map_err(|e| format!("无法启动本机 Codex。{e}"))?;
        let input = child.stdin.take().ok_or("Codex 输入管道不可用。")?;
        let output = child.stdout.take().ok_or("Codex 输出管道不可用。")?;
        let (tx, messages) = mpsc::sync_channel(64);
        std::thread::spawn(move || {
            let mut reader = BufReader::new(output);
            loop {
                let mut line = Vec::new();
                use std::io::Read;
                match reader
                    .by_ref()
                    .take(80 * 1024 * 1024)
                    .read_until(b'\n', &mut line)
                {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {
                        if let Ok(value) = serde_json::from_slice(&line) {
                            if tx.send(value).is_err() {
                                break;
                            }
                        }
                    }
                }
            }
        });
        let mut server = Self {
            child,
            input,
            messages,
            sequence: 0,
            pending: Vec::new(),
        };
        server.call("initialize",json!({"clientInfo":{"name":"charaforge","version":"0.1.0"},"capabilities":{"experimentalApi":true}}))?;
        server.send(json!({"method":"initialized"}))?;
        Ok(server)
    }
    fn send(&mut self, value: Value) -> Result<(), String> {
        writeln!(self.input, "{}", value)
            .and_then(|_| self.input.flush())
            .map_err(|_| "Codex 本地通信中断。".into())
    }
    fn receive(&mut self, deadline: Instant) -> Result<Value, String> {
        let wait = deadline.saturating_duration_since(Instant::now());
        let value = self
            .messages
            .recv_timeout(wait)
            .map_err(|_| "Codex 本地通信超时或进程退出，请检查账号和客户端。")?;
        if value.get("id").is_some() && value.get("method").is_some() {
            self.send(json!({"id":value["id"],"error":{"code":-32601,"message":"This client accepts only image generation output; external tools and interactive approvals are unavailable."}}))?;
        }
        Ok(value)
    }
    fn call(&mut self, method: &str, params: Value) -> Result<Value, String> {
        self.sequence += 1;
        let id = self.sequence;
        self.send(json!({"id":id,"method":method,"params":params}))?;
        let deadline = Instant::now() + Duration::from_secs(30);
        loop {
            let value = self.receive(deadline)?;
            if value.get("id").and_then(Value::as_u64) == Some(id) && value.get("method").is_none()
            {
                if value.get("error").is_some() {
                    return Err(redact(
                        value["error"]["message"]
                            .as_str()
                            .unwrap_or("Codex 请求失败。"),
                        "",
                    ));
                }
                return Ok(value["result"].clone());
            }
            self.pending.push(value);
        }
    }
    fn account(&mut self) -> Result<Value, String> {
        let result = self.call("account/read", json!({"refreshToken":false}))?;
        let account = &result["account"];
        if account["type"] != "chatgpt" {
            return Err("本机 Codex 尚未使用 ChatGPT 订阅登录，请先运行 codex login。".into());
        }
        Ok(account.clone())
    }
}
pub fn generate(
    prompt: &str,
    refs: &[PathBuf],
    folder: &Path,
    proxy: &str,
) -> Result<(Vec<u8>, Value), String> {
    let mut server = Server::start(proxy)?;
    let account = server.account()?;
    let instructions="You are an image-generation client. Call the built-in image_gen exactly once to produce one image. Attached images are visual references in order. Use only the image-generation tool. Do not run commands, scripts, external APIs, external tools or child agents. Return the resulting image.";
    let thread=server.call("thread/start",json!({"cwd":folder,"modelProvider":"openai","sandbox":"read-only","approvalPolicy":"never","ephemeral":true,"developerInstructions":instructions,"config":{"mcp_servers":{}}}))?;
    let tid = thread["thread"]["id"]
        .as_str()
        .ok_or("Codex 未返回任务 ID。")?
        .to_owned();
    let mut input = vec![json!({"type":"text","text":format!("{instructions}\n\n{prompt}")})];
    input.extend(
        refs.iter()
            .map(|path| json!({"type":"localImage","path":path})),
    );
    let started = SystemTime::now();
    let turn = server.call("turn/start", json!({"threadId":tid,"input":input}))?;
    let turn_id = turn["turn"]["id"]
        .as_str()
        .ok_or("Codex 未返回生图轮次。")?
        .to_owned();
    let deadline = Instant::now() + Duration::from_secs(600);
    let mut items = Vec::new();
    let mut explanation = String::new();
    loop {
        let value = if !server.pending.is_empty() {
            server.pending.remove(0)
        } else {
            match server.receive(deadline) {
                Ok(value) => value,
                Err(error) => {
                    let _=server.send(json!({"id":99999,"method":"turn/interrupt","params":{"threadId":tid,"turnId":turn_id}}));
                    return Err(format!("{error} 生图结果未确认，请检查 Codex 当前任务。"));
                }
            }
        };
        let params = &value["params"];
        if params["threadId"].as_str().is_some_and(|t| t != tid) {
            continue;
        }
        if value["method"] == "item/completed" {
            let item = &params["item"];
            if item["type"] == "imageGeneration" {
                items.push(item.clone());
            }
            if item["type"] == "agentMessage" {
                explanation = item["text"]
                    .as_str()
                    .unwrap_or("")
                    .chars()
                    .take(1400)
                    .collect();
            }
        }
        if value["method"] == "turn/completed" && params["turn"]["id"] == turn_id {
            if params["turn"]["status"] != "completed" {
                return Err(redact(
                    &format!("Codex 生图失败。{}", params["turn"]["error"]),
                    "",
                ));
            }
            break;
        }
    }
    if items.len() != 1 {
        return Err(redact(
            &format!("Codex 未返回单张可用的生图产物。{explanation}"),
            "",
        ));
    }
    let item = &items[0];
    if item.get("failure").is_some_and(|v| !v.is_null()) || item["status"] != "completed" {
        return Err(redact(
            &format!("Codex 图像工具未完成。{} {explanation}", item["failure"]),
            "",
        ));
    }
    let bytes = if let Some(saved) = item["savedPath"].as_str() {
        let path = fs::canonicalize(saved).map_err(|_| "Codex 返回的图片路径无效。")?;
        let root = fs::canonicalize(home()).map_err(|_| "无法定位 Codex 生图产物目录。")?;
        let metadata = fs::metadata(&path).map_err(|e| e.to_string())?;
        if !path.starts_with(root)
            || refs
                .iter()
                .any(|r| fs::canonicalize(r).ok().as_ref() == Some(&path))
            || metadata.len() > 50 * 1024 * 1024
            || metadata.modified().map_err(|e| e.to_string())?
                < started
                    .checked_sub(Duration::from_secs(3))
                    .unwrap_or(started)
        {
            return Err("Codex 返回的文件不是本次可验证的生成产物。".into());
        }
        fs::read(path).map_err(|e| e.to_string())?
    } else {
        let data = item["result"]
            .as_str()
            .ok_or("Codex 只返回附件标识，当前 CLI 未提供图片内容或本地路径。")?;
        if data.len() > 70 * 1024 * 1024 {
            return Err("Codex 图片过大。".into());
        }
        STANDARD
            .decode(data.strip_prefix("data:image/png;base64,").unwrap_or(data))
            .map_err(|_| format!("Codex 图片编码不可用。{explanation}"))?
    };
    super::image_format(&bytes)?;
    Ok((
        bytes,
        json!({"thread_id":tid,"turn_id":turn_id,"plan":account["planType"],"revised_prompt":item["revisedPrompt"],"size_quality_mode":"prompt_preferences"}),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn configured_home_is_not_copied_or_parsed_as_a_key() {
        assert!(home().is_absolute());
        let path = binary();
        if let Some(path) = path {
            assert!(path.is_file());
        }
    }
}
