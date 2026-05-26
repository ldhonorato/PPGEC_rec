#!/bin/sh
set -e

# Apply database migrations
python manage.py migrate --noinput

# Collect static files
python manage.py collectstatic --noinput

# Execute the container command (e.g. gunicorn)
exec "$@"
