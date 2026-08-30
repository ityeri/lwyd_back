# lwyd-back

YouTube video/audio downloader backend for [lwyd](https://github.com/ityeri/lwyd-webfront).

## Stack

- Python 3.14 + FastAPI + pytubefix (AsyncYouTube) + ffmpeg
- uv + PEP 517 (`uv_build`), Nix dev shell (flake.nix)

## Setup

```bash
cp .env.example .env   # then edit SERVER_* / DOWNLOAD_DIR
uv sync
uv run lwyd-back
```

Requires `ffmpeg` on PATH.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/info/{video_id}` | Video metadata + available video/audio streams |
| POST | `/api/predownload/{video_id}` | Start a download task, returns `task_id` |
| GET | `/api/task/{task_id}` | Task status / progress polling |
| GET | `/api/download/{task_id}` | Download the finished file |

`predownload` body: `mode` (`video` | `audio` | `both`), `video_resolution`, `video_codec`, `audio_bitrate`, `audio_codec`, `container` (`mp4` | `webm` | `mkv` | `mov` | `mp3` | `wav` | `flac` | `ogg` | `m4a`).

Streams are fetched with `AsyncYouTube`; when the default client gets blocked by YouTube, `WEB`/`IOS`/`TV`/`WEB_EMBED` clients are tried in order. Downloads run in threads, ffmpeg merge/convert runs as a subprocess, so the event loop stays free.
