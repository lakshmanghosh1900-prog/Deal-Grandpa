FROM python:3.11.2-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY Makefile ./
RUN python -m pip install --no-cache-dir --upgrade pip==24.3.1 && python -m pip install --no-cache-dir ".[test]"
RUN mkdir -p /app/var
EXPOSE 8000
CMD ["uvicorn", "deal_grandpa.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
