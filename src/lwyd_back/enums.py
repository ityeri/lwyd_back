from enum import StrEnum


class Mode(StrEnum):
    VIDEO = 'video'
    AUDIO = 'audio'
    BOTH = 'both'


class VideoCodec(StrEnum):
    H264 = 'h264'
    VP9 = 'vp9'
    AV01 = 'av01'


class AudioCodec(StrEnum):
    AAC = 'aac'
    OPUS = 'opus'
    VORBIS = 'vorbis'
    MP3 = 'mp3'


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
