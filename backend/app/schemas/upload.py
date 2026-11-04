from pydantic import BaseModel


class UploadResponse(BaseModel):
    object_key: str
    public_url: str
    content_type: str
    size_bytes: int
