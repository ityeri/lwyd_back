from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import ydpy
from ydpy import AudioCodec as YdAudioCodec
from ydpy import Container as YdContainer
from ydpy import DownloadOptions, Format, PlayableVideo, StreamingProtocol
from ydpy import VideoCodec as YdVideoCodec

if TYPE_CHECKING:
    from lwyd_back.api.schemas import DownloadRequest

logger = logging.getLogger(__name__)


class TaskStatus(StrEnum):
    WAIT = 'WAIT'
    FETCHING = 'FETCHING'
    DOWNLOADING = 'DOWNLOADING'
    POST_PROCESSING = 'POST_PROCESSING'
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


def _to_lwyd_video_codec(codec: YdVideoCodec | None) -> VideoCodec | None:
    mapping = {
        YdVideoCodec.AVC1: VideoCodec.H264,
        YdVideoCodec.VP8: VideoCodec.VP9,
        YdVideoCodec.VP9: VideoCodec.VP9,
        YdVideoCodec.AV01: VideoCodec.AV01,
    }
    return mapping.get(codec)


def _to_lwyd_audio_codec(codec: YdAudioCodec | None) -> AudioCodec | None:
    mapping = {
        YdAudioCodec.MP4A: AudioCodec.AAC,
        YdAudioCodec.OPUS: AudioCodec.OPUS,
        YdAudioCodec.VORBIS: AudioCodec.VORBIS,
        YdAudioCodec.MP3: AudioCodec.MP3,
    }
    return mapping.get(codec)


@dataclass
class DownloadedMedia:
    title: str
    video_path: Path | None
    audio_path: Path | None
    video_codec: VideoCodec | None = None
    audio_codec: AudioCodec | None = None


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
            self.status = TaskStatus.POST_PROCESSING
            self.progress = 0.7
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
        self.status = TaskStatus.FETCHING
        self.progress = 0.05
        url = f'https://www.youtube.com/watch?v={self.video_id}'
        pv = await PlayableVideo.afetch(url)
        logger.info('streams fetched: task_id=%s client=%s video=%s', self.task_id, pv.client, self.video_id)
        fmt_video, fmt_audio = self._pick_formats(pv.formats)
        self.status = TaskStatus.DOWNLOADING
        self.progress = 0.1
        video_path = await self._download_format(fmt_video, work_dir, 'video')
        audio_path = await self._download_format(fmt_audio, work_dir, 'audio')
        return DownloadedMedia(
            title=pv.title or self.video_id,
            video_path=video_path,
            audio_path=audio_path,
            video_codec=_to_lwyd_video_codec(fmt_video.video_codec) if fmt_video else None,
            audio_codec=_to_lwyd_audio_codec((fmt_audio or fmt_video).audio_codec) if (fmt_audio or fmt_video) else None,
        )

    def _pick_formats(self, formats: list[Format]) -> tuple[Format | None, Format | None]:
        clean = [f for f in formats if not f.has_drm and not f.is_damaged and f.url and f.protocol is StreamingProtocol.HTTPS]
        videos = [f for f in clean if f.is_video]
        audios = [f for f in clean if f.is_audio]
        mode = self.request.mode
        container = self.request.container

        fmt_video: Format | None = None
        fmt_audio: Format | None = None

        if mode in (Mode.VIDEO, Mode.BOTH) and not container.is_audio_only:
            fmt_video = self._pick_video_format(videos)
            # adaptive video stream needs a separate audio stream for muxing
            if fmt_video is not None and fmt_video.audio_codec is None and mode == Mode.BOTH:
                fmt_audio = self._pick_audio_format(audios)
        elif mode == Mode.AUDIO or container.is_audio_only:
            fmt_audio = self._pick_audio_format(audios)

        return fmt_video, fmt_audio

    def _pick_video_format(self, candidates: list[Format]) -> Format | None:
        if not candidates:
            raise RuntimeError('no video stream available')
        video_only = [f for f in candidates if f.audio_codec is None]
        pool = video_only or candidates
        target = self._to_int(self.request.video_resolution)
        codec = self.request.video_codec
        if target:
            matched = [f for f in pool if (f.height or 0) == target]
            pool = matched or pool
        if codec:
            matched = [f for f in pool if _to_lwyd_video_codec(f.video_codec) == codec]
            pool = matched or pool
        return max(pool, key=lambda f: f.height or 0)

    def _pick_audio_format(self, candidates: list[Format]) -> Format | None:
        if not candidates:
            raise RuntimeError('no audio stream available')
        codec = self.request.audio_codec
        pool = candidates
        if codec:
            matched = [f for f in pool if _to_lwyd_audio_codec(f.audio_codec) == codec]
            pool = matched or pool
        target = self._to_int(self.request.audio_bitrate)
        if target:
            return min(pool, key=lambda f: abs((f.bitrate or 0) - target * 1000))
        return max(pool, key=lambda f: f.bitrate or 0)

    async def _download_format(self, fmt: Format | None, work_dir: Path, name: str) -> Path | None:
        if fmt is None:
            return None
        ext = fmt.container.value if fmt.container else 'bin'
        target = work_dir / f'{name}.{ext}'
        options = DownloadOptions(progress=self._on_ydpy_progress)
        result = await fmt.adownload(target, options=options)
        logger.info('stream downloaded: task_id=%s name=%s bytes=%d', self.task_id, name, result.bytes_written)
        return target

    def _on_ydpy_progress(self, progress) -> None:
        if progress.total:
            self.progress = 0.1 + 0.6 * (progress.downloaded / progress.total)

    async def _process_media(self, media: DownloadedMedia) -> str:
        output_name = f'{self._sanitize(media.title)}.{self.request.container.value}'
        output_path = self.download_dir / output_name
        await self._run_ffmpeg(media, output_path)
        return output_name

    async def _run_ffmpeg(self, media: DownloadedMedia, output_path: Path) -> None:
        container = self.request.container
        video_path = media.video_path
        audio_path = media.audio_path
        video_codec = media.video_codec
        audio_codec = media.audio_codec
        command = ['ffmpeg', '-y', '-nostats', '-progress', 'pipe:1']
        if video_path:
            command += ['-i', str(video_path)]
        if audio_path:
            command += ['-i', str(audio_path)]
        if video_path and audio_path:
            command += ['-map', '0:v:0', '-map', '1:a:0']
            command += self._merge_codec_args(container, video_codec, audio_codec)
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
            if container == Container.MKV or video_codec == VideoCodec.H264:
                command += ['-c:v', 'copy']
            elif container in (Container.MP4, Container.MOV):
                command += ['-c:v', VideoCodec.H264.ffmpeg_encoder]
            else:
                command += ['-c:v', VideoCodec.VP9.ffmpeg_encoder]
        else:
            raise RuntimeError('nothing to download')
        command += [str(output_path)]
        self.progress = 0.7
        duration_ms = await self._probe_duration_ms(video_path or audio_path)
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stderr_lines: list[str] = []

        async def drain_stderr() -> None:
            async for line in process.stderr:
                stderr_lines.append(line.decode(errors='replace'))

        stderr_task = asyncio.create_task(drain_stderr())
        async for line in process.stdout:
            text = line.decode(errors='replace').strip()
            if text.startswith('out_time_ms='):
                try:
                    # ffmpeg reports microseconds despite the 'ms' name
                    out_ms = int(text.split('=', 1)[1]) // 1000
                    if duration_ms > 0:
                        ratio = min(1.0, out_ms / duration_ms)
                        self.progress = 0.7 + 0.29 * ratio
                except ValueError:
                    pass
        await stderr_task
        await process.wait()
        if process.returncode != 0:
            raise RuntimeError(''.join(stderr_lines)[-1000:])

    def _merge_codec_args(self, container: Container, video_codec: VideoCodec | None, audio_codec: AudioCodec | None) -> list[str]:
        if container == Container.MKV:
            return ['-c', 'copy']
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

    async def _probe_duration_ms(self, path: Path | None) -> int:
        if path is None:
            return 0
        try:
            process = await asyncio.create_subprocess_exec(
                'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await process.communicate()
            return int(float(stdout.decode().strip()) * 1000)
        except Exception:
            return 0

    @staticmethod
    def _sanitize(title: str) -> str:
        return re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:80]

    @staticmethod
    def _to_int(value: str | None) -> int:
        if not value:
            return 0
        match = re.search(r'\d+', value)
        return int(match.group()) if match else 0
