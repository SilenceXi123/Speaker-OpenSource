from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import PROJECT_ROOT
from app.models.cost_event import CostEvent  # noqa: F401
from app.models.canonical_passage import CanonicalPassage  # noqa: F401
from app.models.canonical_passage_variant import CanonicalPassageVariant  # noqa: F401
from app.models.canonical_import_batch import CanonicalImportBatch  # noqa: F401
from app.models.job import Job  # noqa: F401
from app.models.job_event import JobEvent  # noqa: F401
from app.models.knowledge_correction_evidence import KnowledgeCorrectionEvidence  # noqa: F401
from app.models.lexicon_candidate import LexiconCandidate  # noqa: F401
from app.models.lexicon_import import LexiconImport  # noqa: F401
from app.models.lexicon_occurrence import LexiconOccurrence  # noqa: F401
from app.models.lexicon_term import LexiconTerm  # noqa: F401
from app.models.lexicon_term_alias import LexiconTermAlias  # noqa: F401
from app.models.lexicon_term_override import LexiconTermOverride  # noqa: F401
from app.models.person import Person  # noqa: F401
from app.models.price_catalog import PriceCatalogEntry  # noqa: F401
from app.models.project import Project  # noqa: F401
from app.models.provider_call import ProviderCall  # noqa: F401
from app.models.speaker_memory import SpeakerMemory  # noqa: F401
from app.models.subtitle_pipeline_run import SubtitlePipelineRun  # noqa: F401
from app.models.voice_clone_profile import VoiceCloneProfile  # noqa: F401
from app.models.voice_clone_identity import VoiceCloneIdentity  # noqa: F401
from app.models.voice_clone_identity_review import VoiceCloneIdentityReview  # noqa: F401
from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask  # noqa: F401
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: F401
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord  # noqa: F401
from app.models.voiceprint_match import VoiceprintMatch  # noqa: F401
from app.models.voiceprint_sample import VoiceprintSample  # noqa: F401
from app.api.v1.router import api_router
from app.api.v2.router import api_router as api_v2_router
from app.core.config import settings
from app.db.schema_bootstrap import create_legacy_tables
from app.db.session import configure_sqlite_engine, engine, ensure_schema
from app.services.dashscope_event_consumer import start_dashscope_event_consumer, stop_dashscope_event_consumer
from app.services.subtitle_pipeline_tasks import (
    start_subtitle_pipeline_recovery_worker,
    stop_subtitle_pipeline_recovery_worker,
)
from app.services.storage_maintenance import (
    start_storage_maintenance_worker,
    stop_storage_maintenance_worker,
)

ADMIN_UI_DIR = PROJECT_ROOT / 'backend' / 'admin-ui'


configure_sqlite_engine(engine, enable_wal=True)
create_legacy_tables(engine)
ensure_schema()

app = FastAPI(
    title='Speaker Backend',
    version='0.1.0',
    docs_url='/docs',
    redoc_url='/redoc',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(api_router, prefix='/api/v1')
app.include_router(api_v2_router, prefix='/api/v2')
app.mount('/admin-assets', StaticFiles(directory=str(ADMIN_UI_DIR)), name='admin-assets')


@app.on_event('startup')
def start_background_consumers() -> None:
    start_dashscope_event_consumer()
    start_subtitle_pipeline_recovery_worker()
    start_storage_maintenance_worker()


@app.on_event('shutdown')
def stop_background_consumers() -> None:
    stop_dashscope_event_consumer()
    stop_subtitle_pipeline_recovery_worker()
    stop_storage_maintenance_worker()


@app.get('/')
def root() -> dict[str, str]:
    return {
        'name': 'speaker-backend',
        'env': settings.app_env,
        'docs': '/docs',
    }


@app.get('/admin', include_in_schema=False)
def get_admin_ui() -> FileResponse:
    return FileResponse(Path(ADMIN_UI_DIR / 'admin.html'))
