# Lista de verificação do servidor antes de ligar o aviso

O aviso diário de alunos irregulares nasce desligado. Confira os itens abaixo no servidor antes de ligá-lo.

## Antes de ligar

- [ ] **E-mail configurado no `.env`.** `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER` e `EMAIL_HOST_PASSWORD` preenchidos. O remetente do aviso sai de `EMAIL_HOST_USER`: o `settings.py` monta o `DEFAULT_FROM_EMAIL` a partir dele (`AcadFlow <EMAIL_HOST_USER>`), e o aviso usa esse valor. Vazio, o servidor de e-mail pode recusar o envio.
- [ ] **`SITE_URL` com o endereço real do Acadflow.** É ele que forma os links dos alunos dentro do e-mail. Sem isso, os links apontam para `http://localhost:8000`.
- [ ] **Contêineres `ppgec_celery` e `ppgec_celery_beat` rodando.** Conferir com `docker ps --filter name=ppgec_celery`. Sem o beat a tarefa nunca é disparada, e sem o worker ela é disparada, mas ninguém executa.
- [ ] **Pelo menos uma pessoa ativa, com e-mail cadastrado, na Secretaria PPGEC ou na Coordenação PPG.** Sem ninguém, os avisos ficam pendentes e entram no e-mail do dia seguinte.
- [ ] **Ver o e-mail do dia sem enviar nada.** Rodar `docker compose -f docker-compose-prod.yml exec web python manage.py mostrar_aviso_do_dia`. O comando funciona com o aviso desligado e não envia nem grava nada. Para gerar a prévia em arquivo, acrescentar `--html previa.html`.

## Ligar

No `.env` do servidor, com o módulo já ligado (`ACOMPANHAMENTO_ATIVO=True`):

```
ACOMPANHAMENTO_AVISO_ATIVO=True
```

Depois, `docker compose -f docker-compose-prod.yml up -d`. Reiniciar os contêineres não relê o `.env`.

## Depois de ligar

- [ ] **Conferir a execução.** Depois das 7h (horário de Recife), abrir `/acompanhamento/` e ver se "Último aviso rodou em" mostra a data do dia. Se não mostrar, olhar os logs de `ppgec_celery` e `ppgec_celery_beat`.
- [ ] **Esperar o primeiro e-mail com todos os alunos que já estão irregulares** (levantamento inicial) e conferir que um link dele abre o aluno certo.

