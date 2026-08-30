import uvicorn
from fastapi import APIRouter, FastAPI, Path
from fastapi.responses import FileResponse
from pytubefix import AsyncYouTube

from lwyd_back.config import Config
from lwyd_back.download_task import DownloadTask
from lwyd_back.schemes import DownloadRequest, PreDownloadResponse, StreamInfo, TaskStatusResponse, VideoInfoResponse
from lwyd_back.task_status import TaskStatus

_CLIENTS = ('ANDROID_VR', 'WEB', 'IOS', 'TV', 'WEB_EMBED')
_VIDEO_ID = Path(min_length=11, max_length=11)


class Server:
    def __init__(self, config: Config):
        self.config: Config = config
        self._app: FastAPI = FastAPI()
        self._tasks: dict[str, DownloadTask] = {}

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
                    if stream.type == 'video' and stream.resolution
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
        async def predownload(video_id: str = _VIDEO_ID, request: DownloadRequest = None) -> PreDownloadResponse:
            task = DownloadTask(video_id, request, self.config.download_dir)
            self._tasks[task.task_id] = task
            task.start()
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

        @router.get('/download/{task_id}')
        async def download(task_id: str) -> FileResponse:
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.DONE or task.filename is None:
                raise RuntimeError('file not ready')
            return FileResponse(self.config.download_dir / task.filename, filename=task.filename)

        self._app.include_router(router)

        for middleware in self.config.middlewares:
            self._app.add_middleware(
                middleware.middleware_class,
                **middleware.kwargs
            )

    async def start(self):
        config = uvicorn.Config(
            self._app,
            host=self.config.server_host,
            port=self.config.server_port,
            log_config=None,
            loop='asyncio'
        )
        srv = uvicorn.Server(config)
        await srv.serve()


async def _create_youtube(video_id: str) -> AsyncYouTube:
    url = f'https://www.youtube.com/watch?v={video_id}'
    last_error: Exception | None = None
    for client in _CLIENTS:
        try:
            yt = AsyncYouTube(url, client=client)
            await yt.streams()
            return yt
        except Exception as exc:
            last_error = exc
    raise last_error
