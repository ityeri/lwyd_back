import os
from dataclasses import dataclass
from typing import Any

import dotenv
from starlette.middleware.cors import CORSMiddleware


@dataclass
class MiddlewareMeta:
    middleware_class: type
    kwargs: dict[str, Any]

@dataclass
class Config:
    server_host: str
    server_port: int
    middlewares: list[MiddlewareMeta]

def get_dotenv_config() -> Config:
    dotenv.load_dotenv()
    allow_origins = os.getenv('SERVER_ALLOW_ORIGINS').split()

    return Config(
        server_host=os.getenv('SERVER_HOST'),
        server_port=int(os.getenv('SERVER_PORT')),
        middlewares=[
            MiddlewareMeta(
                CORSMiddleware,
                {
                    'allow_origins': allow_origins,
                    'allow_credentials': False,
                    'allow_methods': ['*'],
                    'allow_headers': ['*']
                }
            )
        ]
    )