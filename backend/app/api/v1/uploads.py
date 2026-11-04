from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.schemas.upload import UploadResponse
from app.services.oss_storage import upload_file_to_oss

router = APIRouter()


@router.post('', response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_source_file(file: UploadFile = File(...)) -> UploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail='缺少文件名')
    return await upload_file_to_oss(file)
