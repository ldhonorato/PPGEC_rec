# Feat: Envio de E-mails Assíncronos na Abertura e Movimentação de Processos

## 🎯 Objetivo
Implementar o envio de e-mails notificantes (para alunos e orientadores) após a **abertura** e **movimentação** de um processo. 
A assincronia permite que o usuário ainda utilize o sistema mesmo que o email não tenha sido enviado, utilizamos **Redis** (contido no **Docker**) e **Celery** para tal.

## 🛠️ O que foi feito?
1. **Infraestrutura (Broker):** Configuração do **Redis** rodando via container Docker.
2. **Mensageria (Worker):** Instalação e configuração do **Celery** no ambiente virtual (Django) para processamento em background.
3. **Segurança:** Movimentação das credenciais de e-mail (SMTP) para um arquivo `.env` (listado no .gitignore).
4. **Tasks (`tasks.py`):** Criação de tarefas assíncronas (`@shared_task`) com suporte a tentativas de reenvio (`max_retries`) em caso de falha de conexão.
5. **Views:** Atualização da view de criação e movimentação de processos para disparar as tarefas utilizando o método `.delay()`.
6. **Templates:** Criação de templates HTML básicos para o corpo dos e-mails.

## ⚙️ Como rodar em outras máquinas

Como adicionamos ferramentas novas, você precisará atualizar o seu ambiente local:

### 1. Subir o Redis (Docker)
Certifique-se de ter o Docker instalado e rodar:
`docker run -d --name redis-acadflow -p 6379:6379 redis`

### 2. Atualizar as dependências do Python
Com seu `venv` ativado, instale as novas bibliotecas:
`pip install celery redis python-dotenv`

### 3. Configurar o arquivo `.env`
Crie o arquivo `.env` na raiz do projeto e adicione as variáveis de e-mail (pedir credenciais à equipe):
EMAIL_HOST_USER=email@exemplo.com 
EMAIL_HOST_PASSWORD=senha_exemplo 
DEFAULT_FROM_EMAIL=exemplo default@exemplo.com

### 4. Iniciar os serviços
Você precisará de **dois terminais** rodando simultaneamente:

**Terminal 1 (Rodar Servidor Django):**
`python manage.py runserver`

**Terminal 2 (Rodar o Worker do Celery):**
`celery -A ppgec worker -l info`