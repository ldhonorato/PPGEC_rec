#!/bin/sh
set -e

# O volume persistente e montado depois da criacao da imagem e pode conservar
# proprietario root de uma instalacao anterior. Corrija-o antes de reduzir os
# privilegios; a aplicacao nunca deve executar como root.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/media
  chown -R appuser:appuser /app/media
  exec gosu appuser "$0" "$@"
fi

if [ "$1" != "celery" ]; then
  # Apply database migrations
  python manage.py migrate --noinput

  # Collect static files
  python manage.py collectstatic --noinput
fi

# Execute the container command (e.g. gunicorn)
exec "$@"
