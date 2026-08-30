from enum import Enum


class TaskStatus(Enum):
    WAIT = 'WAIT'
    FETCHING = 'FETCHING'
    DOWNLOADING = 'DOWNLOADING'
    PROCESSING = 'PROCESSING'
    DONE = 'DONE'
    ERROR = 'ERROR'
    CANCELLED = 'CANCELLED'
