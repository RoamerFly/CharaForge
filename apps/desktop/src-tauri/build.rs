fn main() {
    // Cargo must rebuild the Windows resource when a branding asset changes.
    // Frontend-only rebuilds can otherwise retain the previous executable icon.
    println!("cargo:rerun-if-changed=icons/icon.ico");
    println!("cargo:rerun-if-changed=icons/icon.png");
    tauri_build::build()
}
