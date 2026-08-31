from lwyd_back.bootstrap import Bootstrapper
from lwyd_back.config import Config, MiddlewareMeta, get_dotenv_config
from lwyd_back.download_task import AudioCodec, Container, DownloadTask, Mode, TaskStatus, VideoCodec
from lwyd_back.root_container import RootContainer

from lwyd_back import api
from lwyd_back import gc


def main():
    container = RootContainer()
    bootstrapper = Bootstrapper(container)

    bootstrapper.run()


__all__ = [
    'RootContainer',
    'Bootstrapper',
    'Config',
    'MiddlewareMeta',
    'get_dotenv_config',

    'DownloadTask',
    'TaskStatus',
    'Mode',
    'VideoCodec',
    'AudioCodec',
    'Container',

    'api',
    'gc',

    'main',
]
