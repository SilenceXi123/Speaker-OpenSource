from fastapi import APIRouter

from app.core.config import settings
from app.services.dashscope_event_consumer import get_dashscope_event_consumer_status

router = APIRouter()


@router.get('/status')
def config_status() -> dict[str, object]:
    return {
        'app_env': settings.app_env,
        'dashscope': {
            'configured': bool(settings.dashscope_api_key),
            'base_url': settings.dashscope_base_url,
            'region': settings.dashscope_region,
            'model': settings.dashscope_asr_model,
            'rocketmq': {
                'enabled': bool(settings.dashscope_rocketmq_enabled),
                'configured': settings.dashscope_rocketmq_is_configured,
                'endpoints': settings.dashscope_rocketmq_endpoints,
                'instance_id': settings.dashscope_rocketmq_instance_id,
                'topic': settings.dashscope_rocketmq_topic,
                'consumer_group': settings.dashscope_rocketmq_consumer_group,
                'consumer_status': get_dashscope_event_consumer_status(),
            },
        },
        'subtitle_enhancer': {
            'enabled': bool(settings.subtitle_enhancer_enabled),
            'provider': settings.subtitle_enhancer_provider,
            'configured': bool(settings.subtitle_enhancer_api_key_value),
            'base_url': settings.subtitle_enhancer_base_url_value,
            'model': settings.subtitle_enhancer_model,
            'pro_asr_model': settings.subtitle_pro_asr_model,
            'default_strategy': 'enhanced',
            'timeout_seconds': settings.subtitle_enhancer_timeout_seconds,
            'max_batch_chars': settings.subtitle_enhancer_max_batch_chars,
            'max_batch_segments': settings.subtitle_enhancer_max_batch_segments,
            'max_workers': settings.subtitle_enhancer_max_workers,
            'max_segments_per_job': settings.subtitle_enhancer_max_segments_per_job,
            'max_retry_segments': settings.subtitle_enhancer_max_retry_segments,
            'job_timeout_seconds': settings.subtitle_enhancer_job_timeout_seconds,
        },
        'voice_cloning': {
            'enabled': bool(settings.voice_cloning_enabled),
            'provider': settings.voice_cloning_provider,
            'region': settings.voice_cloning_region,
            'target_model': (
                settings.voice_cloning_cosyvoice_target_model
                if settings.voice_cloning_provider == 'cosyvoice'
                else settings.voice_cloning_qwen_target_model
            ),
            'preview_text': settings.voice_cloning_preview_text,
        },
        'oss': {
            'configured': settings.oss_is_configured,
            'region': settings.oss_region,
            'bucket': settings.oss_bucket,
            'public_base_url': settings.oss_public_base_url,
        },
    }
