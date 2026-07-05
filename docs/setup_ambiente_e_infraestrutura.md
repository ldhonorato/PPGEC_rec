# Documentação Técnica de Transição ⬇️
## Setup, Ambiente e Infraestrutura ⚙️

Neste documento se encontram as especificações técnicas da arquitetura e o guia de implantação do ecossistema do **AcadFlow**. Estão inclusas, também, as diretrizes necessárias para configurar, executar e criar usuários de teste.

---

## 1. Visão Geral da Infraestrutura e Contêineres 📦

A aplicação é totalmente orquestrada através do **Docker Compose**, onde cada serviço essencial roda em um ambiente isolado dentro de uma rede virtual privada própria:

* **Nginx (Proxy Reverso):** Intercepta requisições HTTP externas e as encaminha internamente para o contêiner de aplicação, servindo como gateway de segurança.
* **Django (Aplicação Core):** Concentra lógica de negócios, controle de rotas, autenticação, ORM e renderização de páginas.
* **PostgreSQL (Banco de Dados):** Instância relacional responsável pela persistência estruturada dos dados do sistema.
* **Redis & Celery (Processamento Assíncrono):** Tarefas que podem demorar, como disparos de e-mails via SMTP, são enviadas para o **Redis** e processadas em background pelo **Celery Worker**, evitando travamentos na navegação.

---

## 2. Stack Tecnológica e Ferramentas 📝

| Tecnologia / Ferramenta | Escopo / Contexto | Finalidade Específica na Solução |
| :--- | :--- | :--- |
| **Python / Django** | Backend / API Core | Arquitetura de rotas, ORM relacional, controle de acessos e regras de negócio. |
| **PostgreSQL** | Banco de Dados | Persistência estruturada de dados discentes, docentes e trâmite de pareceres. |
| **Nginx** | Servidor Web | Proxy reverso, encapsulamento de portas e gateway de segurança HTTP. |
| **Redis** | Message Broker | Armazenamento em memória para enfileiramento de rotinas assíncronas do Celery. |
| **Celery Worker** | Gerenciador de Filas | Execução de background tasks (processamento pesado e envios automáticos de e-mail). |
| **Docker / Compose** | Infraestrutura | Conteinerização, padronização do ambiente local e orquestração de múltiplos serviços. |

---

## 3. Variáveis de Ambiente (.env) 🔑

É obrigatória a existência de um arquivo nomeado como `.env` na raiz do projeto (no mesmo nível do `docker-compose.yml`, ou `docker-compose-prod.yml`) para viabilizar o funcionamento dos serviços:

```env
#---CONFIGURAÇÕES DO NÚCLEO DA APLICAÇÃO (DJANGO)---
DEBUG=[True/False]
PGADMIN_DEFAULT_EMAIL=[email_administrador_pgadmin]
SECRET_KEY=[chave_secreta_local_desenvolvimento]

#---CONFIGURAÇÕES DE FILAS E PROCESSAMENTO ASSÍNCRONO (CELERY)---
CELERY_BROKER_URL=[url_do_redis_broker]
CELERY_RESULT_BACKEND=[url_do_redis_backend_de_resultados]

#---CONFIGURAÇÕES DE ENVIO DE E-MAIL (SMTP)---
DEFAULT_FROM_EMAIL=[email_remetente_padrao]
EMAIL_HOST=[host_do_servidor_smtp]
EMAIL_HOST_PASSWORD=[senha_do_aplicativo_smtp]
EMAIL_HOST_USER=[usuario_do_servidor_smtp]
EMAIL_PORT=[porta_do_servidor_smtp]
EMAIL_USE_TLS=[True/False]

#---CONFIGURAÇÕES DO BANCO DE DADOS (POSTGRESQL)---
POSTGRES_DB=[nome_do_banco_de_dados]
POSTGRES_HOST=[host_do_banco_de_dados_no_docker]
POSTGRES_PASSWORD=[senha_do_usuario_postgres]
POSTGRES_PORT=[porta_do_banco_de_dados]
POSTGRES_USER=[usuario_do_banco_de_dados]