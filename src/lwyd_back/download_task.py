from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pytubefix.query import StreamQuery
from pytubefix.streams import Stream
from yt_dlp import YoutubeDL

from lwyd_back.youtube_fetcher import create_youtube_sync

if TYPE_CHECKING:
    from lwyd_back.api.schemas import DownloadRequest

logger = logging.getLogger(__name__)


class TaskStatus(StrEnum):
    WAIT = 'WAIT'
    FETCHING = 'FETCHING'
    DOWNLOADING = 'DOWNLOADING'
    PROCESSING = 'PROCESSING'
    DONE = 'DONE'
    ERROR = 'ERROR'
    CANCELLED = 'CANCELLED'


class Mode(StrEnum):
    VIDEO = 'video'
    AUDIO = 'audio'
    BOTH = 'both'


class VideoCodec(StrEnum):
    H264 = 'h264'
    VP9 = 'vp9'
    AV01 = 'av01'

    @property
    def ffmpeg_encoder(self) -> str:
        encoders = {
            'h264': 'libx264',
            'vp9': 'libvpx-vp9',
            'av01': 'libaom-av1',
        }
        return encoders[self.value]


class AudioCodec(StrEnum):
    AAC = 'aac'
    OPUS = 'opus'
    VORBIS = 'vorbis'
    MP3 = 'mp3'

    @property
    def ffmpeg_encoder(self) -> str:
        encoders = {
            'aac': 'aac',
            'opus': 'libopus',
            'vorbis': 'libvorbis',
            'mp3': 'libmp3lame',
        }
        return encoders[self.value]


class Container(StrEnum):
    MP4 = 'mp4'
    WEBM = 'webm'
    MKV = 'mkv'
    MOV = 'mov'
    MP3 = 'mp3'
    WAV = 'wav'
    FLAC = 'flac'
    OGG = 'ogg'
    M4A = 'm4a'

    @property
    def is_audio_only(self) -> bool:
        return self in (Container.MP3, Container.WAV, Container.FLAC, Container.OGG, Container.M4A)

    @property
    def video_codecs(self) -> frozenset[VideoCodec]:
        if self in (Container.MP4, Container.MOV):
            return frozenset({VideoCodec.H264})
        if self == Container.WEBM:
            return frozenset({VideoCodec.VP9, VideoCodec.AV01})
        return frozenset({VideoCodec.H264, VideoCodec.VP9, VideoCodec.AV01})

    @property
    def audio_codecs(self) -> frozenset[AudioCodec]:
        if self in (Container.MP4, Container.MOV):
            return frozenset({AudioCodec.AAC})
        if self == Container.WEBM:
            return frozenset({AudioCodec.OPUS, AudioCodec.VORBIS})
        if self == Container.MP3:
            return frozenset({AudioCodec.MP3})
        return frozenset({AudioCodec.AAC, AudioCodec.OPUS, AudioCodec.VORBIS})

    @property
    def ffmpeg_audio_encoder(self) -> str | None:
        encoders = {
            'mp3': 'libmp3lame',
            'wav': 'pcm_s16le',
            'flac': 'flac',
            'ogg': 'libvorbis',
            'm4a': 'aac',
        }
        return encoders.get(self.value)

    @property
    def ytdlp_preferred_codec(self) -> str | None:
        codecs = {
            'mp3': 'mp3',
            'wav': 'wav',
            'flac': 'flac',
            'ogg': 'vorbis',
            'm4a': 'aac',
        }
        return codecs.get(self.value)


@dataclass
class DownloadedMedia:
    title: str
    video_path: Path | None
    audio_path: Path | None
    video_stream: Stream | None = None
    audio_stream: Stream | None = None


@dataclass
class DownloadTask:
    video_id: str
    request: DownloadRequest
    download_dir: Path
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: TaskStatus = TaskStatus.WAIT
    progress: float | None = None
    error: str | None = None
    filename: str | None = None
    task: asyncio.Task | None = field(default=None, init=False)

    def start(self) -> None:
        self.status = TaskStatus.WAIT
        self.progress = 0.0
        self.task = asyncio.create_task(self._run())

    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()

    async def _run(self) -> None:
        work_dir = self.download_dir / uuid.uuid4().hex
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            media = await self._acquire_media(work_dir)
            self.status = TaskStatus.PROCESSING
            self.filename = await self._process_media(media)
            self.status = TaskStatus.DONE
            self.progress = 1.0
            logger.info('download finished: task_id=%s filename=%s', self.task_id, self.filename)
        except asyncio.CancelledError:
            self.status = TaskStatus.CANCELLED
            logger.info('download cancelled: task_id=%s', self.task_id)
        except Exception as exc:
            self.status = TaskStatus.ERROR
            self.error = str(exc)
            logger.error('download failed: task_id=%s error=%s', self.task_id, exc)
        finally:
            for path in work_dir.iterdir():
                path.unlink(missing_ok=True)
            work_dir.rmdir()

    async def _acquire_media(self, work_dir: Path) -> DownloadedMedia:
        try:
            return await self._acquire_pytubefix(work_dir)
        except asyncio.CancelledError:
            raise
        except Exception as pytubefix_exc:
            logger.warning('pytubefix download failed, falling back to yt-dlp: task_id=%s error=%s', self.task_id, pytubefix_exc)
            return await self._acquire_ytdlp(work_dir)

    async def _acquire_pytubefix(self, work_dir: Path) -> DownloadedMedia:
        self.status = TaskStatus.FETCHING
        yt, streams = await asyncio.to_thread(create_youtube_sync, self.video_id, self._on_progress)
        video_stream = self._pick_video_stream(streams) if self.request.mode in (Mode.VIDEO, Mode.BOTH) else None
        audio_stream = self._pick_audio_stream(streams) if self.request.mode in (Mode.AUDIO, Mode.BOTH) else None
        self.status = TaskStatus.DOWNLOADING
        video_path = await self._download_stream(video_stream, work_dir, 'video')
        audio_path = await self._download_stream(audio_stream, work_dir, 'audio')
        return DownloadedMedia(title=yt.title, video_path=video_path, audio_path=audio_path, video_stream=video_stream, audio_stream=audio_stream)

    async def _acquire_ytdlp(self, work_dir: Path) -> DownloadedMedia:
        self.status = TaskStatus.FETCHING
        url = f'https://www.youtube.com/watch?v={self.video_id}'
        container = self.request.container
        mode = self.request.mode
        resolution = self._to_int(self.request.video_resolution)
        output_template = str(work_dir / 'download.%(ext)s')

        if mode == Mode.AUDIO or container.is_audio_only:
            format_selector = 'bestaudio/best'
        elif mode == Mode.VIDEO:
            format_selector = f'bestvideo[height<={resolution}]/bestvideo/best' if resolution else 'bestvideo/best'
        else:
            format_selector = f'bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]/best' if resolution else 'bestvideo+bestaudio/best'

        if container.is_audio_only:
            options = {
                'quiet': True,
                'no_warnings': True,
                'format': format_selector,
                'outtmpl': output_template,
                'noprogress': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': container.ytdlp_preferred_codec,
                    'preferredquality': '192',
                }],
                'progress_hooks': [self._on_ytdlp_progress],
            }
        else:
            options = {
                'quiet': True,
                'no_warnings': True,
                'format': format_selector,
                'outtmpl': output_template,
                'merge_output_format': container.value,
                'noprogress': True,
                'progress_hooks': [self._on_ytdlp_progress],
            }

        def run() -> tuple[Path, str]:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                path = Path(filename)
                if not path.exists() and info.get('requested_downloads'):
                    path = Path(info['requested_downloads'][0].get('filepath', str(path)))
                title = info.get('title') or self.video_id
                return path, title

        self.status = TaskStatus.DOWNLOADING
        path, title = await asyncio.to_thread(run)
        is_audio = mode == Mode.AUDIO or container.is_audio_only
        return DownloadedMedia(title=title, video_path=None if is_audio else path, audio_path=path if is_audio else None)

    async def _download_stream(self, stream: Stream | None, work_dir: Path, name: str) -> Path | None:
        if stream is None:
            return None
        path = await asyncio.to_thread(stream.download, output_path=str(work_dir), filename=name, skip_existing=False)
        return Path(path)

    def _on_progress(self, stream, chunk: bytes, bytes_remaining: int) -> None:
        total = stream.filesize or stream.filesize_approx
        if total:
            self.progress = min(0.99, max(self.progress or 0, 1.0 - bytes_remaining / total))

    def _on_ytdlp_progress(self, data: dict) -> None:
        if data.get('status') == 'downloading':
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            downloaded = data.get('downloaded_bytes', 0)
            if total:
                self.progress = min(0.99, max(self.progress or 0, downloaded / total))
        elif data.get('status') == 'finished':
            self.progress = 0.99

    async def _process_media(self, media: DownloadedMedia) -> str:
        output_name = f'{self._sanitize(media.title)}.{self.request.container.value}'
        output_path = self.download_dir / output_name
        await self._run_ffmpeg(media.video_path, media.audio_path, media.video_stream, media.audio_stream, output_path)
        return output_name

    async def _run_ffmpeg(self, video_path: Path | None, audio_path: Path | None, video_stream: Stream | None, audio_stream: Stream | None, output_path: Path) -> None:
        container = self.request.container
        command = ['ffmpeg', '-y']
        if video_path:
            command += ['-i', str(video_path)]
        if audio_path:
            command += ['-i', str(audio_path)]
        if video_path and audio_path:
            command += ['-map', '0:v:0', '-map', '1:a:0']
            command += self._merge_codec_args(container, video_stream, audio_stream)
            if container in (Container.MP4, Container.MOV, Container.WEBM):
                command += ['-shortest']
        elif audio_path and not video_path:
            command += ['-vn']
            if container.ffmpeg_audio_encoder:
                command += ['-c:a', container.ffmpeg_audio_encoder]
            elif container in (Container.MP4, Container.MOV):
                command += ['-c:a', AudioCodec.AAC.ffmpeg_encoder]
            else:
                command += ['-c', 'copy']
        elif video_path and not audio_path:
            command += ['-an']
            video_codec = self._video_codec_of(video_stream)
            if container == Container.MKV or video_codec == VideoCodec.H264:
                command += ['-c:v', 'copy']
            elif container in (Container.MP4, Container.MOV):
                command += ['-c:v', VideoCodec.H264.ffmpeg_encoder]
            else:
                command += ['-c:v', VideoCodec.VP9.ffmpeg_encoder]
        else:
            raise RuntimeError('nothing to download')
        command += [str(output_path)]
        self.progress = 0.99
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors='replace')[-1000:])

    def _merge_codec_args(self, container: Container, video_stream: Stream | None, audio_stream: Stream | None) -> list[str]:
        if container == Container.MKV:
            return ['-c', 'copy']
        video_codec = self._video_codec_of(video_stream)
        audio_codec = self._audio_codec_of(audio_stream)
        if container in (Container.MP4, Container.MOV):
            args = []
            args += ['-c:v', 'copy'] if video_codec == VideoCodec.H264 else ['-c:v', VideoCodec.H264.ffmpeg_encoder]
            args += ['-c:a', 'copy'] if audio_codec == AudioCodec.AAC else ['-c:a', AudioCodec.AAC.ffmpeg_encoder]
            return args
        if container == Container.WEBM:
            args = []
            args += ['-c:v', 'copy'] if video_codec in (VideoCodec.VP9, VideoCodec.AV01) else ['-c:v', VideoCodec.VP9.ffmpeg_encoder]
            args += ['-c:a', 'copy'] if audio_codec in (AudioCodec.OPUS, AudioCodec.VORBIS) else ['-c:a', AudioCodec.OPUS.ffmpeg_encoder]
            return args
        return ['-c', 'copy']

    def _pick_video_stream(self, streams: StreamQuery) -> Stream:
        candidates = [s for s in streams if s.type == 'video' and s.resolution]
        if not candidates:
            raise RuntimeError('no video stream available')
        video_only = [s for s in candidates if not s.is_progressive]
        candidates = video_only or candidates
        target = self._to_int(self.request.video_resolution)
        codec = self.request.video_codec
        if target:
            matched = [s for s in candidates if self._to_int(s.resolution) == target]
            candidates = matched or candidates
        if codec:
            matched = [s for s in candidates if self._video_codec_of(s) == codec]
            candidates = matched or candidates
        return max(candidates, key=lambda s: self._to_int(s.resolution))

    def _pick_audio_stream(self, streams: StreamQuery) -> Stream:
        candidates = [s for s in streams if s.type == 'audio']
        if not candidates:
            raise RuntimeError('no audio stream available')
        target = self._to_int(self.request.audio_bitrate)
        codec = self.request.audio_codec
        if target:
            matched = [s for s in candidates if self._to_int(s.abr) == target]
            candidates = matched or candidates
        if codec:
            matched = [s for s in candidates if self._audio_codec_of(s) == codec]
            candidates = matched or candidates
        return max(candidates, key=lambda s: self._to_int(s.abr))

    @staticmethod
    def _video_codec_of(stream: Stream | None) -> VideoCodec | None:
        if stream is None:
            return None
        for codec in stream.codecs:
            lowered = codec.lower()
            if 'avc1' in lowered or 'h264' in lowered:
                return VideoCodec.H264
            if 'vp9' in lowered or 'vp8' in lowered:
                return VideoCodec.VP9
            if 'av01' in lowered:
                return VideoCodec.AV01
        return None

    @staticmethod
    def _audio_codec_of(stream: Stream | None) -> AudioCodec | None:
        if stream is None:
            return None
        for codec in stream.codecs:
            lowered = codec.lower()
            if 'mp4a' in lowered or 'aac' in lowered:
                return AudioCodec.AAC
            if 'opus' in lowered:
                return AudioCodec.OPUS
            if 'vorbis' in lowered:
                return AudioCodec.VORBIS
            if 'mp3' in lowered:
                return AudioCodec.MP3
        return None

    @staticmethod
    def _sanitize(title: str) -> str:
        return re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:80]

    @staticmethod
    def _to_int(value: str | None) -> int:
        if not value:
            return 0
        match = re.search(r'\d+', value)
        return int(match.group()) if match else 0
