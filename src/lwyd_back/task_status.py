from enum import Enum


class TaskStatus(Enum):
    WAIT = 'WAIT'
    PROCESSING = 'PROCESSING'
    DONE = 'DONE'
    ERROR = 'ERROR'
