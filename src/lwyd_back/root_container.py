from dependency_injector import containers
from dependency_injector.providers import Singleton

from lwyd_back import config
from lwyd_back.api import ApiServer
from lwyd_back.gc import DownloadGc


class RootContainer(containers.DeclarativeContainer):
    config: Singleton[config.Config] = \
        Singleton(config.get_dotenv_config)

    api_server: Singleton[ApiServer] = \
        Singleton(ApiServer, config=config)

    download_gc: Singleton[DownloadGc] = \
        Singleton(DownloadGc, config=config)
