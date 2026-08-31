import logging
from collections.abc import Callable

from pytubefix import AsyncYouTube, YouTube
from pytubefix.query import StreamQuery

logger = logging.getLogger(__name__)

_CLIENTS = ('WEB', 'IOS', 'TV', 'WEB_EMBED', 'ANDROID_VR')


def _client_url(video_id: str) -> str:
    return f'https://www.youtube.com/watch?v={video_id}'


async def create_youtube_async(video_id: str) -> AsyncYouTube:
    url = _client_url(video_id)
    last_error: Exception | None = None
    for client in _CLIENTS:
        try:
            yt = AsyncYouTube(url, client=client)
            await yt.streams()
            logger.info('youtube client ok: video_id=%s client=%s', video_id, client)
            return yt
        except Exception as exc:
            logger.warning('youtube client failed: video_id=%s client=%s error=%s', video_id, client, exc)
            last_error = exc
    raise last_error


def create_youtube_sync(video_id: str, on_progress: Callable | None = None) -> tuple[YouTube, StreamQuery]:
    url = _client_url(video_id)
    last_error: Exception | None = None
    for client in _CLIENTS:
        try:
            yt = YouTube(url, client=client, on_progress_callback=on_progress)
            streams = yt.streams
            logger.info('youtube client ok: video_id=%s client=%s', video_id, client)
            return yt, streams
        except Exception as exc:
            logger.warning('youtube client failed: video_id=%s client=%s error=%s', video_id, client, exc)
            last_error = exc
    raise last_error
