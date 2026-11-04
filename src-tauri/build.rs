use std::{env, fs, path::PathBuf, process::Command};

fn command(program: &str, args: &[&str]) {
    let status = Command::new(program)
        .args(args)
        .status()
        .expect("start native speech build");
    assert!(status.success(), "native speech build failed: {program}");
}

fn main() {
    println!("cargo:rerun-if-changed=native/speaker-speech.swift");
    println!("cargo:rerun-if-env-changed=DEVELOPER_DIR");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        let out = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR"));
        let cache = out.join("swift-module-cache");
        fs::create_dir_all(&cache).expect("create Swift cache");
        let mut binaries = Vec::new();
        for arch in ["arm64", "x86_64"] {
            let binary = out.join(format!("speaker-speech-{arch}"));
            let target = format!("{arch}-apple-macos26.0");
            command(
                "xcrun",
                &[
                    "swiftc",
                    "-parse-as-library",
                    "-O",
                    "-module-cache-path",
                    cache.to_str().unwrap(),
                    "-target",
                    &target,
                    "native/speaker-speech.swift",
                    "-o",
                    binary.to_str().unwrap(),
                ],
            );
            binaries.push(binary);
        }
        let universal = out.join("speaker-speech");
        command(
            "xcrun",
            &[
                "lipo",
                "-create",
                binaries[0].to_str().unwrap(),
                binaries[1].to_str().unwrap(),
                "-output",
                universal.to_str().unwrap(),
            ],
        );
        command(
            "/usr/bin/codesign",
            &["--force", "--sign", "-", universal.to_str().unwrap()],
        );
        fs::create_dir_all("resources").expect("create native resources directory");
        fs::copy(universal, "resources/speaker-speech").expect("copy native speech resource");
    }
    tauri_build::build()
}
