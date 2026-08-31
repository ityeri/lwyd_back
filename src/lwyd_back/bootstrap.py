import asyncio
import logging

import reger

from lwyd_back.api import ApiServer
from lwyd_back.config import Config
from lwyd_back.gc import DownloadGc
from lwyd_back.root_container import RootContainer

_logger = logging.getLogger(__name__)


class Bootstrapper:
    def __init__(self, root_container: RootContainer):
        self.root_container: RootContainer = root_container
        self.api_server: ApiServer = self.root_container.api_server()
        self.download_gc: DownloadGc = self.root_container.download_gc()
        self.config: Config = self.root_container.config()

    def run(self):
        asyncio.run(self.arun())

    async def arun(self):
        _logger.info('Setting up logging...')
        self.setup_logging()
        _logger.info('Logging setup is complete')

        _logger.info('Initializing API server...')
        self.api_server.init()
        _logger.info('API server is ready')

        _logger.info('Starting DownloadGc...')
        self.download_gc.start(asyncio.get_running_loop())
        _logger.info('DownloadGc has started')

        _logger.info('Serving API server...')
        await self.api_server.start()

    def setup_logging(self):
        reger.setup_logging(level=self.config.log_level.upper())
