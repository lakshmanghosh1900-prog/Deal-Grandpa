FROM python:3.12.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY .python-version Dockerfile ./
COPY .github ./.github
COPY src ./src
COPY tests ./tests
COPY Makefile docker-compose.yml ./
RUN python -m pip install --no-cache-dir --upgrade pip==24.3.1 uv==0.5.31 && uv sync --locked --extra test
ENV PATH="/app/.venv/bin:$PATH"
RUN mkdir -p /app/var
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn deal_grandpa.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
