import logging

import uvicorn
from fastapi import APIRouter, FastAPI, Path
from fastapi.responses import FileResponse

from lwyd_back.api.schemas import DownloadRequest, PreDownloadResponse, StreamInfo, TaskStatusResponse, VideoInfoResponse
from lwyd_back.config import Config
from lwyd_back.download_task import DownloadTask, TaskStatus
from lwyd_back.youtube_fetcher import create_youtube_async

_VIDEO_ID = Path(min_length=11, max_length=11)

logger = logging.getLogger(__name__)


class ApiServer:
    def __init__(self, config: Config):
        self.config: Config = config
        self._app: FastAPI = FastAPI()
        self._tasks: dict[str, DownloadTask] = {}

    def init(self) -> FastAPI:
        for middleware in self.config.middlewares:
            self._app.add_middleware(middleware.middleware_class, **middleware.kwargs)
        router = APIRouter(prefix='/api')

        @router.post('/info/{video_id}')
        async def info(video_id: str = _VIDEO_ID) -> VideoInfoResponse:
            yt = await create_youtube_async(video_id)
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
        server_config = uvicorn.Config(
            self._app,
            host=self.config.server_host,
            port=self.config.server_port,
            log_level=self.config.log_level,
        )
        await uvicorn.Server(server_config).serve()
