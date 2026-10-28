FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install -r requirements.lock
COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN python -m pip install --no-deps . \
    && useradd --create-home --uid 10001 silencio \
    && mkdir -p /app/storage \
    && chown silencio:silencio /app/storage
USER silencio
EXPOSE 8000
CMD ["uvicorn", "silencio_set.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
