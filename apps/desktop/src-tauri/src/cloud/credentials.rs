//! Encrypt API credentials using the current Windows account.
use base64::{engine::general_purpose::STANDARD, Engine};

#[cfg(windows)]
#[repr(C)]
struct Blob {
    length: u32,
    data: *mut u8,
}

#[cfg(windows)]
#[link(name = "Crypt32")]
extern "system" {
    fn CryptProtectData(
        input: *const Blob,
        description: *const u16,
        entropy: *const Blob,
        reserved: *mut std::ffi::c_void,
        prompt: *mut std::ffi::c_void,
        flags: u32,
        output: *mut Blob,
    ) -> i32;
    fn CryptUnprotectData(
        input: *const Blob,
        description: *mut *mut u16,
        entropy: *const Blob,
        reserved: *mut std::ffi::c_void,
        prompt: *mut std::ffi::c_void,
        flags: u32,
        output: *mut Blob,
    ) -> i32;
}
#[cfg(windows)]
#[link(name = "Kernel32")]
extern "system" {
    fn LocalFree(memory: *mut std::ffi::c_void) -> *mut std::ffi::c_void;
}

#[cfg(windows)]
fn transform(bytes: &[u8], encrypt: bool) -> Result<Vec<u8>, String> {
    use std::ptr::{null, null_mut};
    let input = Blob {
        length: bytes.len().try_into().map_err(|_| "凭据过长。")?,
        data: bytes.as_ptr() as *mut u8,
    };
    let mut output = Blob {
        length: 0,
        data: null_mut(),
    };
    // DPAPI owns the returned buffer. Copy before freeing it with LocalFree.
    let ok = unsafe {
        if encrypt {
            CryptProtectData(
                &input,
                null(),
                null(),
                null_mut(),
                null_mut(),
                1,
                &mut output,
            )
        } else {
            CryptUnprotectData(
                &input,
                null_mut(),
                null(),
                null_mut(),
                null_mut(),
                1,
                &mut output,
            )
        }
    };
    if ok == 0 {
        return Err("Windows 无法读取或保存当前账号的加密凭据，请重新填写 Key。".into());
    }
    let result =
        unsafe { std::slice::from_raw_parts(output.data, output.length as usize).to_vec() };
    unsafe {
        LocalFree(output.data.cast());
    }
    Ok(result)
}

#[cfg(not(windows))]
fn transform(_bytes: &[u8], _encrypt: bool) -> Result<Vec<u8>, String> {
    Err("当前 API 凭据存储需要 Windows。".into())
}

pub fn encrypt(key: &str) -> Result<String, String> {
    transform(key.as_bytes(), true).map(|x| STANDARD.encode(x))
}
pub fn decrypt(key: &str) -> Result<String, String> {
    String::from_utf8(transform(
        &STANDARD.decode(key).map_err(|_| "加密凭据损坏。")?,
        false,
    )?)
    .map_err(|_| "加密凭据格式无效。".into())
}

pub fn redact(text: &str, key: &str) -> String {
    let text = if key.is_empty() {
        text.to_owned()
    } else {
        text.replace(key, "[redacted]")
    };
    text.split_whitespace()
        .map(|part| {
            if part.contains("sk-") || part.starts_with("eyJ") {
                "[redacted]"
            } else {
                part
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(1800)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(windows)]
    #[test]
    fn dpapi_round_trip_and_redaction() {
        let key = "sk-fixture-private-value";
        let encrypted = encrypt(key).unwrap();
        assert!(!encrypted.contains(key));
        assert_eq!(decrypt(&encrypted).unwrap(), key);
        assert!(!redact(&format!("provider error {key}"), key).contains(key));
    }
}
