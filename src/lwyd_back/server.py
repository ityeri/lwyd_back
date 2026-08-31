import asyncio
import logging
import time
from pathlib import Path

import uvicorn
from fastapi import APIRouter, FastAPI, Path
from fastapi.responses import FileResponse
from pytubefix import AsyncYouTube

from lwyd_back.config import Config
from lwyd_back.download_task import DownloadTask
from lwyd_back.enums import AudioCodec, Container, VideoCodec
from lwyd_back.schemas import DownloadRequest, PreDownloadResponse, StreamInfo, TaskStatusResponse, VideoInfoResponse
from lwyd_back.task_status import TaskStatus

_CLIENTS = ('WEB', 'IOS', 'TV', 'WEB_EMBED', 'ANDROID_VR')
_VIDEO_ID = Path(min_length=11, max_length=11)
_GC_MAX_AGE_SECONDS = 24 * 60 * 60
_GC_INTERVAL_SECONDS = 60 * 60


logger = logging.getLogger(__name__)


class Server:
    def __init__(self, config: Config):
        self.config: Config = config
        self._app: FastAPI = FastAPI()
        self._tasks: dict[str, DownloadTask] = {}
        self._gc_task: asyncio.Task | None = None

    def init(self):
        router = APIRouter(prefix='/api')

        @router.post('/info/{video_id}')
        async def info(video_id: str = _VIDEO_ID) -> VideoInfoResponse:
            yt = await _create_youtube(video_id)
            try:
                streams = await yt.streams()
                video_streams = [
                    StreamInfo(
                        itag=stream.itag,
                        type='video',
                        resolution=stream.resolution,
                        codec=', '.join(stream.codecs),
                        container=stream.subtype,
                        fps=stream.fps,
                    )
                    for stream in streams
                    if stream.type == 'video'
                ]
                audio_streams = [
                    StreamInfo(
                        itag=stream.itag,
                        type='audio',
                        abr=stream.abr,
                        codec=', '.join(stream.codecs),
                        container=stream.subtype,
                    )
                    for stream in streams
                    if stream.type == 'audio'
                ]
                return VideoInfoResponse(
                    video_id=video_id,
                    title=await yt.title(),
                    thumbnail_url=await yt.thumbnail_url(),
                    duration_seconds=await yt.length(),
                    video_streams=video_streams,
                    audio_streams=audio_streams,
                )
            finally:
                await yt.http_client.close()

        @router.post('/predownload/{video_id}')
        async def predownload(video_id: str, request: DownloadRequest) -> PreDownloadResponse:
            task = DownloadTask(video_id=video_id, request=request, download_dir=self.config.download_dir)
            self._tasks[task.task_id] = task
            task.start()
            logger.info('download task started: task_id=%s video_id=%s mode=%s container=%s', task.task_id, video_id, request.mode.value, request.container.value)
            return PreDownloadResponse(video_id=video_id, task_id=task.task_id, status=task.status.value)

        @router.get('/task/{task_id}')
        async def task_status(task_id: str) -> TaskStatusResponse:
            task = self._tasks.get(task_id)
            if task is None:
                return TaskStatusResponse(task_id=task_id, status=TaskStatus.ERROR.value, error='task not found')
            return TaskStatusResponse(
                task_id=task_id,
                status=task.status.value,
                progress=task.progress,
                error=task.error,
                filename=task.filename,
            )

        @router.post('/cancel/{task_id}')
        async def cancel(task_id: str) -> TaskStatusResponse:
            task = self._tasks.get(task_id)
            if task is None:
                return TaskStatusResponse(task_id=task_id, status=TaskStatus.ERROR.value, error='task not found')
            task.cancel()
            logger.info('download cancel requested: task_id=%s', task_id)
            return TaskStatusResponse(
                task_id=task_id,
                status=task.status.value,
                progress=task.progress,
                error=task.error,
                filename=task.filename,
            )

        @router.get('/download/{task_id}')
        async def download(task_id: str):
            task = self._tasks.get(task_id)
            if task is None or task.filename is None:
                return TaskStatusResponse(task_id=task_id, status=TaskStatus.ERROR.value, error='file not ready')
            path = self.config.download_dir / task.filename
            if not path.exists():
                return TaskStatusResponse(task_id=task_id, status=TaskStatus.ERROR.value, error='file missing')
            return FileResponse(path, filename=task.filename)

        self._app.include_router(router)
        return self._app

    async def start(self) -> None:
        self._gc_task = asyncio.create_task(self._gc_loop())
        server_config = uvicorn.Config(
            self._app,
            host=self.config.server_host,
            port=self.config.server_port,
            log_level=self.config.log_level,
        )
        await uvicorn.Server(server_config).serve()

    async def _gc_loop(self) -> None:
        while True:
            await asyncio.sleep(_GC_INTERVAL_SECONDS)
            self._gc_downloads()

    def _gc_downloads(self) -> None:
        cutoff = time.time() - _GC_MAX_AGE_SECONDS
        removed = 0
        for path in self.config.download_dir.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        if removed:
            logger.info('download gc: removed %d stale files', removed)


async def _create_youtube(video_id: str) -> AsyncYouTube:
    url = f'https://www.youtube.com/watch?v={video_id}'
    last_error: Exception | None = None
    for client in _CLIENTS:
        try:
            yt = AsyncYouTube(url, client=client)
            await yt.streams()
            logger.info('youtube client ok: video_id=%s client=%s', video_id, client)
            return yt
        except Exception as exc:
            logger.warning('youtube client failed: video_id=%s client=%s error=%s', video_id, client, exc)
            last_error = exc
    raise last_error
