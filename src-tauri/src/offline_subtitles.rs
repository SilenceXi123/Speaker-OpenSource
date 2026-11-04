use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::Manager;

const JSON_LIMIT: u64 = 64 * 1024 * 1024;

#[derive(Debug, Serialize)]
pub struct OfflineError {
    code: String,
    message: String,
}
impl OfflineError {
    fn new(code: &str, message: &str) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
    fn storage() -> Self {
        Self::new(
            "storage_error",
            "无法读写本机字幕任务，请检查磁盘空间与文件权限",
        )
    }
}
struct ActiveWorker {
    child: Child,
    cancelled: bool,
}
#[derive(Clone, Default)]
pub struct OfflineSubtitlesState {
    storage: Arc<Mutex<()>>,
    process: Arc<Mutex<Option<ActiveWorker>>>,
    shutting_down: Arc<AtomicBool>,
}
impl OfflineSubtitlesState {
    pub fn shutdown(&self) {
        self.shutting_down.store(true, Ordering::SeqCst);
        self.cancel();
    }
    pub fn cancel(&self) -> bool {
        let mut active = self.process.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(worker) = active.as_mut() {
            worker.cancelled = true;
            let _ = worker.child.kill();
            return true;
        }
        false
    }
}

fn valid_id(id: &str) -> bool {
    let Some(uuid) = id.strip_prefix("local-") else {
        return false;
    };
    uuid.len() == 36
        && uuid.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_hexdigit()
            }
        })
}
fn new_id() -> Result<String, OfflineError> {
    let mut bytes = [0u8; 16];
    File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(&mut bytes))
        .map_err(|_| OfflineError::storage())?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    Ok(format!(
        "local-{}-{}-{}-{}-{}",
        &hex[..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..]
    ))
}
fn namespace(base: &Path, scope: &str) -> Result<PathBuf, OfflineError> {
    let scope = scope.trim().trim_end_matches('/');
    if scope.is_empty()
        || scope.len() > 180
        || !scope.starts_with("http://") && !scope.starts_with("https://")
    {
        return Err(OfflineError::new(
            "invalid_scope",
            "缺少有效服务器地址，无法确定本机项目范围",
        ));
    }
    Ok(base.join("offline").join(URL_SAFE_NO_PAD.encode(scope)))
}
fn job_path(directory: &Path, id: &str) -> Result<PathBuf, OfflineError> {
    if !valid_id(id) {
        return Err(OfflineError::new("invalid_id", "本机任务编号无效"));
    }
    Ok(directory.join("jobs").join(format!("{id}.json")))
}
fn read_json(path: &Path) -> Result<Value, OfflineError> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|_| OfflineError::new("not_found", "未找到当前项目的本机任务"))?;
    if !metadata.is_file() || metadata.len() > JSON_LIMIT {
        return Err(OfflineError::new("invalid_data", "本机任务文件无效"));
    }
    serde_json::from_slice(&fs::read(path).map_err(|_| OfflineError::storage())?)
        .map_err(|_| OfflineError::new("invalid_data", "本机任务文件无法读取"))
}
fn atomic_json(path: &Path, value: &Value) -> Result<(), OfflineError> {
    let bytes = serde_json::to_vec(value).map_err(|_| OfflineError::storage())?;
    if bytes.len() as u64 > JSON_LIMIT {
        return Err(OfflineError::new(
            "invalid_data",
            "本机任务内容超出保存限制",
        ));
    }
    let parent = path.parent().ok_or_else(OfflineError::storage)?;
    fs::create_dir_all(parent).map_err(|_| OfflineError::storage())?;
    let temporary = parent.join(format!(".{}.tmp", new_id()?));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        fs::rename(&temporary, path)?;
        File::open(parent)?.sync_all()
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result.map_err(|_| OfflineError::storage())
}
fn request_identity(payload: &Value) -> Result<(&str, &str), OfflineError> {
    let id = payload["id"]
        .as_str()
        .ok_or_else(|| OfflineError::new("invalid_id", "缺少本机任务编号"))?;
    let project = payload["project_id"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| OfflineError::new("invalid_project", "请选择项目"))?;
    Ok((id, project))
}
fn get_job(directory: &Path, id: &str, project: &str) -> Result<Value, OfflineError> {
    let job = read_json(&job_path(directory, id)?)?;
    if job["id"] != id || job["project_id"] != project {
        return Err(OfflineError::new("not_found", "未找到当前项目的本机任务"));
    }
    Ok(job)
}

fn run_worker(
    state: &OfflineSubtitlesState,
    executable: &Path,
    request: &Value,
    timeout: Duration,
) -> Result<Value, OfflineError> {
    let input = serde_json::to_vec(request)
        .map_err(|_| OfflineError::new("invalid_request", "离线处理请求无效"))?;
    if input.len() as u64 > JSON_LIMIT {
        return Err(OfflineError::new("invalid_request", "离线处理请求过大"));
    }
    let (mut stdin, stdout) = {
        let mut active = state.process.lock().unwrap_or_else(|e| e.into_inner());
        if state.shutting_down.load(Ordering::SeqCst) {
            return Err(OfflineError::new(
                "cancelled",
                "客户端正在退出，已取消离线字幕处理",
            ));
        }
        if active.is_some() {
            return Err(OfflineError::new(
                "busy",
                "已有离线字幕处理任务，请等待完成",
            ));
        }
        let mut child = Command::new(executable)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| {
                OfflineError::new(
                    "worker_unavailable",
                    "无法启动离线字幕组件，请重新安装客户端",
                )
            })?;
        let pipes = (
            child.stdin.take().expect("piped stdin"),
            child.stdout.take().expect("piped stdout"),
        );
        *active = Some(ActiveWorker {
            child,
            cancelled: false,
        });
        pipes
    };
    let writer = thread::spawn(move || stdin.write_all(&input));
    let (send, receive) = mpsc::channel();
    let reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = stdout.take(JSON_LIMIT + 1).read_to_end(&mut bytes);
        let _ = send.send((result, bytes));
    });
    let started = Instant::now();
    let mut captured = None;
    let mut failure = None;
    let exit = loop {
        if captured.is_none() {
            if let Ok((result, bytes)) = receive.try_recv() {
                if result.is_err() || bytes.len() as u64 > JSON_LIMIT {
                    failure = Some(OfflineError::new(
                        "invalid_result",
                        "离线字幕结果过大或无法读取",
                    ));
                }
                captured = Some(bytes);
            }
        }
        let mut active = state.process.lock().unwrap_or_else(|e| e.into_inner());
        let worker = active.as_mut().expect("worker owns process");
        if worker.cancelled {
            failure = Some(OfflineError::new("cancelled", "已取消离线字幕处理"));
        } else if started.elapsed() >= timeout {
            failure = Some(OfflineError::new("timeout", "离线字幕处理超时，请重试"));
        }
        if failure.is_some() {
            let _ = worker.child.kill();
            let status = worker.child.wait();
            active.take();
            break status;
        }
        match worker.child.try_wait() {
            Ok(Some(status)) => {
                active.take();
                break Ok(status);
            }
            Ok(None) => {}
            Err(error) => {
                let _ = worker.child.kill();
                let _ = worker.child.wait();
                active.take();
                break Err(error);
            }
        }
        drop(active);
        thread::sleep(Duration::from_millis(30));
    };
    let _ = writer.join();
    let _ = reader.join();
    if let Some(error) = failure {
        return Err(error);
    }
    let bytes = captured
        .or_else(|| {
            receive
                .recv()
                .ok()
                .and_then(|(result, bytes)| result.ok().map(|_| bytes))
        })
        .filter(|bytes| bytes.len() as u64 <= JSON_LIMIT)
        .ok_or_else(|| OfflineError::new("invalid_result", "无法读取离线字幕结果"))?;
    let response: Value = serde_json::from_slice(&bytes)
        .map_err(|_| OfflineError::new("invalid_result", "离线字幕组件没有返回有效结果"))?;
    if response["ok"] != true {
        return Err(OfflineError::new(
            response["error"]["code"]
                .as_str()
                .unwrap_or("worker_failed"),
            response["error"]["message"]
                .as_str()
                .unwrap_or("离线字幕处理失败"),
        ));
    }
    if !exit.map(|status| status.success()).unwrap_or(false) || response.get("data").is_none() {
        return Err(OfflineError::new("worker_failed", "离线字幕组件意外退出"));
    }
    Ok(response["data"].clone())
}

fn runtime_path(app: &tauri::AppHandle) -> Result<PathBuf, OfflineError> {
    let bundled = app
        .path()
        .resolve("offline-core", tauri::path::BaseDirectory::Resource)
        .map_err(|_| OfflineError::storage())?;
    #[cfg(debug_assertions)]
    let bundled = if bundled.is_dir() {
        bundled
    } else {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/offline-core")
    };
    let metadata = read_json(&bundled.join("runtime.json"))
        .map_err(|_| OfflineError::new("worker_missing", "客户端缺少离线字幕组件"))?;
    let architectures = metadata["architectures"]
        .as_array()
        .ok_or_else(|| OfflineError::new("worker_missing", "离线字幕组件架构信息无效"))?;
    if !architectures
        .iter()
        .any(|arch| arch.as_str() == Some(std::env::consts::ARCH))
    {
        return Err(OfflineError::new(
            "unsupported_architecture",
            "此测试版离线字幕组件暂不支持当前处理器，请使用云端识别",
        ));
    }
    let executable = bundled.join("speaker-offline");
    if !executable.is_file() {
        return Err(OfflineError::new(
            "worker_missing",
            "客户端缺少离线字幕组件",
        ));
    }
    Ok(executable)
}
fn operate(
    directory: &Path,
    state: &OfflineSubtitlesState,
    operation: &str,
    payload: Value,
    worker: Option<&Path>,
) -> Result<Value, OfflineError> {
    let _lock = state
        .storage
        .try_lock()
        .map_err(|_| OfflineError::new("busy", "本机任务正在保存或处理，请稍后重试"))?;
    match operation {
        "projects" => {
            let path = directory.join("projects.json");
            if !path.exists() {
                return Ok(json!([]));
            }
            read_json(&path)
        }
        "save_projects" => {
            let incoming = payload
                .get("projects")
                .and_then(Value::as_array)
                .ok_or_else(|| OfflineError::new("invalid_projects", "项目缓存格式无效"))?;
            if incoming
                .iter()
                .any(|project| project["id"].as_str().is_none_or(str::is_empty))
            {
                return Err(OfflineError::new(
                    "invalid_projects",
                    "项目缓存缺少有效项目编号",
                ));
            }
            let path = directory.join("projects.json");
            let mut projects = incoming.clone();
            // Cloud refresh and local project creation each read before writing. Preserve
            // local additions made after a refresh took its snapshot, under this same lock.
            if path.exists() {
                if let Some(previous) = read_json(&path)?.as_array() {
                    for project in previous {
                        if project["local_only"] == true
                            && !projects.iter().any(|item| item["id"] == project["id"])
                        {
                            projects.push(project.clone());
                        }
                    }
                }
            }
            let projects = Value::Array(projects);
            atomic_json(&path, &projects)?;
            Ok(projects)
        }
        "list" => {
            let mut jobs = Vec::new();
            let directory = directory.join("jobs");
            if directory.is_dir() {
                for entry in fs::read_dir(directory).map_err(|_| OfflineError::storage())? {
                    let path = entry.map_err(|_| OfflineError::storage())?.path();
                    let Some(id) = path.file_stem().and_then(|value| value.to_str()) else {
                        continue;
                    };
                    if path.extension().and_then(|v| v.to_str()) != Some("json") || !valid_id(id) {
                        continue;
                    }
                    let job = read_json(&path)?;
                    if job["id"] != id {
                        continue;
                    }
                    if payload
                        .get("project_id")
                        .filter(|v| !v.is_null())
                        .is_some_and(|project| job["project_id"] != *project)
                    {
                        continue;
                    }
                    jobs.push(job);
                }
            }
            jobs.sort_by(|a, b| {
                b["created_at"]
                    .as_str()
                    .unwrap_or("")
                    .cmp(a["created_at"].as_str().unwrap_or(""))
            });
            Ok(Value::Array(jobs))
        }
        "build" => {
            let mut request = payload;
            if !request["job"].is_object() {
                return Err(OfflineError::new("invalid_request", "缺少离线任务信息"));
            }
            if request["job"].get("id").is_none() {
                request["job"]["id"] = json!(new_id()?);
            }
            let (id, project) = request_identity(&request["job"])?;
            let (id, project) = (id.to_owned(), project.to_owned());
            let path = job_path(directory, &id)?;
            if path.exists() {
                return Err(OfflineError::new(
                    "already_exists",
                    "本机任务已存在，请勿重复创建",
                ));
            }
            request["operation"] = json!("build");
            let job = run_worker(
                state,
                worker.ok_or_else(|| OfflineError::new("worker_missing", "缺少离线字幕组件"))?,
                &request,
                Duration::from_secs(300),
            )?;
            validate_job(&job, &id, &project)?;
            atomic_json(&path, &job)?;
            Ok(job)
        }
        "get" | "delete" | "edit" | "export" | "mark_synced" => {
            let (id, project) = request_identity(&payload)?;
            let mut job = get_job(directory, id, project)?;
            let path = job_path(directory, id)?;
            match operation {
                "get" => Ok(job),
                "delete" => {
                    fs::remove_file(path).map_err(|_| OfflineError::storage())?;
                    Ok(json!({"deleted":true,"id":id}))
                }
                "mark_synced" => {
                    let server = payload["server_job_id"]
                        .as_str()
                        .filter(|s| !s.is_empty())
                        .ok_or_else(|| {
                            OfflineError::new("invalid_request", "缺少服务器任务编号")
                        })?;
                    job["server_job_id"] = json!(server);
                    if let Some(value) = payload.get("server_project_id") {
                        let project = value
                            .as_str()
                            .filter(|value| !value.trim().is_empty())
                            .ok_or_else(|| {
                                OfflineError::new("invalid_request", "服务器项目编号无效")
                            })?;
                        job["server_project_id"] = json!(project);
                    }
                    atomic_json(&path, &job)?;
                    Ok(job)
                }
                _ => {
                    let mut request = payload.clone();
                    request["operation"] = json!(operation);
                    request["job"] = job;
                    let result = run_worker(
                        state,
                        worker.ok_or_else(|| {
                            OfflineError::new("worker_missing", "缺少离线字幕组件")
                        })?,
                        &request,
                        Duration::from_secs(300),
                    )?;
                    if operation == "edit" {
                        validate_job(&result, id, project)?;
                        atomic_json(&path, &result)?;
                    }
                    Ok(result)
                }
            }
        }
        _ => Err(OfflineError::new(
            "invalid_operation",
            "不支持的离线字幕操作",
        )),
    }
}
fn validate_job(job: &Value, id: &str, project: &str) -> Result<(), OfflineError> {
    if !job.is_object()
        || job["id"] != id
        || job["project_id"] != project
        || job["local_only"] != true
    {
        return Err(OfflineError::new(
            "invalid_result",
            "离线字幕结果与当前项目不匹配",
        ));
    }
    Ok(())
}

#[tauri::command]
pub async fn offline_subtitles(
    app: tauri::AppHandle,
    state: tauri::State<'_, OfflineSubtitlesState>,
    scope: String,
    operation: String,
    payload: Value,
) -> Result<Value, OfflineError> {
    let state = state.inner().clone();
    if operation == "cancel" {
        return Ok(json!({"cancelled":state.cancel()}));
    }
    tauri::async_runtime::spawn_blocking(move|| {
        if operation=="capability" {return Ok(match runtime_path(&app) {Ok(_)=>json!({"available":true,"architecture":std::env::consts::ARCH}),Err(error)=>json!({"available":false,"architecture":std::env::consts::ARCH,"reason":error.message,"code":error.code})});}
        let base=app.path().app_data_dir().map_err(|_|OfflineError::storage())?;
        let directory=namespace(&base,&scope)?;
        let worker=if ["build","edit","export"].contains(&operation.as_str()) {Some(runtime_path(&app)?)}else{None};
        operate(&directory,&state,&operation,payload,worker.as_deref())
    }).await.map_err(|_|OfflineError::new("worker_failed","离线字幕任务意外中断"))?
}

#[tauri::command]
pub async fn offline_source_path(
    app: tauri::AppHandle,
    state: tauri::State<'_, OfflineSubtitlesState>,
    scope: String,
    id: String,
    project_id: String,
) -> Result<String, OfflineError> {
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let _lock = state
            .storage
            .try_lock()
            .map_err(|_| OfflineError::new("busy", "本机任务正在保存，请稍后重试"))?;
        let base = app
            .path()
            .app_data_dir()
            .map_err(|_| OfflineError::storage())?;
        let job = get_job(&namespace(&base, &scope)?, &id, &project_id)?;
        let path = PathBuf::from(
            job["source_local_path"]
                .as_str()
                .ok_or_else(|| OfflineError::new("source_missing", "本机任务没有源音频路径"))?,
        );
        if !path.is_absolute() || !path.is_file() {
            return Err(OfflineError::new(
                "source_missing",
                "源音频已移动或删除，请重新选择音频",
            ));
        }
        app.asset_protocol_scope()
            .allow_file(&path)
            .map_err(|_| OfflineError::new("source_unavailable", "无法授权试听该任务源音频"))?;
        Ok(path.to_string_lossy().to_string())
    })
    .await
    .map_err(|_| OfflineError::new("storage_error", "读取源音频路径失败"))?
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> PathBuf {
        let path = std::env::temp_dir().join(new_id().unwrap());
        fs::create_dir_all(&path).unwrap();
        path
    }
    #[test]
    fn namespaces_and_project_checks_reject_cross_project_and_path_traversal() {
        let root = fixture();
        let a = namespace(&root, "https://beijing/api/v1").unwrap();
        let b = namespace(&root, "https://shanghai/api/v1").unwrap();
        assert_ne!(a, b);
        assert_eq!(a, namespace(&root, "https://beijing/api/v1/").unwrap());
        let id = new_id().unwrap();
        let job = json!({"id":id,"project_id":"one","local_only":true});
        atomic_json(&job_path(&a, &id).unwrap(), &job).unwrap();
        assert_eq!(get_job(&a, &id, "one").unwrap(), job);
        assert_eq!(get_job(&a, &id, "two").unwrap_err().code, "not_found");
        assert_eq!(get_job(&b, &id, "one").unwrap_err().code, "not_found");
        for bad in [
            "../projects",
            "local-../../tmp/a",
            "local-123",
            "local-00000000-0000-0000-0000-00000000000/",
        ] {
            assert!(job_path(&a, bad).is_err());
        }
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn delete_keeps_media_and_sync_keeps_local_revision() {
        let root = fixture();
        let directory = namespace(&root, "https://beijing/api/v1").unwrap();
        let id = new_id().unwrap();
        let media = root.join("source.wav");
        fs::write(&media, b"source fixture").unwrap();
        let job = json!({"id":id,"project_id":"one","local_only":true,"local_cues":[{"text":"edited"}],"source_local_path":media});
        atomic_json(&job_path(&directory, &id).unwrap(), &job).unwrap();
        let state = OfflineSubtitlesState::default();
        let marked = operate(
            &directory,
            &state,
            "mark_synced",
            json!({"id":id,"project_id":"one","server_job_id":"cloud-1","server_project_id":"cloud-project"}),
            None,
        )
        .unwrap();
        assert_eq!(marked["local_cues"], job["local_cues"]);
        assert_eq!(marked["server_job_id"], "cloud-1");
        assert_eq!(marked["server_project_id"], "cloud-project");
        assert_eq!(operate(&directory, &state, "mark_synced", json!({"id":id,"project_id":"one","server_job_id":"cloud-1","server_project_id":""}), None).unwrap_err().code,"invalid_request");
        assert_eq!(get_job(&directory, &id, "one").unwrap(), marked);
        assert_eq!(
            operate(
                &directory,
                &state,
                "list",
                json!({"project_id":"two"}),
                None
            )
            .unwrap(),
            json!([])
        );
        operate(
            &directory,
            &state,
            "delete",
            json!({"id":id,"project_id":"one"}),
            None,
        )
        .unwrap();
        assert!(media.is_file());
        assert!(!job_path(&directory, &id).unwrap().exists());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn project_cache_is_atomic_and_storage_rejects_concurrent_mutation() {
        let root = fixture();
        let state = OfflineSubtitlesState::default();
        let projects = json!([{"id":"one"}]);
        operate(
            &root,
            &state,
            "save_projects",
            json!({"projects":projects}),
            None,
        )
        .unwrap();
        assert_eq!(
            operate(&root, &state, "projects", json!({}), None).unwrap(),
            projects
        );
        let _guard = state.storage.lock().unwrap();
        assert_eq!(
            operate(&root, &state, "save_projects", json!({"projects":[]}), None)
                .unwrap_err()
                .code,
            "busy"
        );
        assert_eq!(read_json(&root.join("projects.json")).unwrap(), projects);
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn stale_cloud_project_refresh_keeps_new_local_project() {
        let root = fixture();
        let state = OfflineSubtitlesState::default();
        operate(
            &root,
            &state,
            "save_projects",
            json!({"projects":[{"id":"cloud"}]}),
            None,
        )
        .unwrap();
        operate(
            &root,
            &state,
            "save_projects",
            json!({"projects":[{"id":"cloud"},{"id":"local-project", "local_only":true}]}),
            None,
        )
        .unwrap();
        let refreshed = operate(
            &root,
            &state,
            "save_projects",
            json!({"projects":[{"id":"cloud","name":"fresh"}]}),
            None,
        )
        .unwrap();
        assert_eq!(refreshed.as_array().unwrap().len(), 2);
        assert_eq!(refreshed[0]["name"], "fresh");
        assert_eq!(refreshed[1]["id"], "local-project");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    #[ignore = "requires packaged worker and a real Apple recognition fixture in SPEAKER_OFFLINE_FIXTURE"]
    fn frozen_worker_roundtrip_preserves_recognition_and_local_edits() {
        let fixture_path = std::env::var("SPEAKER_OFFLINE_FIXTURE").expect("fixture path");
        let mut payload: Value = serde_json::from_slice(&fs::read(fixture_path).unwrap()).unwrap();
        let recognition = payload["recognition"].clone();
        payload["job"].as_object_mut().unwrap().remove("id");
        let project = payload["job"]["project_id"].as_str().unwrap().to_owned();
        let worker = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("resources/offline-core/speaker-offline");
        let root = fixture();
        let directory = namespace(&root, "https://offline-fixture/api/v1").unwrap();
        let state = OfflineSubtitlesState::default();
        let job = operate(&directory, &state, "build", payload, Some(&worker)).unwrap();
        let id = job["id"].as_str().unwrap();
        assert!(valid_id(id));
        let edited = operate(&directory, &state, "edit", json!({"id":id,"project_id":project,"action":"subtitle_text","changes":[{"index":1,"clean_text":"本机人工校正"}]}), Some(&worker)).unwrap();
        assert_eq!(edited["local_cues"][0]["clean_text"], "本机人工校正");
        assert_eq!(edited["local_recognition"], recognition);
        assert_eq!(get_job(&directory, id, &project).unwrap(), edited);
        for format in ["srt", "vtt", "xml"] {
            let exported = operate(
                &directory,
                &state,
                "export",
                json!({"id":id,"project_id":project,"format":format,"variant":"clean"}),
                Some(&worker),
            )
            .unwrap();
            let content = base64::engine::general_purpose::STANDARD
                .decode(exported["content_base64"].as_str().unwrap())
                .unwrap();
            assert!(!content.is_empty());
        }
        assert_eq!(get_job(&directory, id, &project).unwrap(), edited);
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn malformed_worker_output_does_not_leave_process_active() {
        let state = OfflineSubtitlesState::default();
        let error = run_worker(
            &state,
            Path::new("/usr/bin/true"),
            &json!({}),
            Duration::from_secs(1),
        )
        .unwrap_err();
        assert_eq!(error.code, "invalid_result");
        assert!(state.process.lock().unwrap().is_none());
    }
    #[test]
    fn worker_timeout_cancel_and_shutdown_reap_processes() {
        use std::os::unix::fs::PermissionsExt;
        let root = fixture();
        let helper = root.join("hanging-worker");
        fs::write(&helper, "#!/bin/sh\nexec /bin/sleep 10\n").unwrap();
        fs::set_permissions(&helper, fs::Permissions::from_mode(0o700)).unwrap();
        let state = OfflineSubtitlesState::default();
        assert_eq!(
            run_worker(&state, &helper, &json!({}), Duration::from_millis(60))
                .unwrap_err()
                .code,
            "timeout"
        );
        assert!(state.process.lock().unwrap().is_none());
        let worker_state = state.clone();
        let worker_helper = helper.clone();
        let worker = thread::spawn(move || {
            run_worker(
                &worker_state,
                &worker_helper,
                &json!({}),
                Duration::from_secs(2),
            )
        });
        let deadline = Instant::now() + Duration::from_secs(1);
        while state.process.lock().unwrap().is_none() {
            assert!(Instant::now() < deadline);
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(
            run_worker(&state, &helper, &json!({}), Duration::from_secs(1))
                .unwrap_err()
                .code,
            "busy"
        );
        assert!(state.cancel());
        assert_eq!(worker.join().unwrap().unwrap_err().code, "cancelled");
        state.shutdown();
        assert_eq!(
            run_worker(&state, &helper, &json!({}), Duration::from_secs(1))
                .unwrap_err()
                .code,
            "cancelled"
        );
        assert!(!state.cancel());
        fs::remove_dir_all(root).unwrap();
    }
}
