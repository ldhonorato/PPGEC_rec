# Passagem de conhecimento: módulo de acompanhamento acadêmico

Equipe E4, disciplina de Engenharia de Software da UPE, segundo semestre de 2026. Situação em 21/11/2026.

Este documento é para quem vai continuar o módulo. Ele traz o que ficou pendente e o que a equipe aprendeu e não está escrito em outro lugar. O que o módulo faz está no [documento de negócio](documento-de-negocio.md), e como instalar, testar e operar, no [documento técnico](entrega-modulo-acompanhamento.md).

## 1. Onde as coisas estão

| O quê | Onde |
|---|---|
| Repositório oficial | `ldhonorato/PPGEC_rec`, do coordenador do PPGEC. Branches `stage` e `main` |
| Fork da equipe | `gustavosouto/PPGEC_rec`. Branches `develop` e `homolog` |
| Produção | `acadflow.poli.br`, no servidor da POLI, versão 1.4.1 desde 16/11/2026 |
| Código do módulo | App `acompanhamento` |

Em 21/11/2026, a `develop` do fork estava igual à `main` oficial.

## 2. Estado em 21/11/2026

- Todas as funcionalidades pedidas para o semestre estão em produção, inclusive o Painel por Professor.
- O módulo está ligado em produção: `/acompanhamento/` pede login em vez de responder como página inexistente.
- Não foi possível conferir, de fora do servidor, se o aviso diário está ligado e se o worker do Celery está estável na 1.4.1.

## 3. Pendências

Em ordem de importância.

| Pendência | Por que importa | Com quem |
|---|---|---|
| Conferir o worker e o aviso no servidor | A imagem de 03/11/2026 tinha uma versão do `kombu` que faz o worker reiniciar sem parar, corrigida na 1.4.1. Isso nunca foi conferido no servidor. Sem o worker, o aviso não roda. Os comandos estão no documento técnico, em "Como atualizar a produção" | Coordenador, que tem acesso ao servidor |
| Validar os números com dados reais | A lista, o cartão e os painéis foram testados com dados fictícios. A secretaria e a coordenação ainda não compararam os resultados com o controle que já mantinham | Secretaria e coordenação |
| Conferir se o "Aluno Mock 1" existe em produção | A migration `0003_seed_mock_data`, do sistema original, cria esse aluno. Se ele existir, entra no cartão, na lista e no aviso | Coordenador decide a remoção |
| Definir quem vê a lista e a planilha | Hoje servidores, bolsistas e voluntários também veem, e a planilha tem dados pessoais | Coordenação |
| Medir o desempenho do Painel por Professor | Não foi medido com o tamanho real do programa, cerca de 400 alunos e 40 professores. O Painel de Processos também não foi medido com muitos dados | Próxima equipe |
| Cadastrar o calendário de cada semestre | Sem as datas, parte dos alunos aparece como "Não foi possível calcular" | Secretaria |

## 4. Limitações conhecidas

- A página do aluno, do sistema original, conta a prorrogação mantendo o dia do mês. Com ingresso em fevereiro de 2025 e 3 meses de prorrogação, ela mostra 28/05/2027, e o módulo mostra 31/05/2027. O módulo segue a regra confirmada pela coordenação, e a página do aluno não foi alterada.
- No Painel por Professor, o histórico do aluno fica com o orientador atual. Se o orientador mudou, os números antigos passam para o novo.
- A lista considera a trajetória ativa mais recente do aluno, e o painel conta todas as ativas. Os números só divergem se um aluno tiver duas trajetórias ativas.
- O início dos registros, 2026.2, está fixo em `INICIO_DOS_REGISTROS`, em `acompanhamento/servicos.py`.
- O módulo usa a função privada `_has_gestao_access`, do app `processos`. Um teste avisa se as duas cópias da regra divergirem.
- Desde a 1.4.1, o Dashboard só tem atalho pela página de Acompanhamento. Se o módulo for desligado, o Dashboard continua em `/coordenacao/dashboard/`, mas some do menu.

## 5. Pedidos que não entraram

| Pedido | Situação |
|---|---|
| Curvas semestre a semestre | Pedido da coordenação, sem prioridade definida |
| Melhorias no Dashboard | Pedido da coordenação. O coordenador já levou o Dashboard para o Acompanhamento em 16/11/2026 |
| Cada professor ver os próprios números | Hoje só o coordenador e a Coordenação PPG veem o Painel por Professor |
| Aviso antes do vencimento do prazo | Proposto pela equipe, não pedido |
| Coorientação no Painel por Professor | Fora, por decisão da coordenação |

## 6. O que não está escrito em outro lugar

### Sobre o repositório

- O coordenador também altera o código diretamente na `main` oficial. Em 16/11/2026, ele publicou a 1.4.1 com mudanças em processos, em textos do módulo e no Dashboard. Antes de começar qualquer trabalho, sincronizar o fork com a `main` oficial.
- Os merges na `stage` e na `main` são do coordenador. A equipe abre o pull request da `homolog` do fork para a `stage` oficial e manda junto as instruções de atualização.
- O fork é público. Nenhum dado real de aluno pode ir para o código, os testes, as issues ou os pull requests.

### Sobre o servidor

- Só se entra no servidor pela VPN da POLI, liberada pela DTI. A equipe não teve acesso durante o semestre, e tudo no servidor foi feito pelo coordenador, a partir de comandos enviados pela equipe. Comandos para o servidor devem ser só de leitura, ou vir com o passo para desfazer.
- Dentro da rede da POLI, `acadflow.poli.br` tem problema de DNS e pode não abrir. De fora, pelo 4G por exemplo, abre. Antes de tratar um relato de "sistema fora do ar", perguntar de qual rede a pessoa estava.
- A imagem é gerada a cada merge na `main` oficial. Em 03/11/2026, a imagem pegou uma versão nova do `kombu` sem nenhuma mudança no código, e o worker passou a reiniciar sem parar. Por isso as versões ficam fixas no `requirements.txt`.
- O comando `mostrar_aviso_do_dia` mostra nomes de alunos reais. A saída não deve ir para grupo de mensagens nem para o GitHub.

### Sobre o desenvolvimento

- O GitHub Actions do fork está parado desde 06/11/2026, por falta de pagamento da conta. Os passos do `ci.yml` precisam rodar na máquina de quem revisa antes de cada merge. Os comandos estão no documento técnico, seção 5.
- No Windows, a suíte mostra um erro em `tearDownClass` de `processos.tests.ArquivoEnviadoTests`. É um problema de limpeza de arquivos do Windows e não aparece no Linux.
- O ensaio de produção pode rodar na máquina local com Docker. No Windows, é preciso `COMPOSE_PATH_SEPARATOR=":"` e um checkout com fim de linha LF, senão os scripts falham dentro do contêiner.
- Os dados fictícios saem de `python manage.py gerar_dados_ficticios --semente 42`. Com `--data-referencia`, os prazos ficam fixos numa data, o que deixa os testes manuais repetíveis. O comando recusa rodar com `DEBUG=False`.
- Os testes das telas do módulo precisam de `@override_settings(ACOMPANHAMENTO_ATIVO=True)`.

### Sobre as pessoas

- O coordenador do PPGEC, Prof. Leandro Honorato, decide as regras, faz os merges no repositório oficial e tem acesso ao servidor.
- O Denis, bolsista da secretaria, conhece a rotina dos prazos e quais indicadores são úteis. As regras de prazo foram respondidas primeiro por ele e depois confirmadas pelo coordenador.
- As regras de negócio confirmadas estão no documento de negócio. Mudar uma delas exige nova confirmação da coordenação.

## 7. Por onde começar

1. Ler o documento de negócio e o manual da secretaria.
2. Sincronizar o fork com a `main` oficial.
3. Seguir o [guia do ambiente local](guia-do-ambiente-local.md), gerar os dados fictícios e abrir `/acompanhamento/` com o módulo ligado.
4. Rodar os testes da seção 5 do documento técnico.
5. Pedir ao coordenador o resultado dos comandos de conferência do servidor e começar pelas pendências da seção 3.
