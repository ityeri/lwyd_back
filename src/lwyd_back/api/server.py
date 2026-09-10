import asyncio
import logging

import uvicorn
import ydpy
from fastapi import APIRouter, FastAPI, HTTPException, Path
from fastapi.responses import FileResponse
from yspy import Video, VideoState

from lwyd_back.api.schemas import DownloadRequest, DownloadStartResponse, StreamInfo, TaskStatusResponse, \
    VideoInfoResponse
from lwyd_back.config import Config
from lwyd_back.download_task import (
    DownloadSpec, DownloadTask, TaskStatus,
    copy_containers_for_audio, copy_containers_for_video,
    to_lwyd_audio_codec, to_lwyd_video_codec,
)

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
        async def info(video_id: str = Path(min_length=11, max_length=11)) -> VideoInfoResponse:
            video_task = asyncio.create_task(Video.aget(video_id))
            streams_task = asyncio.create_task(ydpy.PlayableVideo.afetch(video_id))
            video, state = await video_task
            if state is not VideoState.OK:
                logger.warning('video unavailable: video_id=%s state=%s', video_id, state.value)
                raise HTTPException(status_code=404, detail=f'video unavailable: {state.value}')
            pv = await streams_task
            video_streams = [
                StreamInfo(
                    itag=fmt.itag,
                    type='video',
                    resolution=f'{fmt.height}p' if fmt.height else None,
                    codec=fmt.codecs,
                    container=fmt.container.value if fmt.container else None,
                    fps=fmt.fps,
                    copy_containers=copy_containers_for_video(to_lwyd_video_codec(fmt.video_codec)),
                )
                for fmt in pv.formats
                if fmt.is_video and not fmt.has_drm
            ]
            audio_streams = [
                StreamInfo(
                    itag=fmt.itag,
                    type='audio',
                    abr=f'{round((fmt.bitrate or 0) / 1000)}kbps' if fmt.bitrate else None,
                    codec=fmt.codecs,
                    container=fmt.container.value if fmt.container else None,
                    copy_containers=copy_containers_for_audio(to_lwyd_audio_codec(fmt.audio_codec)),
                )
                for fmt in pv.formats
                if fmt.is_audio and not fmt.has_drm
            ]
            thumbnail_url = ''
            if video is not None and video.thumbnails:
                thumbnail_url = max(video.thumbnails, key=lambda t: t.width).url
            return VideoInfoResponse(
                video_id=video_id,
                title=pv.title or '',
                thumbnail_url=thumbnail_url,
                duration_seconds=(pv.duration_ms or 0) // 1000,
                video_streams=video_streams,
                audio_streams=audio_streams,
            )

        @router.post('/download/{video_id}')
        async def download_start(video_id: str, request: DownloadRequest) -> DownloadStartResponse:
            video, state = await Video.aget(video_id)
            if state is not VideoState.OK:
                logger.warning('video unavailable: video_id=%s state=%s', video_id, state.value)
                raise HTTPException(status_code=404, detail=f'video unavailable: {state.value}')
            spec = DownloadSpec(
                mode=request.mode,
                container=request.container,
                video_resolution=request.video_resolution,
                video_codec=request.video_codec,
                audio_bitrate=request.audio_bitrate,
                audio_codec=request.audio_codec,
            )
            duration_ms = (video.length_seconds or 0) * 1000 if video is not None else 0
            task = DownloadTask(video_id=video_id, spec=spec, media_duration_ms=duration_ms,
                                download_dir=self.config.download_dir)
            self._tasks[task.task_id] = task
            task.start()
            logger.info('download task started: task_id=%s video_id=%s mode=%s container=%s', task.task_id, video_id,
                        request.mode.value, request.container.value)
            return DownloadStartResponse(video_id=video_id, task_id=task.task_id, status=task.status.value)

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
        async def download_file(task_id: str):
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
            log_config=None  # To make uvicorn using the root logger setting
        )
        await uvicorn.Server(server_config).serve()
