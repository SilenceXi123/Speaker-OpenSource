from fastapi import APIRouter

from app.api.v1 import admin, admin_knowledge, canonical_knowledge, client_updates, config, health, lexicon, transcriptions, uploads, voice_cloning, voiceprints

api_router = APIRouter()
api_router.include_router(health.router, tags=['health'])
api_router.include_router(config.router, prefix='/config', tags=['config'])
api_router.include_router(uploads.router, prefix='/uploads', tags=['uploads'])
api_router.include_router(transcriptions.router, prefix='/transcriptions', tags=['transcriptions'])
api_router.include_router(voiceprints.router, prefix='/voiceprints', tags=['voiceprints'])
api_router.include_router(voice_cloning.router, prefix='/voice-cloning', tags=['voice-cloning'])
api_router.include_router(admin.router, prefix='/admin', tags=['admin'])
api_router.include_router(
    admin_knowledge.router,
    prefix='/admin/knowledge',
    tags=['admin-knowledge'],
)
api_router.include_router(client_updates.router, prefix='/client-updates', tags=['client-updates'])
api_router.include_router(lexicon.router, prefix='/lexicon', tags=['lexicon'])
api_router.include_router(
    canonical_knowledge.router,
    prefix='/canonical-knowledge',
    tags=['canonical-knowledge'],
)
