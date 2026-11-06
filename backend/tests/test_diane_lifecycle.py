import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from silencio_set.core.config import Settings
from silencio_set.diane.model import create_model


def test_consecutive_worker_lifespans_can_call_model_after_previous_client_closed():
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            payload = json.dumps({'id': 'local-reply', 'object': 'chat.completion', 'created': 1,
                'model': 'test-model', 'choices': [{'index': 0, 'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': '已收到。'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = Settings(_env_file=None, llm_api_key='local-test-key', llm_model='test-model',
        llm_base_url='https://model.example/v1').model_copy(update={
            'llm_base_url': f'http://127.0.0.1:{server.server_port}/v1'})

    async def one_job():
        model = create_model(config)
        try:
            assert (await model.ainvoke('你好')).text == '已收到。'
        finally:
            await model.root_async_client.close()
            model.root_client.close()

    try:
        # Celery solo uses a new event loop and application lifespan for each job.
        for _ in range(3):
            asyncio.run(one_job())
        assert len(calls) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
