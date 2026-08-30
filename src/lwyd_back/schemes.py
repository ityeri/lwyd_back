from pydantic import BaseModel, Field


class StreamInfo(BaseModel):
    itag: int
    type: str
    resolution: str | None = None
    abr: str | None = None
    codec: str | None = None
    container: str | None = None
    fps: int | None = None


class VideoInfoResponse(BaseModel):
    video_id: str = Field(min_length=11, max_length=11)
    title: str
    thumbnail_url: str
    duration_seconds: int
    video_streams: list[StreamInfo]
    audio_streams: list[StreamInfo]


class DownloadRequest(BaseModel):
    mode: str = 'both'
    video_resolution: str | None = None
    video_codec: str | None = None
    audio_bitrate: str | None = None
    audio_codec: str | None = None
    container: str = 'mp4'


class PreDownloadResponse(BaseModel):
    video_id: str = Field(min_length=11, max_length=11)
    task_id: str
    status: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: float | None = None
    error: str | None = None
    filename: str | None = None
