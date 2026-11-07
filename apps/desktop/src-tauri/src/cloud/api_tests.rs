//! Real HTTP transport against a loopback fixture; never calls a paid provider.
use super::api::*;
use super::{profiles::StoredProfile, GenerateRequest, InputImage};
use base64::{engine::general_purpose::STANDARD, Engine};
use std::{
    io::{Read, Write},
    net::TcpListener,
    path::PathBuf,
    time::Duration,
};

#[test]
fn reference_images_are_sent_as_bytes_for_both_protocols() {
    for protocol in ["images", "grok"] {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let bytes = b"\x89PNG\r\n\x1a\nfixture-pixels".to_vec();
        let encoded = STANDARD.encode(&bytes);
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut received = Vec::new();
            let mut block = [0u8; 4096];
            loop {
                let n = socket.read(&mut block).unwrap();
                assert!(n > 0);
                received.extend_from_slice(&block[..n]);
                if let Some(i) = received.windows(4).position(|x| x == b"\r\n\r\n") {
                    let head = String::from_utf8_lossy(&received[..i]);
                    let length = head
                        .lines()
                        .find_map(|l| {
                            l.to_ascii_lowercase()
                                .strip_prefix("content-length:")
                                .and_then(|v| v.trim().parse::<usize>().ok())
                        })
                        .unwrap();
                    if received.len() >= i + 4 + length {
                        break;
                    }
                }
            }
            let body = format!(r#"{{"data":[{{"b64_json":"{encoded}"}}]}}"#);
            write!(socket,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\nx-request-id: fixture-001\r\n\r\n{}",body.len(),body).unwrap();
            received
        });
        let profile = StoredProfile {
            id: "fixture".into(),
            name: "loopback".into(),
            base_url: format!("http://{address}/v1"),
            model: "fixture-image".into(),
            protocol: protocol.into(),
            encrypted_key: super::credentials::encrypt("fixture-key").unwrap(),
        };
        let request = GenerateRequest {
            channel: "fixture".into(),
            prompt: "separate identity pose scene".into(),
            prompt_parts: Default::default(),
            size: "1024x1536".into(),
            quality: "high".into(),
            references: vec![super::Reference {
                path: "unused-source-path.png".into(),
                purpose: "pose only".into(),
            }],
        };
        let image = InputImage {
            path: PathBuf::from("unused-source-path.png"),
            mime: "image/png",
            bytes: bytes.clone(),
            purpose: "pose only".into(),
        };
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let result = runtime
            .block_on(generate(
                &profile,
                &request,
                "separate identity pose scene",
                &[image],
                "",
            ))
            .unwrap();
        assert_eq!(result.bytes, bytes);
        assert_eq!(result.metadata["provider_request_id"], "fixture-001");
        let received = server.join().unwrap();
        let raw = String::from_utf8_lossy(&received);
        assert!(raw.starts_with("POST /v1/images/edits HTTP/1.1"));
        assert!(raw
            .to_ascii_lowercase()
            .contains("authorization: bearer fixture-key"));
        assert!(raw.contains("separate identity pose scene"));
        if protocol == "images" {
            assert!(raw.contains("name=\"image[]\""));
            assert!(received.windows(bytes.len()).any(|x| x == bytes));
            assert!(raw.contains("1024x1536"));
        } else {
            let start = received.windows(4).position(|x| x == b"\r\n\r\n").unwrap() + 4;
            let json: serde_json::Value = serde_json::from_slice(&received[start..]).unwrap();
            assert!(json["images"][0]["url"]
                .as_str()
                .unwrap()
                .starts_with("data:image/png;base64,"));
        }
    }
}
