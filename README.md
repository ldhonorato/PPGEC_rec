# PPGEC - Sistema de Gestão de Processos

Aplicação Django para gestão de processos acadêmicos do PPGEC, com perfis de acesso para alunos, docentes, coordenação e servidores.

## Requisitos

- Python 3.12+
- Conda (ambiente `ppgec`)

## Instalação

```bash
conda create -n ppgec python=3.12 -y
conda activate ppgec
pip install -r requirements.txt
```

## Executar localmente

```bash
conda run -n ppgec python manage.py migrate
conda run -n ppgec python manage.py runserver
```

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
