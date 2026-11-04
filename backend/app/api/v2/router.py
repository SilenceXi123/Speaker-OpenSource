from fastapi import APIRouter

from app.api.v2 import projects, transcriptions, voice_cloning, voiceprints

api_router = APIRouter()
api_router.include_router(projects.router, prefix='/projects', tags=['v2-projects'])
api_router.include_router(transcriptions.router, tags=['v2-transcriptions'])
api_router.include_router(voiceprints.router, tags=['v2-voiceprints'])
api_router.include_router(voice_cloning.router, tags=['v2-voice-cloning'])
