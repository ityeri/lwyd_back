from asyncio import Task

from pytubefix import YouTube

from lwyd_back.task_status import TaskStatus


class DownloadTask:
    def __init__(self, video_id: str, status: TaskStatus = TaskStatus.WAIT):
        self.video_id: str = video_id
        self.status: TaskStatus = status
        self.progress: float | None = None
        self.task: Task | None = None

    async def _download(self):
        yt = YouTube(f'https://www.youtube.com/watch?v={self.video_id}')

    async def start(self):
        ...
        # self.