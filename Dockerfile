FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /code

# Install dependencies first (better layer caching)
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e .

# Copy the application
COPY app ./app
COPY tests ./tests

EXPOSE 8000

# Default command runs the API; the worker service overrides this in docker-compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
