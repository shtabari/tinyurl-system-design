# services/api/Dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /tinyurl

COPY ./requirements.txt /tinyurl/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /tinyurl/requirements.txt

COPY ./app /tinyurl/app
COPY ./alembic.ini /tinyurl/alembic.ini
COPY ./migrations /tinyurl/migrations

# Drop root: install as root above, run as non-root below.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /tinyurl
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]