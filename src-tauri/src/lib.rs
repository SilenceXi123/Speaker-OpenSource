mod local_speech;
mod offline_subtitles;

use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
};

use base64::Engine;
use serde::Serialize;
use tauri::Manager;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_updater::UpdaterExt;

#[derive(Serialize)]
struct BridgeHealthResponse {
    status: &'static str,
    mode: &'static str,
}

#[derive(Serialize, Clone)]
struct PickedFile {
    name: String,
    path: String,
    size_bytes: Option<u64>,
}

#[derive(Serialize)]
struct PickFilesResponse {
    files: Vec<PickedFile>,
}

#[derive(Serialize)]
struct ClientLogResponse {
    path: String,
    content: String,
}

#[derive(Serialize)]
struct ClientUpdateInfo {
    available: bool,
    version: Option<String>,
    notes: Option<String>,
    date: Option<String>,
}

#[derive(Serialize)]
struct ClientUpdateInstallResponse {
    installed: bool,
    version: Option<String>,
}

fn build_update_endpoint(api_base: &str, channel: &str) -> Result<String, String> {
    let base = api_base.trim().trim_end_matches('/');
    if base.is_empty() {
        return Err("缺少后端 API 地址".to_string());
    }
    let channel_value = channel.trim();
    if channel_value.is_empty() {
        return Err("缺少客户端更新通道".to_string());
    }
    Ok(format!(
        "{base}/client-updates/latest/{{{{target}}}}/{{{{arch}}}}/{{{{current_version}}}}?channel={channel_value}"
    ))
}

fn normalize_dialog_path(path: tauri_plugin_dialog::FilePath) -> Result<PathBuf, String> {
    path.into_path()
        .map_err(|_| "桌面客户端暂不支持当前返回的文件 URI".to_string())
}

fn to_picked_file(path: PathBuf) -> PickedFile {
    let size_bytes = fs::metadata(&path).ok().map(|metadata| metadata.len());
    PickedFile {
        name: path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("未命名文件")
            .to_string(),
        path: path.to_string_lossy().to_string(),
        size_bytes,
    }
}

fn ensure_client_log_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let mut directory = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("获取客户端日志目录失败：{error}"))?;
    directory.push("logs");
    fs::create_dir_all(&directory).map_err(|error| format!("创建客户端日志目录失败：{error}"))?;
    directory.push("client-events.log");
    Ok(directory)
}

#[tauri::command]
fn bridge_health() -> BridgeHealthResponse {
    BridgeHealthResponse {
        status: "ok",
        mode: "tauri",
    }
}

#[tauri::command]
async fn pick_media(app: tauri::AppHandle) -> Result<PickFilesResponse, String> {
    let file = app
        .dialog()
        .file()
        .add_filter(
            "Audio",
            &["wav", "mp3", "m4a", "aac", "aif", "aiff", "flac"],
        )
        .blocking_pick_file();
    let Some(file) = file else {
        return Ok(PickFilesResponse { files: Vec::new() });
    };
    let path = normalize_dialog_path(file)?;
    Ok(PickFilesResponse {
        files: vec![to_picked_file(path)],
    })
}

#[tauri::command]
async fn pick_lower_thirds(app: tauri::AppHandle) -> Result<PickFilesResponse, String> {
    let files = app
        .dialog()
        .file()
        .add_filter("Images", &["png", "jpg", "jpeg", "webp"])
        .blocking_pick_files();
    let Some(files) = files else {
        return Ok(PickFilesResponse { files: Vec::new() });
    };
    let picked = files
        .into_iter()
        .map(normalize_dialog_path)
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(to_picked_file)
        .collect::<Vec<_>>();
    Ok(PickFilesResponse { files: picked })
}

#[tauri::command]
fn read_file_base64(path: String) -> Result<String, String> {
    const CLIENT_READ_FILE_MAX_BYTES: u64 = 2 * 1024 * 1024 * 1024;
    let metadata = fs::metadata(&path).map_err(|error| format!("读取文件信息失败：{error}"))?;
    if metadata.len() > CLIENT_READ_FILE_MAX_BYTES {
        return Err(
            "当前文件超过 2GB 上限，客户端已停止读取。请先压缩或拆分后再上传。".to_string(),
        );
    }
    let bytes = fs::read(&path).map_err(|error| format!("读取文件失败：{error}"))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(bytes))
}

#[tauri::command]
fn write_file_base64(path: String, contents_base64: String) -> Result<(), String> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(contents_base64)
        .map_err(|error| format!("解析文件内容失败：{error}"))?;
    fs::write(&path, bytes).map_err(|error| format!("写入文件失败：{error}"))
}

#[tauri::command]
async fn pick_save_file(
    app: tauri::AppHandle,
    default_name: String,
) -> Result<Option<String>, String> {
    let mut dialog = app.dialog().file();
    let normalized_name = default_name.trim();
    if !normalized_name.is_empty() {
        dialog = dialog.set_file_name(normalized_name);
    }
    let Some(file) = dialog.blocking_save_file() else {
        return Ok(None);
    };
    let path = normalize_dialog_path(file)?;
    Ok(Some(path.to_string_lossy().to_string()))
}

#[tauri::command]
async fn pick_xml_file(app: tauri::AppHandle) -> Result<PickFilesResponse, String> {
    let file = app
        .dialog()
        .file()
        .add_filter("XML", &["xml"])
        .blocking_pick_file();
    let Some(file) = file else {
        return Ok(PickFilesResponse { files: Vec::new() });
    };
    let path = normalize_dialog_path(file)?;
    Ok(PickFilesResponse {
        files: vec![to_picked_file(path)],
    })
}

#[tauri::command]
async fn pick_xml_repair_images(app: tauri::AppHandle) -> Result<PickFilesResponse, String> {
    let files = app
        .dialog()
        .file()
        .add_filter("PNG Images", &["png"])
        .blocking_pick_files();
    let Some(files) = files else {
        return Ok(PickFilesResponse { files: Vec::new() });
    };
    let picked = files
        .into_iter()
        .map(normalize_dialog_path)
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(to_picked_file)
        .collect::<Vec<_>>();
    Ok(PickFilesResponse { files: picked })
}

#[tauri::command]
fn read_text_file(path: String) -> Result<String, String> {
    fs::read_to_string(&path).map_err(|error| format!("读取文本文件失败：{error}"))
}

#[tauri::command]
fn write_text_file(path: String, contents: String) -> Result<(), String> {
    fs::write(&path, contents).map_err(|error| format!("写入文本文件失败：{error}"))
}

#[tauri::command]
fn append_client_log(app: tauri::AppHandle, entry: String) -> Result<(), String> {
    let path = ensure_client_log_path(&app)?;
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|error| format!("打开客户端日志文件失败：{error}"))?;
    file.write_all(entry.as_bytes())
        .and_then(|_| file.write_all(b"\n"))
        .map_err(|error| format!("写入客户端日志失败：{error}"))
}

#[tauri::command]
fn read_client_logs(
    app: tauri::AppHandle,
    max_lines: Option<usize>,
) -> Result<ClientLogResponse, String> {
    let path = ensure_client_log_path(&app)?;
    let content = if path.exists() {
        fs::read_to_string(&path).map_err(|error| format!("读取客户端日志失败：{error}"))?
    } else {
        String::new()
    };
    let normalized_max_lines = max_lines.unwrap_or(200).max(1);
    let lines = content.lines().collect::<Vec<_>>();
    let start = lines.len().saturating_sub(normalized_max_lines);
    let recent = lines[start..].join("\n");
    Ok(ClientLogResponse {
        path: path.to_string_lossy().to_string(),
        content: recent,
    })
}

#[tauri::command]
fn clear_client_logs(app: tauri::AppHandle) -> Result<(), String> {
    let path = ensure_client_log_path(&app)?;
    fs::write(&path, "").map_err(|error| format!("清空客户端日志失败：{error}"))
}

#[tauri::command]
async fn check_client_update(
    app: tauri::AppHandle,
    api_base: String,
    channel: String,
) -> Result<ClientUpdateInfo, String> {
    let endpoint = build_update_endpoint(&api_base, &channel)?;
    let updater = app
        .updater_builder()
        .endpoints(vec![endpoint
            .parse()
            .map_err(|error| format!("更新地址无效：{error}"))?])
        .map_err(|error| format!("初始化更新器失败：{error}"))?
        .build()
        .map_err(|error| format!("初始化更新器失败：{error}"))?;
    let update = updater
        .check()
        .await
        .map_err(|error| format!("检查更新失败：{error}"))?;

    Ok(match update {
        Some(update) => ClientUpdateInfo {
            available: true,
            version: Some(update.version.clone()),
            notes: update.body.clone(),
            date: update.date.map(|value| value.to_string()),
        },
        None => ClientUpdateInfo {
            available: false,
            version: None,
            notes: None,
            date: None,
        },
    })
}

#[tauri::command]
async fn install_client_update(
    app: tauri::AppHandle,
    api_base: String,
    channel: String,
) -> Result<ClientUpdateInstallResponse, String> {
    let endpoint = build_update_endpoint(&api_base, &channel)?;
    let updater = app
        .updater_builder()
        .endpoints(vec![endpoint
            .parse()
            .map_err(|error| format!("更新地址无效：{error}"))?])
        .map_err(|error| format!("初始化更新器失败：{error}"))?
        .build()
        .map_err(|error| format!("初始化更新器失败：{error}"))?;
    let Some(update) = updater
        .check()
        .await
        .map_err(|error| format!("检查更新失败：{error}"))?
    else {
        return Ok(ClientUpdateInstallResponse {
            installed: false,
            version: None,
        });
    };

    update
        .download_and_install(|_chunk_length, _content_length| {}, || {})
        .await
        .map_err(|error| format!("安装更新失败：{error}"))?;
    app.restart()
}

#[cfg(test)]
mod tests {
    use super::build_update_endpoint;

    #[test]
    fn build_update_endpoint_requires_explicit_channel() {
        let error = build_update_endpoint(" http://127.0.0.1:8000/api/v1/ ", " ")
            .expect_err("empty channel should fail");

        assert_eq!(error, "缺少客户端更新通道");
    }

    #[test]
    fn build_update_endpoint_trims_base_and_keeps_channel() {
        let endpoint = build_update_endpoint(" http://127.0.0.1:8000/api/v1/ ", "alpha-macos12")
            .expect("endpoint should be built");

        assert_eq!(
            endpoint,
            "http://127.0.0.1:8000/api/v1/client-updates/latest/{{target}}/{{arch}}/{{current_version}}?channel=alpha-macos12"
        );
    }

    #[test]
    fn build_update_endpoint_rejects_empty_base() {
        let error = build_update_endpoint("   ", "beta").expect_err("empty base should fail");

        assert_eq!(error, "缺少后端 API 地址");
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(local_speech::LocalSpeechState::default())
        .manage(offline_subtitles::OfflineSubtitlesState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            bridge_health,
            local_speech::detect_local_speech,
            local_speech::prepare_local_speech,
            local_speech::transcribe_local_speech,
            local_speech::cancel_local_speech,
            offline_subtitles::offline_subtitles,
            offline_subtitles::offline_source_path,
            pick_media,
            pick_lower_thirds,
            read_file_base64,
            write_file_base64,
            pick_save_file,
            pick_xml_file,
            pick_xml_repair_images,
            read_text_file,
            write_text_file,
            append_client_log,
            read_client_logs,
            clear_client_logs,
            check_client_update,
            install_client_update
        ])
        .build(tauri::generate_context!())
        .expect("error while building Speaker desktop client")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                app.state::<local_speech::LocalSpeechState>().shutdown();
                app.state::<offline_subtitles::OfflineSubtitlesState>()
                    .shutdown();
            }
        });
}
