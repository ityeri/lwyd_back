from pydantic import BaseModel, Field

class VideoInfoResponse(BaseModel):
    resolutions: list[int]
    average_bitrate: list[int]
    bitrate_unit: str

class PreDownloadResponse(BaseModel):
    video_id: str = Field(min_length=11, max_length=11)
    status: str