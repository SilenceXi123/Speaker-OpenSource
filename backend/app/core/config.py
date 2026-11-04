from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


class Settings(BaseSettings):
    app_env: str = 'dev'
    app_port: int = 8787

    dashscope_api_key: str = ''
    sfm_workspace_id: str = ''
    dashscope_base_url: str = 'https://dashscope.aliyuncs.com'
    dashscope_region: str = 'cn-beijing'
    dashscope_asr_model: str = 'fun-asr'
    dashscope_asr_transient_retry_limit: int = 3
    dashscope_rocketmq_enabled: bool = False
    dashscope_rocketmq_endpoints: str = ''
    dashscope_rocketmq_instance_id: str = ''
    dashscope_rocketmq_topic: str = ''
    dashscope_rocketmq_consumer_group: str = ''
    dashscope_rocketmq_access_key: str = ''
    dashscope_rocketmq_access_secret: str = ''
    dashscope_rocketmq_batch_size: int = 8
    dashscope_rocketmq_invisible_duration_seconds: int = 15
    subtitle_enhancer_enabled: bool = False
    subtitle_enhancer_provider: str = 'dashscope'
    subtitle_enhancer_api_key: str = ''
    subtitle_enhancer_base_url: str = ''
    subtitle_enhancer_model: str = 'qwen3.6-plus'
    subtitle_enhancer_timeout_seconds: int = 30
    subtitle_enhancer_max_batch_chars: int = 2200
    subtitle_enhancer_max_batch_segments: int = 12
    subtitle_enhancer_max_workers: int = 4
    subtitle_enhancer_max_segments_per_job: int = 220
    subtitle_enhancer_max_retry_segments: int = 8
    subtitle_enhancer_job_timeout_seconds: int = 240
    subtitle_enhancer_window_seconds: int = 600
    subtitle_enhancer_window_max_seconds: int = 780
    subtitle_enhancer_window_min_gap_ms: int = 800
    subtitle_pro_asr_model: str = 'qwen3-asr-flash-filetrans'
    subtitle_pro_asr_timeout_seconds: int = 180
    subtitle_pro_asr_poll_interval_seconds: float = 2.0
    provider_cost_facts_enabled: bool = False
    speaker_v2_shadow_enabled: bool = False
    speaker_v2_alpha_token: str = ''

    oss_region: str = ''
    oss_bucket: str = ''
    oss_public_base_url: str = ''
    oss_access_key_id: str = ''
    oss_access_key_secret: str = ''
    oss_upload_prefix: str = 'uploads/source'
    oss_audio_prefix: str = 'uploads/audio'
    oss_subtitle_prefix: str = 'outputs/subtitles'
    oss_render_prefix: str = 'outputs/rendered'
    oss_lower_third_prefix: str = 'assets/lower-thirds'
    oss_voice_sample_prefix: str = 'assets/voice-samples'

    sqlite_path: str = 'backend/data/speaker.db'
    local_upload_dir: str = 'backend/data/tmp/uploads'
    local_audio_dir: str = 'backend/data/tmp/audio'
    local_render_dir: str = 'backend/data/tmp/render'
    local_media_library_dir: str = 'backend/data/library/media'
    local_lower_third_library_dir: str = 'backend/data/library/lower-thirds'
    local_voiceprint_library_dir: str = 'backend/data/library/voiceprints'
    local_voice_clone_library_dir: str = 'backend/data/library/voice-cloning'
    voiceprint_provider: str = 'disabled'
    voiceprint_model_id: str = 'iic/speech_campplus_sv_zh-cn_16k-common'
    voiceprint_3d_speaker_dir: str = 'backend/vendor/3D-Speaker'
    voiceprint_model_cache_dir: str = 'backend/data/models'
    voice_clone_identity_match_threshold: float = 0.90
    voice_cloning_enabled: bool = False
    voice_cloning_provider: str = 'qwen'
    voice_cloning_region: str = 'cn-beijing'
    voice_cloning_cosyvoice_target_model: str = 'cosyvoice-v3.5-plus'
    voice_cloning_qwen_target_model: str = 'qwen3-tts-vc-2026-01-22'
    voice_cloning_sample_target_rate_hz: int = 24000
    voice_cloning_sample_channel_count: int = 1
    voice_cloning_cosyvoice_max_prompt_audio_length: float = 20.0
    voice_cloning_cosyvoice_enable_preprocess: bool = False
    voice_cloning_preview_text: str = '你好，欢迎使用声音复刻功能。'
    admin_username: str = ''
    admin_password_hash: str = ''
    admin_session_secret: str = ''
    admin_session_cookie_name: str = 'speaker_admin_session'
    admin_session_max_age_seconds: int = 28800
    admin_session_cookie_secure: bool = False
    client_update_enabled: bool = True
    client_update_dir: str = 'backend/data/client-updates'
    client_update_public_base_url: str = ''
    storage_cleanup_tmp_days: int = 1
    storage_cleanup_completed_source_days: int = 15
    storage_cleanup_failed_source_days: int = 7
    storage_cleanup_enabled: bool = True
    storage_cleanup_interval_seconds: int = 3600
    storage_cleanup_stale_active_hours: int = 48
    storage_min_free_gb: float = 8.0
    storage_max_used_percent: float = 85.0

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / '.env'),
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    @property
    def sqlite_url(self) -> str:
        db_path = _resolve_project_path(self.sqlite_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f'sqlite:///{db_path}'

    @property
    def upload_dir_path(self) -> Path:
        path = _resolve_project_path(self.local_upload_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def audio_dir_path(self) -> Path:
        path = _resolve_project_path(self.local_audio_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def render_dir_path(self) -> Path:
        path = _resolve_project_path(self.local_render_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def client_update_dir_path(self) -> Path:
        path = _resolve_project_path(self.client_update_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def media_library_dir_path(self) -> Path:
        path = _resolve_project_path(self.local_media_library_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lower_third_library_dir_path(self) -> Path:
        path = _resolve_project_path(self.local_lower_third_library_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def voiceprint_library_dir_path(self) -> Path:
        path = _resolve_project_path(self.local_voiceprint_library_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def voice_clone_library_dir_path(self) -> Path:
        path = _resolve_project_path(self.local_voice_clone_library_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def voiceprint_3d_speaker_dir_path(self) -> Path:
        return _resolve_project_path(self.voiceprint_3d_speaker_dir)

    @property
    def voiceprint_model_cache_dir_path(self) -> Path:
        path = _resolve_project_path(self.voiceprint_model_cache_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def oss_is_configured(self) -> bool:
        return all([
            self.oss_region,
            self.oss_bucket,
            self.oss_public_base_url,
            self.oss_access_key_id,
            self.oss_access_key_secret,
        ])

    @property
    def admin_auth_is_configured(self) -> bool:
        return all([
            self.admin_username.strip(),
            self.admin_password_hash.strip(),
            self.admin_session_secret.strip(),
        ])

    @property
    def dashscope_compatible_base_url(self) -> str:
        return f'{self.dashscope_base_url.rstrip("/")}/compatible-mode/v1'

    @property
    def dashscope_rocketmq_is_configured(self) -> bool:
        return all([
            self.dashscope_rocketmq_endpoints.strip(),
            self.dashscope_rocketmq_topic.strip(),
            self.dashscope_rocketmq_consumer_group.strip(),
        ])

    @property
    def subtitle_enhancer_api_key_value(self) -> str:
        return self.subtitle_enhancer_api_key.strip() or self.dashscope_api_key.strip()

    @property
    def subtitle_enhancer_base_url_value(self) -> str:
        explicit = self.subtitle_enhancer_base_url.strip().rstrip('/')
        if explicit:
            return explicit

        provider = self.subtitle_enhancer_provider.strip().lower()
        if provider == 'deepseek':
            return 'https://api.deepseek.com'
        return self.dashscope_compatible_base_url


settings = Settings()
