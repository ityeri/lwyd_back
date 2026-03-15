import anyio.to_thread
import uvicorn
from fastapi import FastAPI, APIRouter, Path
from pytubefix import YouTube

from lwyd_back.config import Config
from lwyd_back.schemes import VideoInfoResponse


class Server:
    def __init__(self, config: Config):
        self.config: Config = config
        self._app: FastAPI = FastAPI()

    def init(self):

        router = APIRouter(prefix='/api')

        @router.post('/info/{video_id}')
        async def info(video_id: str = Path(min_length=11, max_length=11)) -> VideoInfoResponse:
            yt = anyio.to_thread.run_sync(YouTube, url)

        @router.post('/predownload/{video_id}')
        async def per_process(video_id: str = Path(min_length=11, max_length=11)):
            ...

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
        server = uvicorn.Server(config)
        await server.serve()