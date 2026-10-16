FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect /static/ into the image for WhiteNoise. Settings refuse to load without these
# variables, so this one step gets throwaway values; they are not kept in the image.
RUN DJANGO_SECRET_KEY=collectstatic-only DB_NAME=x DB_USER=x DB_PASSWORD=x \
    python manage.py collectstatic --noinput -v 0

# The app runs as the unprivileged "app" user. The container starts as root only so
# docker/entrypoint.sh can hand /data/media (uploaded documents, never served publicly)
# to "app" when a host mounts it root-owned (Railway volumes), then drops to "app".
RUN useradd --create-home app && mkdir -p /data/media && chown app /data/media
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]

# Hosts such as Railway set PORT. docker-compose.yml runs runserver instead.
EXPOSE 8000
CMD ["sh", "-c", "exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --access-logfile -"]
