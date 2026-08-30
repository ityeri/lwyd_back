import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pytubefix import YouTube
from pytubefix.query import StreamQuery
from pytubefix.streams import Stream
from yt_dlp import YoutubeDL

from lwyd_back.schemes import DownloadRequest
from lwyd_back.task_status import TaskStatus

logger = logging.getLogger(__name__)

_CLIENTS = ('WEB', 'IOS', 'TV', 'WEB_EMBED', 'ANDROID_VR')
_AUDIO_ENCODERS = {
    'mp3': 'libmp3lame',
    'wav': 'pcm_s16le',
    'flac': 'flac',
    'ogg': 'libvorbis',
    'm4a': 'aac',
}


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
        try:
            await self._run_with_fallback()
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

    async def _run_with_fallback(self) -> None:
        try:
            await self._run_pytubefix()
        except asyncio.CancelledError:
            raise
        except Exception as pytubefix_exc:
            logger.warning('pytubefix download failed, falling back to yt-dlp: task_id=%s error=%s', self.task_id, pytubefix_exc)
            await self._run_ytdlp()

    async def _run_pytubefix(self) -> None:
        self.status = TaskStatus.FETCHING
        yt, streams = await self._create_youtube()
        await self._process_pytubefix(yt, streams)

    async def _create_youtube(self) -> tuple[YouTube, StreamQuery]:
        def create() -> tuple[YouTube, StreamQuery]:
            url = f'https://www.youtube.com/watch?v={self.video_id}'
            last_error: Exception | None = None
            for client in _CLIENTS:
                try:
                    yt = YouTube(url, client=client, on_progress_callback=self._on_progress)
                    streams = yt.streams
                    logger.info('youtube client ok: video_id=%s client=%s', self.video_id, client)
                    return yt, streams
                except Exception as exc:
                    logger.warning('youtube client failed: video_id=%s client=%s error=%s', self.video_id, client, exc)
                    last_error = exc
            raise last_error
        return await asyncio.to_thread(create)

    def _on_progress(self, stream, chunk: bytes, bytes_remaining: int) -> None:
        total = stream.filesize or stream.filesize_approx
        if total:
            self.progress = min(0.99, max(self.progress or 0, 1.0 - bytes_remaining / total))

    async def _process_pytubefix(self, yt: YouTube, streams: StreamQuery) -> None:
        work_dir = self.download_dir / uuid.uuid4().hex
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            video_stream = self._pick_video_stream(streams) if self.request.mode in ('video', 'both') else None
            audio_stream = self._pick_audio_stream(streams) if self.request.mode in ('audio', 'both') else None
            self.status = TaskStatus.DOWNLOADING
            video_path = await self._download(video_stream, work_dir, 'video')
            audio_path = await self._download(audio_stream, work_dir, 'audio')
            self.status = TaskStatus.PROCESSING
            output_name = f'{self._sanitize(yt.title)}.{self.request.container}'
            output_path = self.download_dir / output_name
            await self._run_ffmpeg(video_path, audio_path, video_stream, audio_stream, output_path)
            self.filename = output_name
        finally:
            for path in work_dir.iterdir():
                path.unlink(missing_ok=True)
            work_dir.rmdir()

    async def _download(self, stream: Stream | None, work_dir: Path, name: str) -> Path | None:
        if stream is None:
            return None
        path = await asyncio.to_thread(stream.download, output_path=str(work_dir), filename=name, skip_existing=False)
        return Path(path)

    def _pick_video_stream(self, streams: StreamQuery) -> Stream:
        candidates = [s for s in streams if s.type == 'video' and s.resolution]
        if not candidates:
            raise RuntimeError('no video stream available')
        video_only = [s for s in candidates if not s.is_progressive]
        candidates = video_only or candidates
        target = self.request.video_resolution
        codec = self.request.video_codec
        if target:
            matched = [s for s in candidates if self._to_int(s.resolution) == self._to_int(target)]
            candidates = matched or candidates
        if codec:
            matched = [s for s in candidates if self._codec_matches(s.codecs, codec)]
            candidates = matched or candidates
        return max(candidates, key=lambda s: self._to_int(s.resolution))

    def _pick_audio_stream(self, streams: StreamQuery) -> Stream:
        candidates = [s for s in streams if s.type == 'audio']
        if not candidates:
            raise RuntimeError('no audio stream available')
        target = self.request.audio_bitrate
        codec = self.request.audio_codec
        if target:
            matched = [s for s in candidates if self._to_int(s.abr) == self._to_int(target)]
            candidates = matched or candidates
        if codec:
            matched = [s for s in candidates if self._codec_matches(s.codecs, codec)]
            candidates = matched or candidates
        return max(candidates, key=lambda s: self._to_int(s.abr))

    async def _run_ffmpeg(self, video_path: Path | None, audio_path: Path | None, video_stream: Stream | None, audio_stream: Stream | None, output_path: Path) -> None:
        container = self.request.container
        command = ['ffmpeg', '-y']
        if video_path:
            command += ['-i', str(video_path)]
        if audio_path:
            command += ['-i', str(audio_path)]
        if video_path and audio_path:
            command += ['-map', '0:v:0', '-map', '1:a:0']
        if video_path and audio_path:
            command += self._merge_codec_args(container, video_stream, audio_stream)
            if container in ('mp4', 'mov', 'webm'):
                command += ['-shortest']
        elif audio_path and not video_path:
            command += ['-vn']
            if container in _AUDIO_ENCODERS:
                command += ['-c:a', _AUDIO_ENCODERS[container]]
            elif container in ('mp4', 'mov'):
                command += ['-c:a', 'aac']
            else:
                command += ['-c', 'copy']
        elif video_path and not audio_path:
            command += ['-an']
            if container == 'mkv' or self._is_h264(video_stream):
                command += ['-c:v', 'copy']
            elif container in ('mp4', 'mov'):
                command += ['-c:v', 'libx264']
            else:
                command += ['-c:v', 'libvpx-vp9']
        else:
            raise RuntimeError('nothing to download')
        command += [str(output_path)]
        self.progress = 0.99
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors='replace')[-1000:])

    def _merge_codec_args(self, container: str, video_stream: Stream | None, audio_stream: Stream | None) -> list[str]:
        if container == 'mkv':
            return ['-c', 'copy']
        if container in ('mp4', 'mov'):
            args = []
            args += ['-c:v', 'copy'] if self._is_h264(video_stream) else ['-c:v', 'libx264']
            args += ['-c:a', 'copy'] if self._is_aac(audio_stream) else ['-c:a', 'aac']
            return args
        if container == 'webm':
            args = []
            args += ['-c:v', 'copy'] if self._is_vp9(video_stream) else ['-c:v', 'libvpx-vp9']
            args += ['-c:a', 'copy'] if self._is_opus(audio_stream) else ['-c:a', 'libopus']
            return args
        return ['-c', 'copy']

    async def _run_ytdlp(self) -> None:
        url = f'https://www.youtube.com/watch?v={self.video_id}'
        container = self.request.container
        mode = self.request.mode
        resolution = self._to_int(self.request.video_resolution)
        work_dir = self.download_dir / uuid.uuid4().hex
        work_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(work_dir / 'download.%(ext)s')

        if mode == 'audio' or container in _AUDIO_ENCODERS:
            format_selector = 'bestaudio/best'
        elif mode == 'video':
            if resolution:
                format_selector = f'bestvideo[height<={resolution}]/bestvideo/best'
            else:
                format_selector = 'bestvideo/best'
        else:
            if resolution:
                format_selector = f'bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]/best'
            else:
                format_selector = 'bestvideo+bestaudio/best'

        if container in _AUDIO_ENCODERS:
            options = {
                'quiet': True,
                'no_warnings': True,
                'format': format_selector,
                'outtmpl': output_template,
                'noprogress': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': container,
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
                'merge_output_format': container,
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
        self.status = TaskStatus.PROCESSING
        output_name = f'{self._sanitize(title)}.{path.suffix.lstrip(".")}'
        final_path = self.download_dir / output_name
        path.replace(final_path)
        self.filename = output_name
        work_dir.rmdir()

    def _on_ytdlp_progress(self, data: dict) -> None:
        if data.get('status') == 'downloading':
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            downloaded = data.get('downloaded_bytes', 0)
            if total:
                self.progress = min(0.99, max(self.progress or 0, downloaded / total))
        elif data.get('status') == 'finished':
            self.progress = 0.99

    @staticmethod
    def _is_h264(stream: Stream | None) -> bool:
        return stream is not None and any('avc1' in c or 'h264' in c for c in stream.codecs)

    @staticmethod
    def _is_aac(stream: Stream | None) -> bool:
        return stream is not None and any('mp4a' in c or 'aac' in c for c in stream.codecs)

    @staticmethod
    def _is_vp9(stream: Stream | None) -> bool:
        return stream is not None and any('vp9' in c or 'vp8' in c or 'av01' in c for c in stream.codecs)

    @staticmethod
    def _is_opus(stream: Stream | None) -> bool:
        return stream is not None and any('opus' in c or 'vorbis' in c for c in stream.codecs)

    @staticmethod
    def _sanitize(title: str) -> str:
        return re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:80]

    @staticmethod
    def _to_int(value: str | None) -> int:
        if not value:
            return 0
        match = re.search(r'\d+', value)
        return int(match.group()) if match else 0

    @staticmethod
    def _codec_matches(codecs: list[str], target: str) -> bool:
        return any(target.lower() in codec.lower() for codec in codecs)
