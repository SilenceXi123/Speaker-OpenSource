from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get('/health')
def health() -> dict[str, object]:
    return {
        'status': 'ok',
        'env': settings.app_env,
        'model': settings.dashscope_asr_model,
        'bucket': settings.oss_bucket,
    }
