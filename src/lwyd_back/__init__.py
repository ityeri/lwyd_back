import asyncio
import logging

import reger

from lwyd_back.config import Config, MiddlewareMeta, get_dotenv_config
from lwyd_back.enums import AudioCodec, Container, Mode, VideoCodec
from lwyd_back.server import Server
from lwyd_back.task_status import TaskStatus


def main():
    config: Config = get_dotenv_config()
    reger.setup_logging(level=config.log_level.upper())
    server = Server(config)
    server.init()
    asyncio.run(server.start())


__all__ = [
    'Server',
    'Config',
    'MiddlewareMeta',
    'get_dotenv_config',
    'TaskStatus',
    'main',
]
