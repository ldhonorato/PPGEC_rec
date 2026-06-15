#!/bin/sh
set -e

if [ "$1" != "celery" ]; then
  # Apply database migrations
  python manage.py migrate --noinput

  # Collect static files
  python manage.py collectstatic --noinput
fi

# Execute the container command (e.g. gunicorn)
exec "$@"
