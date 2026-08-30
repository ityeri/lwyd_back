# lwyd-back

YouTube video/audio downloader backend for [lwyd](https://github.com/ityeri/lwyd-webfront).

## Stack

- Python 3.14 + FastAPI + pytubefix + yt-dlp + ffmpeg
- uv + PEP 517 (`uv_build`), Nix dev shell (flake.nix)
- Logging via [reger](https://pypi.org/project/reger/) (default level INFO)

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

## Download pipeline

1. **pytubefix** (sync `YouTube`) — streams fetched with client fallback `WEB → IOS → TV → WEB_EMBED → ANDROID_VR`; downloads run in threads, ffmpeg merge/convert runs as a subprocess, so the event loop stays free.
2. **yt-dlp fallback** — when pytubefix fails (e.g. SABR-only videos blocked by `PoToken INVALID`), the task automatically retries with yt-dlp, which handles SABR/po_token streams.

`/api/info` uses `AsyncYouTube` for non-blocking metadata lookups.
