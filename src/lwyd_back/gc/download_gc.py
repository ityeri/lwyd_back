import asyncio
import logging
import time
from asyncio import AbstractEventLoop, Task
from pathlib import Path

from lwyd_back.config import Config

logger = logging.getLogger(__name__)

_GC_MAX_AGE_SECONDS = 24 * 60 * 60
_GC_INTERVAL_SECONDS = 60 * 60


class DownloadGc:
    def __init__(self, config: Config, max_age_seconds: float = _GC_MAX_AGE_SECONDS, interval_seconds: float = _GC_INTERVAL_SECONDS):
        self.download_dir: Path = config.download_dir
        self.max_age_seconds: float = max_age_seconds
        self.interval_seconds: float = interval_seconds
        self._task: Task | None = None

    def start(self, running_loop: AbstractEventLoop) -> None:
        self._task = running_loop.create_task(self.run())

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            self.cleanup()

    def cleanup(self) -> None:
        cutoff = time.time() - self.max_age_seconds
        removed = 0
        for path in self.download_dir.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        if removed:
            logger.info('download gc: removed %d stale files', removed)
