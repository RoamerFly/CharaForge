use super::{
    credentials,
    profiles::{self, StoredProfile},
    GenerateRequest, InputImage,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use reqwest::{multipart, Client};
use serde_json::{json, Value};
use std::time::Duration;

pub struct ApiOutput {
    pub bytes: Vec<u8>,
    pub metadata: Value,
}

pub fn client(proxy: &str) -> Result<Client, String> {
    let mut builder = Client::builder()
        .connect_timeout(Duration::from_secs(20))
        .timeout(Duration::from_secs(600))
        .redirect(reqwest::redirect::Policy::none());
    if !proxy.trim().is_empty() {
        builder = builder.proxy(reqwest::Proxy::all(proxy).map_err(|_| "代理地址无效。")?);
    }
    builder.build().map_err(|e| e.to_string())
}
async fn limited_bytes(response: reqwest::Response, limit: usize) -> Result<Vec<u8>, String> {
    if response.content_length().is_some_and(|n| n > limit as u64) {
        return Err("接口返回内容超过大小限制。".into());
    }
    let mut response = response;
    let mut bytes = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| "读取生图响应中断，任务结果未知，请先检查服务商记录。")?
    {
        if bytes.len() + chunk.len() > limit {
            return Err("接口返回内容超过大小限制。".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}
pub async fn generate(
    profile: &StoredProfile,
    request: &GenerateRequest,
    prompt: &str,
    images: &[InputImage],
    proxy: &str,
) -> Result<ApiOutput, String> {
    let key = credentials::decrypt(&profile.encrypted_key)?;
    let result = generate_with_key(profile, &key, request, prompt, images, proxy).await;
    result.map_err(|e| credentials::redact(&e, &key))
}
async fn generate_with_key(
    profile: &StoredProfile,
    key: &str,
    request: &GenerateRequest,
    prompt: &str,
    images: &[InputImage],
    proxy: &str,
) -> Result<ApiOutput, String> {
    let client = client(proxy)?;
    let base = profile.base_url.trim_end_matches('/');
    profiles::url(base)?;
    let endpoint = if images.is_empty() {
        "generations"
    } else {
        "edits"
    };
    let url = format!("{base}/images/{endpoint}");
    let request_builder = client.post(&url).bearer_auth(key);
    let response=if profile.protocol=="grok" {
        let mut body=json!({"model":profile.model,"prompt":prompt,"n":1});
        if !images.is_empty() {
            body["images"]=Value::Array(images.iter().map(|im| json!({"type":"image_url","url":format!("data:{};base64,{}",im.mime,STANDARD.encode(&im.bytes))})).collect());
        }
        request_builder.json(&body).send().await
    } else if images.is_empty() {
        request_builder.json(&json!({"model":profile.model,"prompt":prompt,"size":request.size,"quality":request.quality,"n":1,"output_format":"png"})).send().await
    } else {
        let mut form=multipart::Form::new().text("model",profile.model.clone()).text("prompt",prompt.to_owned())
            .text("size",request.size.clone()).text("quality",request.quality.clone()).text("n","1").text("output_format","png");
        for (i,im) in images.iter().enumerate() {
            let part=multipart::Part::bytes(im.bytes.clone()).file_name(format!("reference-{}.{}",i+1,super::image_format(&im.bytes)?.1)).mime_str(im.mime).map_err(|e|e.to_string())?;
            form=form.part("image[]",part);
        }
        request_builder.multipart(form).send().await
    }.map_err(|error| if error.is_timeout() { "生图请求超时，服务商结果未知，请检查记录后再决定是否重试。".into() } else { format!("生图请求失败，请核对服务商记录。{error}") })?;
    let status = response.status();
    let request_id = response
        .headers()
        .get("x-request-id")
        .and_then(|x| x.to_str().ok())
        .unwrap_or_default()
        .to_owned();
    let bytes = limited_bytes(response, 80 * 1024 * 1024).await?;
    let body: Value = serde_json::from_slice(&bytes)
        .map_err(|_| format!("接口返回非 JSON，HTTP {}。", status.as_u16()))?;
    if !status.is_success() {
        let message = body
            .pointer("/error/message")
            .and_then(Value::as_str)
            .unwrap_or("服务商未提供具体原因。");
        return Err(format!(
            "HTTP {}，{}（请求 {}）",
            status.as_u16(),
            message,
            request_id
        ));
    }
    let data = body
        .get("data")
        .and_then(Value::as_array)
        .ok_or("接口没有返回图片列表。")?;
    if data.len() != 1 {
        return Err("本次请求一张图片，接口返回数量不符，请检查服务商记录。".into());
    }
    let item = &data[0];
    let raw = if let Some(encoded) = item
        .get("b64_json")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
    {
        STANDARD
            .decode(encoded)
            .map_err(|_| "接口返回的图片编码无效。")?
    } else if let Some(url) = item.get("url").and_then(Value::as_str) {
        if url.starts_with("data:image/") {
            STANDARD
                .decode(url.split_once(',').ok_or("图片数据地址无效。")?.1)
                .map_err(|_| "图片编码无效。")?
        } else {
            let image_url = reqwest::Url::parse(url).map_err(|_| "图片下载地址无效。")?;
            let local = matches!(
                image_url.host_str(),
                Some("localhost" | "127.0.0.1" | "[::1]" | "::1")
            );
            if (image_url.scheme() != "https" && !(local && image_url.scheme() == "http"))
                || !image_url.username().is_empty()
                || image_url.password().is_some()
            {
                return Err("图片下载地址须使用 HTTPS。".into());
            }
            // Image download is unauthenticated and uses its own short timeout.
            let response = client
                .get(url)
                .timeout(Duration::from_secs(90))
                .send()
                .await
                .map_err(|_| "图片下载失败，请从请求记录恢复，避免重复生图。")?;
            if !response.status().is_success() {
                return Err(format!(
                    "下载生成图失败，HTTP {}。",
                    response.status().as_u16()
                ));
            }
            limited_bytes(response, 50 * 1024 * 1024).await?
        }
    } else {
        return Err("接口返回成功，但没有可用图片数据或 URL。".into());
    };
    if raw.len() > 50 * 1024 * 1024 {
        return Err("生成图片超过 50 MiB。".into());
    }
    super::image_format(&raw)?;
    Ok(ApiOutput {
        bytes: raw,
        metadata: json!({"provider_request_id":request_id,"revised_prompt":item.get("revised_prompt"),"usage":body.get("usage"),"protocol":profile.protocol}),
    })
}
