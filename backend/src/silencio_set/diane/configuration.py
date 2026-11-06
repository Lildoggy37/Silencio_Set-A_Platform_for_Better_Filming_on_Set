"""Load only model settings for an explicitly enabled local preview."""
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr


def model_options(env_file: Path):
    values = dotenv_values(env_file, interpolate=False) if env_file.is_file() else {}
    options = {}
    for suffix in ('API_KEY', 'BASE_URL', 'MODEL'):
        names = ('LLM_' + suffix, 'QWEN_' + suffix) + (('DASHSCOPE_API_KEY',) if suffix == 'API_KEY' else ())
        value = next((os.environ[name] for name in names if name in os.environ),
                     next((values[name] for name in names if name in values), None))
        options['llm_' + suffix.lower()] = SecretStr(value) if suffix == 'API_KEY' and value else value
    return options
