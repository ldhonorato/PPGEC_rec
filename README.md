# PPGEC - Sistema de Gestão de Processos

Aplicação Django para gestão de processos acadêmicos do PPGEC, com perfis de acesso para alunos, docentes, coordenação e servidores.

## Documentação do Projeto

Os documentos abaixo apresentam informações necessárias para compreender, executar, manter e dar continuidade ao desenvolvimento do sistema.

| Documento | Descrição |
|---|---|
| [Documento de Negócio](docs/documento-de-negocio.md) | Apresenta o propósito do sistema, o público atendido, as funcionalidades implementadas, as regras de negócio e as evoluções realizadas durante o semestre. |
| [Documento Técnico](docs/entrega-modulo-acompanhamento.md) | Descreve a arquitetura, as tecnologias utilizadas, a configuração do ambiente, a execução do sistema, os testes e os procedimentos técnicos. |
| [Documento de Passagem de Conhecimento](docs/passagem-de-conhecimento.md) | Registra o estado atual do projeto, as decisões importantes, os aprendizados da equipe e as recomendações para sua continuidade. |

**Documentação complementar:**

- [Manual de Uso da Secretaria](docs/manual-de-uso-secretaria.md): orientações para utilização das funcionalidades do módulo de acompanhamento.
- [Guia do Ambiente Local](docs/guia-do-ambiente-local.md): instruções detalhadas para configuração e execução do projeto em ambiente de desenvolvimento.

## Requisitos

- Python 3.12+
- Conda (ambiente `ppgec`)

## Instalação

```bash
conda create -n ppgec python=3.12 -y
conda activate ppgec
pip install -r requirements.txt
```

## Configuração do ambiente

O projeto lê variáveis do arquivo `.env`. Um arquivo local de desenvolvimento já pode ser usado diretamente; para recriar a partir do exemplo:

```bash
cp .env.example .env
```

Principais variáveis para Postgres:

```env
USE_POSTGRES=True
POSTGRES_DB=ppgec
POSTGRES_USER=ppgec
POSTGRES_PASSWORD=ppgec_dev_password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

## Executar localmente

```bash
docker compose up -d db redis pgadmin
conda run -n ppgec python manage.py migrate
conda run -n ppgec python manage.py runserver
```

O pgAdmin fica disponível em `http://localhost:5050`. Para conectar ao banco pelo pgAdmin, use:

- Host: `db`
- Porta: `5432`
- Database: valor de `POSTGRES_DB`
- Usuário: valor de `POSTGRES_USER`
- Senha: valor de `POSTGRES_PASSWORD`

Para voltar temporariamente ao SQLite local, defina `USE_POSTGRES=False` no `.env`.

## Estrutura principal

- `ppgec/`: configurações do projeto Django (settings, urls, wsgi/asgi)
- `processos/`: app principal (models, views, forms, migrations, admin)
- `templates/`: templates HTML
- `static/`: arquivos estáticos

## Comandos úteis

```bash
conda run -n ppgec python manage.py makemigrations
conda run -n ppgec python manage.py migrate
conda run -n ppgec python manage.py createsuperuser
conda run -n ppgec python manage.py check
```
