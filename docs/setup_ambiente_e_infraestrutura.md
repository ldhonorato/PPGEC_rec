# Documentação Técnica de Transição ⬇️
## Setup, Ambiente e Infraestrutura ⚙️

Neste documento se encontram as especificações técnicas da arquitetura e o guia de implantação do ecossistema do **AcadFlow**. Estão inclusas as diretrizes necessárias para configurar, executar, criar usuários de teste e entender as principais regras de negócio do sistema.

---

## 1. Visão Geral da Infraestrutura e Contêineres 📦

A aplicação é totalmente orquestrada através do **Docker Compose**, onde cada serviço roda em um ambiente isolado dentro de uma rede virtual privada:

* **Gunicorn (Servidor WSGI):** Recebe as requisições HTTP e as processa através da aplicação Django. Arquivos estáticos são servidos diretamente pelo **WhiteNoise**, sem necessidade de Nginx.
* **Django (Aplicação Core):** Concentra a lógica de negócios, controle de rotas, autenticação, ORM e renderização de páginas.
* **PostgreSQL (Banco de Dados):** Instância relacional responsável pela persistência de dados do sistema. Em desenvolvimento local sem Docker, o sistema cai automaticamente para SQLite.
* **Redis & Celery (Processamento Assíncrono):** Tarefas como disparos de e-mails via SMTP são enviadas para o **Redis** e processadas em background pelo **Celery Worker** e **Celery Beat**, evitando travamentos na navegação.
* **pgAdmin:** Interface web para administração visual do banco PostgreSQL, acessível na porta 5050.

Existem dois arquivos de orquestração:

| Arquivo | Contexto | Diferença principal |
| :--- | :--- | :--- |
| `docker-compose.yml` | **Desenvolvimento local** | Faz build da imagem a partir do código local (`build: .`). A porta do app é **8000**. |
| `docker-compose-prod.yml` | **Produção** | Puxa a imagem publicada no GitHub Container Registry (`ghcr.io/ldhonorato/ppgec_rec:latest`). A porta do app é **8001**. Inclui o **Watchtower** para atualizações automáticas de imagem. |

---

## 2. Stack Tecnológica e Ferramentas 📝

| Tecnologia / Ferramenta | Versão | Escopo | Finalidade na Solução |
| :--- | :--- | :--- | :--- |
| **Python** | 3.11 | Backend | Linguagem principal |
| **Django** | 5.2 | Backend / API Core | Rotas, ORM, autenticação, admin, regras de negócio |
| **Gunicorn** | latest | Servidor WSGI | Processa requisições HTTP em produção |
| **WhiteNoise** | 6.4.0 | Servidor de estáticos | Serve CSS/JS/imagens sem Nginx |
| **PostgreSQL** | 16-alpine | Banco de Dados | Persistência estruturada em Docker/produção |
| **SQLite** | embutido | Banco de Dados | Alternativa local sem Docker (automático) |
| **Redis** | 7-alpine | Message Broker | Fila de tarefas assíncronas para o Celery |
| **Celery Worker** | 5.4.0 | Gerenciador de Filas | Execução de background tasks (e-mails) |
| **Celery Beat** | 5.4.0 | Agendador | Execução de tarefas periódicas |
| **django-celery-results** | 2.5.1 | Resultados | Persiste resultados das tasks no banco |
| **psycopg2-binary** | 2.9.10 | Driver | Conexão Python ↔ PostgreSQL |
| **pgAdmin 4** | 8 (imagem) | Admin DB | Interface web de administração do PostgreSQL |
| **Watchtower** | 1.7.1 | DevOps (prod) | Auto-atualização de imagens Docker em produção |
| **Docker / Compose** | v2 | Infraestrutura | Conteinerização e orquestração de serviços |
| **python-dotenv** | 1.0.0 | Configuração | Carregamento de variáveis de ambiente do `.env` |

---

## 3. Variáveis de Ambiente (.env) 🔑

É obrigatória a existência de um arquivo `.env` na raiz do projeto (mesmo nível do `docker-compose.yml`) para viabilizar o funcionamento dos serviços.

> ⚠️ **Segurança:** Nunca versione o `.env` no repositório. Nunca compartilhe `EMAIL_HOST_PASSWORD` em PRs, issues ou canais públicos — é uma credencial real de produção.

```env
#---CONFIGURAÇÕES DO NÚCLEO DA APLICAÇÃO (DJANGO)---
DEBUG=True
SECRET_KEY=[chave_secreta_local_desenvolvimento]
SITE_URL=http://127.0.0.1:8000

#---CONFIGURAÇÕES DO BANCO DE DADOS (POSTGRESQL)---
POSTGRES_DB=[nome_do_banco_de_dados]
POSTGRES_HOST=db
POSTGRES_PASSWORD=[senha_do_usuario_postgres]
POSTGRES_PORT=5432
POSTGRES_USER=[usuario_do_banco_de_dados]

#---CONFIGURAÇÕES DE ENVIO DE E-MAIL (SMTP)---
DEFAULT_FROM_EMAIL=[email_remetente_padrao]
EMAIL_HOST=smtp.gmail.com
EMAIL_HOST_PASSWORD=[senha_de_app_gmail_16_chars]
EMAIL_HOST_USER=[usuario_do_servidor_smtp]
EMAIL_PORT=587
EMAIL_USE_TLS=True

#---CONFIGURAÇÕES DO PGADMIN---
PGADMIN_DEFAULT_EMAIL=[email_valido_para_login_no_pgadmin]
PGADMIN_DEFAULT_PASSWORD=[senha_do_pgadmin]

#---CONFIGURAÇÕES DE FILAS E PROCESSAMENTO ASSÍNCRONO (CELERY)---
# Normalmente não precisam ser definidas no .env para uso local com Docker,
# pois já estão configuradas no docker-compose.yml
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
```

> **Sobre `EMAIL_HOST_PASSWORD`:** O sistema usa Gmail com **Senha de App** — não é a senha da conta Google. Gere em: Conta Google → Segurança → Verificação em duas etapas → Senhas de app. O resultado é uma sequência de 16 caracteres.

> **Sobre `PGADMIN_DEFAULT_EMAIL`:** O pgAdmin 4.8+ valida o formato do e-mail. Use um endereço com domínio válido (ex.: `admin@ppgec.com`), não `.local`.

---

## 4. Passo a Passo: Configuração e Execução 🚀

### 4.1 Pré-requisitos

Instale apenas:
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS) ou Docker Engine + Docker Compose v2 (Linux)
- Git

Não é necessário instalar Python, PostgreSQL ou Redis localmente.

### 4.2 Clonar o repositório

```bash
git clone https://github.com/ldhonorato/PPGEC_rec
cd PPGEC_rec
```

### 4.3 Criar o arquivo `.env`

Copie o template da seção 3 e preencha com os valores reais. Salve como `.env` na raiz do projeto.

### 4.4 Subir os contêineres

```bash
docker compose up --build
```

Aguarde até aparecer nos logs: `Booting worker with pid` (serviço `web`).

### 4.5 Aplicar as migrações *(apenas na primeira vez)*

```bash
docker compose exec web python manage.py migrate
```

### 4.6 Criar superusuário *(apenas na primeira vez)*

```bash
docker compose exec web python manage.py createsuperuser
```

Siga as instruções no terminal para definir e-mail e senha.

### 4.7 Acessar o sistema

| Interface | URL |
| :--- | :--- |
| Aplicação principal | http://localhost:8000 |
| Painel Admin Django | http://localhost:8000/admin/ |
| pgAdmin (banco) | http://localhost:5050 |

### 4.8 Comandos úteis

```bash
# Parar todos os serviços
docker compose down

# Parar e apagar volumes (apaga o banco de dados)
docker compose down -v

# Ver logs em tempo real
docker compose logs -f

# Ver logs de um serviço específico
docker compose logs -f web

# Criar novas migrações após alterar models.py
docker compose exec web python manage.py makemigrations

# Acessar shell Django
docker compose exec web python manage.py shell
```

---

## 5. Criação de Usuários de Teste 👥

Acesse o Admin Django em http://localhost:8000/admin/ com o superusuário criado no passo 4.6.

### Perfis disponíveis

| Perfil | Onde cadastrar no Admin | Campos obrigatórios |
| :--- | :--- | :--- |
| **Aluno** | Processos → Alunos | nome, e-mail, matrícula, status_aluno |
| **Docente** | Processos → Docentes | nome, e-mail; marcar `coordenador=True` para Coordenador |
| **Servidor (Secretaria)** | Processos → Users | nome, e-mail, tipo_usuario = SERVIDOR |

> **Importante:** Para que os fluxos de processo funcionem corretamente, o Aluno deve ter uma **TrajetoriaAcademica ativa** com orientador vinculado. Cadastre em: Processos → Trajetoria academicas.

### Fluxo mínimo para testar

1. Criar um Docente
2. Criar um Aluno
3. Criar uma TrajetoriaAcademica vinculando o Aluno ao Docente (status = ATIVA)
4. Criar um Setor chamado "Secretaria" (Processos → Setores)
5. Criar um Setor chamado "Pleno" (necessário para testar encaminhamento ao Pleno)
6. Logar como Aluno e abrir um processo

---

## 6. Regras de Negócio 📋

Esta seção descreve as principais regras que governam o comportamento do sistema. São essenciais para qualquer desenvolvedor que for evoluir o projeto.

### 6.1 Perfis e Permissões

| Ação | Aluno | Docente | Coordenador¹ | Servidor |
| :--- | :---: | :---: | :---: | :---: |
| Abrir processo | ✅ | ❌ | ❌ | ❌ |
| Ver processos próprios | ✅ | — | — | — |
| Ver processos de orientandos | ❌ | ✅ | ✅ | ✅ |
| Gerenciar caixa de entrada | ❌ | ❌ | ✅² | ✅ |
| Encaminhar processo | ❌ | ❌ | ✅² | ✅ |
| Solicitar ciência do orientador | ❌ | ❌ | ✅² | ✅ |
| Deferir / Indeferir | ❌ | ❌ | ✅² | ✅ |
| Comentar no processo (Pleno) | ❌ | ✅³ | ✅ | ✅ |
| Dar ciência (orientador) | ❌ | ✅⁴ | ✅⁴ | ❌ |

> ¹ Coordenador = Docente com `docente.coordenador = True`  
> ² Apenas quando o processo está na caixa da Coordenação ou do Pleno  
> ³ Docentes sem cargo de coordenador só podem **comentar** em processos `EM_DEBATE` — não podem encaminhar nem deferir  
> ⁴ Apenas o orientador responsável pelo processo pode confirmar ou recusar a ciência

### 6.2 Ciclo de Vida de um Processo

```
[Aluno abre]
      ↓
  EM_ANÁLISE
      ↓ (Secretaria/Coordenação encaminha)
  EM_ANÁLISE  ←→  AGUARDANDO_DOCUMENTO
      ↓ (Secretaria solicita ciência do orientador)
  AGUARDANDO_CIÊNCIA
      ↓ (Orientador confirma ou recusa)
  EM_ANÁLISE
      ↓ (encaminhado ao Pleno)
  EM_DEBATE
      ↓ (Secretaria/Coordenação defere ou indefere)
  FINALIZADO
```

Regras importantes:
- **Não é possível encaminhar um processo finalizado.**
- **Não é possível encaminhar com ciência do orientador pendente** — o encaminhamento é bloqueado até a manifestação.
- **Encaminhar ao Pleno exige data limite** (`prazo_pleno`) — o campo é obrigatório e não pode ser uma data passada.
- Ao deferir ou indeferir, um **termo de finalização** é obrigatório.

### 6.3 Numeração de Processos

O número é gerado automaticamente no formato `YYYYMM-NNNNNN` (ex.: `202507-000001`). A sequência reinicia a cada mês. A geração é protegida por `select_for_update` para evitar duplicatas em concorrência.

### 6.4 Prazos Automáticos

Ao criar um processo, o `prazo_limite` é calculado automaticamente:

| Tipo de Processo | Prazo (dias) |
| :--- | :---: |
| Trancamento de Matrícula | 15 |
| Prorrogação de Prazo / Mudança de Orientador | 20 |
| Aproveitamento de Créditos / Reingresso | 30 |
| Defesa de Mestrado / Doutorado / Qualificação | 45 |
| Outro | 60 |

Processos com `prazo_limite < data_atual` e não finalizados são marcados como **atrasados** e exibem um indicador visual.

### 6.5 Solicitação de Ciência do Orientador

- Só pode ser solicitada por **Servidor** ou **Coordenador**.
- Só pode existir **uma solicitação pendente por vez** por processo.
- O processo muda para status `AGUARDANDO_CIÊNCIA` automaticamente ao solicitar.
- Ao orientador confirmar ou recusar, o processo volta para `EM_ANÁLISE`.
- O orientador responsável é determinado pela **TrajetoriaAcademica ativa** do aluno criador do processo.

### 6.6 Documentos e Restrição de Acesso

Documentos podem ser marcados com 7 categorias de restrição baseadas na Lei de Acesso à Informação (Lei 12.527/2011):

| Quem pode visualizar um documento restrito |
| :--- |
| Quem enviou o documento |
| Servidor (Secretaria) — acesso irrestrito |
| Coordenador (`docente.coordenador = True`) |

Alunos e Docentes comuns **não veem** documentos restritos de outros. A remoção de um arquivo exige motivo obrigatório e é rastreada (quem removeu, quando, por quê).

### 6.7 Trajetória Acadêmica e Orientador

O vínculo entre Aluno e Orientador **não fica no modelo Aluno** — fica em `TrajetoriaAcademica`. Isso tem impacto direto em queries:

```python
# ✅ Correto
Aluno.objects.filter(
    trajetorias__orientador=request.user,
    trajetorias__status=TrajetoriaAcademica.Status.ATIVA,
).distinct()

# ❌ Errado — campo não existe mais
Aluno.objects.filter(orientador=request.user)
```

Um aluno pode ter múltiplas trajetórias (ex.: após reingresso), mas apenas **uma ativa por vez** governa o vínculo atual com orientador.

### 6.8 Notificações por E-mail

Os e-mails são disparados de forma **assíncrona via Celery** (não bloqueiam a requisição). O nome do aluno é incluído em todos os assuntos para facilitar o filtro na caixa de entrada do destinatário.

| Evento | Destinatário |
| :--- | :--- |
| Processo aberto | Aluno |
| Solicitação de ciência | Orientador |
| Devolução para ajustes | Aluno |
| Movimentação de processo | Aluno |
| Processo finalizado | Aluno |
| Novo processo no Pleno | Setor Pleno (e-mail do setor) |
| Intervenção no Pleno (aprovação automática cancelada) | Responsáveis |

O e-mail institucional dos setores é configurado no campo `email` do modelo `Setor` (Admin → Processos → Setores).

### 6.9 Reserva de Ambientes

- Reservas só podem ser feitas em **horários dentro da disponibilidade** cadastrada para a sala.
- O sistema detecta **conflito de horário** automaticamente — duas reservas ativas não podem se sobrepor na mesma sala.
- Reservas recorrentes (diária, semanal, mensal) são limitadas a **6 meses**.
- A exclusão de uma reserva exige **justificativa obrigatória**.
