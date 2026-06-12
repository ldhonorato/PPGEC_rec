
# o container é baseado na imagem oficial do docker que usa uma versao leve do python e eh baseado em debian
FROM python:3.11-slim

ARG APP_VERSION=local
ARG APP_REVISION=unknown
ARG APP_BUILD_RUN_ID=local

# variaveis p que o python n gere cache e escreva os logs no console imediatamente
ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	APP_VERSION=${APP_VERSION} \
	APP_REVISION=${APP_REVISION} \
	APP_BUILD_RUN_ID=${APP_BUILD_RUN_ID}

# atualiza o sistema e instala as dependencias/compiladores necessarios, alem de limpar o cache do apt
RUN apt-get update \
	&& apt-get install -y --no-install-recommends build-essential gcc \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./

RUN pip install --upgrade pip \
	&& pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

RUN adduser --disabled-password --gecos "" appuser || true
RUN mkdir -p /home/appuser \
	&& chown -R appuser:appuser /app /home/appuser

ENV HOME=/home/appuser

COPY entrypoint.sh /entrypoint.sh
RUN dos2unix /entrypoint.sh || sed -i 's/\r$//' /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER appuser

EXPOSE 8001

ENTRYPOINT ["/entrypoint.sh"]

CMD ["gunicorn", "ppgec.wsgi:application", "--bind", "0.0.0.0:8001", "--workers", "3"]
