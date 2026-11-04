use serde::Serialize;
use serde_json::{json, Value};
use std::{
    ffi::OsString,
    io::Read,
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

const OUTPUT_LIMIT: u64 = 64 * 1024 * 1024;

#[derive(Debug, Serialize)]
pub struct SpeechError {
    code: String,
    message: String,
}

impl SpeechError {
    fn new(code: &str, message: &str) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

struct ActiveProcess {
    child: Child,
    cancelled: bool,
}

#[derive(Clone, Default)]
pub struct LocalSpeechState(Arc<Mutex<Option<ActiveProcess>>>, Arc<AtomicBool>);

impl LocalSpeechState {
    pub fn shutdown(&self) {
        self.1.store(true, Ordering::SeqCst);
        self.cancel();
    }
    pub fn cancel(&self) -> bool {
        let mut active = self.0.lock().unwrap_or_else(|error| error.into_inner());
        if let Some(process) = active.as_mut() {
            process.cancelled = true;
            let _ = process.child.kill();
            return true;
        }
        false
    }
}

fn supports_system(version: &str) -> bool {
    version
        .trim()
        .split('.')
        .next()
        .and_then(|part| part.parse::<u32>().ok())
        .is_some_and(|major| major >= 26)
}

fn system_version() -> String {
    if !cfg!(target_os = "macos") {
        return std::env::consts::OS.to_string();
    }
    Command::new("/usr/bin/sw_vers")
        .arg("-productVersion")
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .unwrap_or_default()
}

fn unavailable(version: &str, reason: &str) -> Value {
    json!({"engine":"apple_speech", "locale":"zh_CN", "system_version":version,
        "min_system_version":"26.0", "speech_available":false, "model_installed":false,
        "available":false, "download_available":false, "reason":reason})
}

fn helper_path(app: &tauri::AppHandle) -> Result<PathBuf, SpeechError> {
    let bundled = app
        .path()
        .resolve("speaker-speech", tauri::path::BaseDirectory::Resource)
        .map_err(|_| SpeechError::new("helper_missing", "无法定位 Apple 本机识别组件"))?;
    if bundled.is_file() {
        return Ok(bundled);
    }
    #[cfg(debug_assertions)]
    {
        let development =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/speaker-speech");
        if development.is_file() {
            return Ok(development);
        }
    }
    Err(SpeechError::new(
        "helper_missing",
        "客户端缺少 Apple 本机识别组件，请重新安装客户端",
    ))
}

fn run_process(
    state: LocalSpeechState,
    executable: &Path,
    args: &[OsString],
    timeout: Duration,
) -> Result<Value, SpeechError> {
    let (stdout, stderr) = {
        let mut active = state.0.lock().unwrap_or_else(|error| error.into_inner());
        if state.1.load(Ordering::SeqCst) {
            return Err(SpeechError::new(
                "cancelled",
                "客户端正在退出，已取消本机识别",
            ));
        }
        if active.is_some() {
            return Err(SpeechError::new(
                "busy",
                "已有本机识别或模型准备任务，请等待或取消后重试",
            ));
        }
        let mut child = Command::new(executable)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|_| SpeechError::new("helper_unavailable", "无法启动 Apple 本机识别组件"))?;
        let stdout = child.stdout.take().expect("piped stdout");
        let stderr = child.stderr.take().expect("piped stderr");
        *active = Some(ActiveProcess {
            child,
            cancelled: false,
        });
        (stdout, stderr)
    };
    let (send, receive) = mpsc::channel();
    let reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = stdout.take(OUTPUT_LIMIT + 1).read_to_end(&mut bytes);
        let _ = send.send((result, bytes));
    });
    let errors = thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = stderr.take(64 * 1024).read_to_end(&mut bytes);
    });
    let start = Instant::now();
    let mut captured = None;
    let mut failure = None;
    let status = loop {
        if captured.is_none() {
            if let Ok((result, bytes)) = receive.try_recv() {
                if result.is_err() || bytes.len() as u64 > OUTPUT_LIMIT {
                    failure = Some(SpeechError::new(
                        "invalid_result",
                        "本机识别结果过大或无法读取",
                    ));
                }
                captured = Some(bytes);
            }
        }
        let mut active = state.0.lock().unwrap_or_else(|error| error.into_inner());
        let process = active.as_mut().expect("worker owns active process");
        if process.cancelled {
            failure = Some(SpeechError::new("cancelled", "已取消本机识别或模型准备"));
        } else if start.elapsed() >= timeout {
            failure = Some(SpeechError::new(
                "timeout",
                "本机识别或模型准备超时，请重试",
            ));
        }
        if failure.is_some() {
            let _ = process.child.kill();
            let status = process.child.wait();
            active.take();
            break status;
        }
        match process.child.try_wait() {
            Ok(Some(status)) => {
                active.take();
                break Ok(status);
            }
            Ok(None) => {}
            Err(error) => {
                let _ = process.child.kill();
                let _ = process.child.wait();
                active.take();
                break Err(error);
            }
        }
        drop(active);
        thread::sleep(Duration::from_millis(40));
    };
    let _ = reader.join();
    let _ = errors.join();
    if let Some(error) = failure {
        return Err(error);
    }
    if captured.is_none() {
        captured = receive
            .recv()
            .ok()
            .and_then(|(result, bytes)| result.ok().map(|_| bytes));
    }
    let bytes = captured
        .filter(|bytes| bytes.len() as u64 <= OUTPUT_LIMIT)
        .ok_or_else(|| SpeechError::new("invalid_result", "无法读取本机识别结果"))?;
    let value: Value = serde_json::from_slice(&bytes)
        .map_err(|_| SpeechError::new("invalid_result", "Apple 本机识别没有返回有效结果"))?;
    if let Some(error) = value.get("error") {
        return Err(SpeechError::new(
            error["code"].as_str().unwrap_or("transcription_failed"),
            error["message"].as_str().unwrap_or("Apple 本机识别失败"),
        ));
    }
    if !status.map(|status| status.success()).unwrap_or(false) {
        return Err(SpeechError::new(
            "transcription_failed",
            "Apple 本机识别意外结束",
        ));
    }
    Ok(value)
}

async fn execute(
    app: tauri::AppHandle,
    state: LocalSpeechState,
    action: &'static str,
    path: Option<String>,
) -> Result<Value, SpeechError> {
    tauri::async_runtime::spawn_blocking(move || {
        let version = system_version();
        if !cfg!(target_os = "macos") || !supports_system(&version) {
            if action == "detect" {
                return Ok(unavailable(
                    &version,
                    "Apple 本机识别需要 macOS 26 或更新系统",
                ));
            }
            return Err(SpeechError::new(
                "unsupported_system",
                "Apple 本机识别需要 macOS 26 或更新系统",
            ));
        }
        let helper = match helper_path(&app) {
            Ok(path) => path,
            Err(error) if action == "detect" => return Ok(unavailable(&version, &error.message)),
            Err(error) => return Err(error),
        };
        let mut args = vec![OsString::from(action)];
        if let Some(path) = path {
            let source = PathBuf::from(path);
            if !source.is_absolute() || !source.is_file() {
                return Err(SpeechError::new(
                    "invalid_audio",
                    "请选择有效的本机音频文件",
                ));
            }
            args.push(source.into_os_string());
        }
        let timeout = Duration::from_secs(match action {
            "detect" => 30,
            "prepare" => 1800,
            _ => 3600,
        });
        run_process(state, &helper, &args, timeout)
    })
    .await
    .map_err(|_| SpeechError::new("transcription_failed", "本机识别任务意外中断"))?
}

#[tauri::command]
pub async fn detect_local_speech(
    app: tauri::AppHandle,
    state: tauri::State<'_, LocalSpeechState>,
) -> Result<Value, SpeechError> {
    execute(app, state.inner().clone(), "detect", None).await
}

#[tauri::command]
pub async fn prepare_local_speech(
    app: tauri::AppHandle,
    state: tauri::State<'_, LocalSpeechState>,
) -> Result<Value, SpeechError> {
    execute(app, state.inner().clone(), "prepare", None).await
}

#[tauri::command]
pub async fn transcribe_local_speech(
    app: tauri::AppHandle,
    state: tauri::State<'_, LocalSpeechState>,
    path: String,
) -> Result<Value, SpeechError> {
    execute(app, state.inner().clone(), "transcribe", Some(path)).await
}

#[tauri::command]
pub fn cancel_local_speech(state: tauri::State<'_, LocalSpeechState>) -> Value {
    json!({"cancelled":state.cancel()})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shutdown_rejects_late_helper_start() {
        let state = LocalSpeechState::default();
        state.shutdown();
        assert_eq!(
            run_process(
                state.clone(),
                Path::new("/usr/bin/true"),
                &[],
                Duration::from_secs(1)
            )
            .unwrap_err()
            .code,
            "cancelled"
        );
        assert!(state.0.lock().unwrap().is_none());
    }
    #[test]
    fn old_system_is_rejected_before_helper_execution() {
        for version in ["12.7.6", "15.7", "", "Darwin"] {
            assert!(!supports_system(version));
        }
        assert!(supports_system("26.0"));
        assert!(supports_system("26.6.2"));
    }
    #[test]
    fn hanging_helper_times_out_and_is_reaped() {
        let state = LocalSpeechState::default();
        let error = run_process(
            state.clone(),
            Path::new("/bin/sleep"),
            &["10".into()],
            Duration::from_millis(80),
        )
        .unwrap_err();
        assert_eq!(error.code, "timeout");
        assert!(state.0.lock().unwrap().is_none());
    }
    #[test]
    fn one_process_can_be_cancelled_without_accepting_a_second() {
        let state = LocalSpeechState::default();
        let worker_state = state.clone();
        let worker = thread::spawn(move || {
            run_process(
                worker_state,
                Path::new("/bin/sleep"),
                &["10".into()],
                Duration::from_secs(10),
            )
        });
        let deadline = Instant::now() + Duration::from_secs(2);
        while state.0.lock().unwrap().is_none() {
            assert!(Instant::now() < deadline);
            thread::sleep(Duration::from_millis(5));
        }
        let error = run_process(
            state.clone(),
            Path::new("/bin/sleep"),
            &["1".into()],
            Duration::from_secs(1),
        )
        .unwrap_err();
        assert_eq!(error.code, "busy");
        assert!(state.cancel());
        assert_eq!(worker.join().unwrap().unwrap_err().code, "cancelled");
        assert!(!state.cancel());
    }
}
