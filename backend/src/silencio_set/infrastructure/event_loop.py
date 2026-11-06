"""Windows psycopg async connections require a selector event loop."""
import asyncio
import sys


def new_event_loop():
    return asyncio.SelectorEventLoop() if sys.platform == 'win32' else asyncio.new_event_loop()
