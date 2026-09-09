# lwyd-back

YouTube video/audio downloader backend for [lwyd](https://github.com/ityeri/lwyd-webfront).

## Stack

- Python 3.14 + FastAPI + yspy + ydpy + ffmpeg
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

1. **[yspy](https://github.com/ityeri/yspy)** — video metadata (title, thumbnails, availability) via `Video.aget`.
2. **[ydpy](https://github.com/ityeri/ydpy)** — playable stream fetch (`PlayableVideo.afetch`, multi-client bot bypass) and direct stream download with chunked range requests and progress hooks.
3. **ffmpeg** — merge/convert runs as an async subprocess; codec-aware copy vs transcode decisions driven by ydpy format codecs.

`/api/info` uses `AsyncYouTube` for non-blocking metadata lookups.
