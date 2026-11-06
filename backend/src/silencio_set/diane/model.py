"""Model Studio's OpenAI-compatible Chat Completions adapter, with no implicit retries."""
from langchain_openai import ChatOpenAI
from httpx2 import AsyncClient, Client


def create_model(settings, *, http_async_client=None):
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.diane_timeout_seconds,
        max_retries=0,
        use_responses_api=False,
        extra_body={'enable_thinking': False},
        # Each application lifespan owns its transports. LangChain's cached
        # defaults survive Celery jobs even after the previous job closes them.
        http_client=Client(timeout=settings.diane_timeout_seconds),
        http_async_client=http_async_client if http_async_client is not None else AsyncClient(
            timeout=settings.diane_timeout_seconds),
    )
