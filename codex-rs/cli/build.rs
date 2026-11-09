fn main() {
    println!("cargo:rerun-if-env-changed=CODEX_FACTORY_REVISION");
    println!(
        "cargo:rustc-env=CODEX_FACTORY_REVISION={}",
        std::env::var("CODEX_FACTORY_REVISION").unwrap_or_else(|_| "development".to_string())
    );
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-link-arg=-ObjC");
    }
}
