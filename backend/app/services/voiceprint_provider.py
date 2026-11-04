from __future__ import annotations

from dataclasses import dataclass
import gc
import os
from pathlib import Path
import importlib
import sys
import threading
from typing import Optional

from app.core.config import settings


OFFICIAL_3D_SPEAKER_MODELS = {
    'iic/speech_campplus_sv_zh-cn_16k-common': {
        'revision': 'v1.0.0',
        'model_obj': 'speakerlab.models.campplus.DTDNN.CAMPPlus',
        'model_args': {
            'feat_dim': 80,
            'embedding_size': 192,
        },
        'model_pt': 'campplus_cn_common.bin',
    },
    'iic/speech_eres2net_sv_zh-cn_16k-common': {
        'revision': 'v1.0.5',
        'model_obj': 'speakerlab.models.eres2net.ERes2Net_huge.ERes2Net',
        'model_args': {
            'feat_dim': 80,
            'embedding_size': 192,
        },
        'model_pt': 'pretrained_eres2net_aug.ckpt',
    },
    'iic/speech_eres2netv2_sv_zh-cn_16k-common': {
        'revision': 'v1.0.1',
        'model_obj': 'speakerlab.models.eres2net.ERes2NetV2.ERes2NetV2',
        'model_args': {
            'feat_dim': 80,
            'embedding_size': 192,
            'baseWidth': 26,
            'scale': 2,
            'expansion': 2,
        },
        'model_pt': 'pretrained_eres2netv2.ckpt',
    },
    'iic/speech_eres2netv2w24s4ep4_sv_zh-cn_16k-common': {
        'revision': 'v1.0.1',
        'model_obj': 'speakerlab.models.eres2net.ERes2NetV2.ERes2NetV2',
        'model_args': {
            'feat_dim': 80,
            'embedding_size': 192,
            'baseWidth': 24,
            'scale': 4,
            'expansion': 4,
        },
        'model_pt': 'pretrained_eres2netv2w24s4ep4.ckpt',
    },
}

# ponytail: one process-wide model lock; split by model only if extraction throughput becomes a bottleneck.
_embedding_lock = threading.Lock()


@dataclass
class VoiceprintProviderStatus:
    provider: str
    model_id: str
    available: bool
    message: str


@dataclass
class VoiceprintEmbeddingResult:
    embedding: list[float]
    provider_name: str
    provider_model_id: str
    provider_message: str


def get_voiceprint_provider_status() -> VoiceprintProviderStatus:
    provider = settings.voiceprint_provider.strip().lower()
    if provider in {'', 'disabled'}:
        return VoiceprintProviderStatus(
            provider='disabled',
            model_id=settings.voiceprint_model_id,
            available=False,
            message='当前未启用声纹 provider。样本可以先入库，待接入 3D-Speaker 后再补 embedding。',
        )

    if provider == '3d_speaker_cli':
        speaker_dir = settings.voiceprint_3d_speaker_dir_path
        infer_script = speaker_dir / 'speakerlab' / 'bin' / 'infer_sv.py'
        if not infer_script.exists():
            return VoiceprintProviderStatus(
                provider=provider,
                model_id=settings.voiceprint_model_id,
                available=False,
                message='未找到 3D-Speaker 推理脚本，请先按官方说明准备本地 3D-Speaker 仓库。',
            )
        model_id = settings.voiceprint_model_id
        if model_id not in OFFICIAL_3D_SPEAKER_MODELS:
            return VoiceprintProviderStatus(
                provider=provider,
                model_id=model_id,
                available=False,
                message='当前 3D-Speaker provider 仅支持官方 infer_sv.py 已覆盖的常用声纹模型。',
            )
        try:
            _load_3d_speaker_runtime()
        except Exception as error:
            return VoiceprintProviderStatus(
                provider=provider,
                model_id=model_id,
                available=False,
                message=f'3D-Speaker 运行环境未就绪：{error}',
            )
        return VoiceprintProviderStatus(
            provider=provider,
            model_id=model_id,
            available=True,
            message='3D-Speaker 官方本地运行时已就绪，可直接提取 embedding。',
        )

    return VoiceprintProviderStatus(
        provider=provider,
        model_id=settings.voiceprint_model_id,
        available=False,
        message=f'未知的声纹 provider: {settings.voiceprint_provider}',
    )


def extract_voiceprint_embedding(audio_path: Path) -> VoiceprintEmbeddingResult:
    status = get_voiceprint_provider_status()
    if not status.available:
        raise RuntimeError(status.message)

    if status.provider != '3d_speaker_cli':
        raise RuntimeError(f'暂不支持的声纹 provider: {status.provider}')

    with _embedding_lock:
        embedding = _extract_embedding_with_3d_speaker(audio_path)

    return VoiceprintEmbeddingResult(
        embedding=embedding,
        provider_name=status.provider,
        provider_model_id=settings.voiceprint_model_id,
        provider_message='embedding 提取成功',
    )


def _extract_embedding_with_3d_speaker(audio_path: Path) -> list[float]:
    numpy_module = _import_numpy()
    torch_module = _import_torch()
    runtime = _load_3d_speaker_runtime()
    model_conf = OFFICIAL_3D_SPEAKER_MODELS[settings.voiceprint_model_id]
    model_path = _prepare_local_model_path(model_conf)
    if not model_path.exists():
        raise RuntimeError(f'缺少官方模型权重文件：{model_path.name}')

    pretrained_state = None
    embedding_model = None
    wav = None
    feat = None
    embedding = None
    feature_extractor = None
    try:
        pretrained_state = torch_module.load(model_path, map_location='cpu')
        embedding_model = runtime['dynamic_import'](model_conf['model_obj'])(**model_conf['model_args'])
        embedding_model.load_state_dict(pretrained_state)
        device = torch_module.device('cuda' if torch_module.cuda.is_available() else 'cpu')
        embedding_model.to(device)
        embedding_model.eval()

        wav = _load_voiceprint_audio(audio_path)
        feature_extractor = runtime['FBank'](80, sample_rate=16000, mean_nor=True)
        feat = feature_extractor(wav).unsqueeze(0).to(device)
        with torch_module.no_grad():
            embedding = embedding_model(feat).detach().squeeze(0).cpu().numpy()
        return numpy_module.asarray(embedding, dtype=float).tolist()
    finally:
        pretrained_state = None
        embedding_model = None
        wav = None
        feat = None
        embedding = None
        feature_extractor = None
        gc.collect()
        if torch_module.cuda.is_available():
            torch_module.cuda.empty_cache()
        _trim_process_heap()


def _trim_process_heap() -> None:
    try:
        import ctypes

        libc = ctypes.CDLL(None)
        malloc_trim = getattr(libc, 'malloc_trim', None)
        if malloc_trim is not None:
            malloc_trim(0)
    except Exception:
        pass


def _prepare_local_model_path(model_conf: dict[str, object]) -> Path:
    from modelscope.hub.snapshot_download import snapshot_download

    model_id = settings.voiceprint_model_id
    cache_root = _get_modelscope_cache_dir()
    cache_dir = Path(snapshot_download(model_id, revision=str(model_conf['revision']), cache_dir=str(cache_root)))
    model_filename = str(model_conf['model_pt'])
    model_path = cache_dir / model_filename
    if not model_path.exists():
        raise RuntimeError(f'未在官方模型目录找到权重文件：{model_filename}')
    return model_path


def _get_modelscope_cache_dir() -> Path:
    configured = os.environ.get('MODELSCOPE_CACHE', '').strip()
    if configured:
        cache_dir = Path(configured)
    else:
        cache_dir = settings.voiceprint_model_cache_dir_path / 'modelscope'
        os.environ['MODELSCOPE_CACHE'] = str(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _load_voiceprint_audio(audio_path: Path, target_sample_rate: int = 16000):
    torchaudio_module = _import_torchaudio()
    wav, sample_rate = torchaudio_module.load(str(audio_path))
    if sample_rate != target_sample_rate:
        wav = torchaudio_module.functional.resample(wav, sample_rate, target_sample_rate)
    if wav.shape[0] > 1:
        wav = wav[:1, :]
    return wav


def _load_3d_speaker_runtime() -> dict[str, object]:
    speaker_dir = settings.voiceprint_3d_speaker_dir_path
    if not speaker_dir.exists():
        raise RuntimeError('未找到本地 3D-Speaker 仓库目录')
    speaker_dir_text = str(speaker_dir)
    if speaker_dir_text not in sys.path:
        sys.path.insert(0, speaker_dir_text)
    try:
        processor_module = importlib.import_module('speakerlab.process.processor')
        builder_module = importlib.import_module('speakerlab.utils.builder')
        importlib.import_module('modelscope.hub.snapshot_download')
    except Exception as error:
        raise RuntimeError(str(error)) from error
    return {
        'FBank': getattr(processor_module, 'FBank'),
        'dynamic_import': getattr(builder_module, 'dynamic_import'),
    }


def _import_numpy():
    try:
        return importlib.import_module('numpy')
    except Exception as error:
        raise RuntimeError(f'缺少 numpy 依赖：{error}') from error


def _import_torch():
    try:
        return importlib.import_module('torch')
    except Exception as error:
        raise RuntimeError(f'缺少 torch 依赖：{error}') from error


def _import_torchaudio():
    try:
        return importlib.import_module('torchaudio')
    except Exception as error:
        raise RuntimeError(f'缺少 torchaudio 依赖：{error}') from error
