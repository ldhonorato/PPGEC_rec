# Passagem de Conhecimento: Módulo de Acompanhamento Acadêmico

**Projeto:** AcadFlow – Sistema de Gestão de Processos do Programa de Pós-Graduação em Engenharia da Computação (PPGEC) da UPE  
**Equipe:** E4 – Disciplina de Engenharia de Software  
**Período:** Segundo semestre de 2026  
**Versão do módulo:** 1.4.1  
**Data de referência:** 21/11/2026

Este documento é para a equipe que vai continuar o desenvolvimento e a manutenção do módulo de Acompanhamento Acadêmico.

A ideia é explicar, de maneira direta, onde o projeto está, o que aprendemos durante o desenvolvimento e quais cuidados devem ser tomados para dar continuidade ao trabalho.

O que o módulo faz está no [Documento de Negócio](documento-de-negocio.md). Como instalar, executar, testar e implantar está no [Documento Técnico](entrega-modulo-acompanhamento.md). As instruções para utilização estão no [Manual de Uso da Secretaria](manual-de-uso-secretaria.md).

## 1. Onde as coisas estão

| O quê | Onde |
|---|---|
| Repositório oficial | [`ldhonorato/PPGEC_rec`](https://github.com/ldhonorato/PPGEC_rec), mantido pelo coordenador do PPGEC. Branches `stage` e `main`. |
| Fork da equipe | [`gustavosouto/PPGEC_rec`](https://github.com/gustavosouto/PPGEC_rec). Branches `develop` e `homolog`. |
| Produção | `acadflow.poli.br`, no servidor da POLI. |
| Versão em produção | 1.4.1, desde 16/11/2026. |
| Código do módulo | Aplicativo Django `acompanhamento/`. |
| Ambiente local | Instruções no [Guia do Ambiente Local](guia-do-ambiente-local.md). |
| Testes automatizados | Diretório `acompanhamento/tests/` e testes do aplicativo `processos`. |
| Automação | `.github/workflows/`, com workflows de CI e ensaio de produção. |

Na data de referência, a branch `develop` do fork estava sincronizada com a `main` oficial.

Antes de iniciar qualquer alteração, confira novamente se as branches estão atualizadas. O coordenador também realiza alterações diretamente no repositório oficial.

## 2. Estado atual do projeto

O módulo foi entregue e suas funcionalidades estão integradas ao AcadFlow.

- Todas as funcionalidades previstas para a entrega do semestre foram implementadas.
- O Painel por Professor está incluído na versão 1.4.1.
- O módulo está ativo em produção.
- As funcionalidades de lista de alunos, planilha, calendário, painéis e avisos estão implementadas.
- As chaves de ativação permitem controlar separadamente o módulo e o envio dos avisos.
- Os testes automatizados e o ensaio de produção fazem parte do processo de validação.
- As dependências possuem versões fixadas no `requirements.txt`.
- As dívidas técnicas identificadas durante o desenvolvimento foram resolvidas.

**Bugs conhecidos:** nenhum bug conhecido pela equipe E4 na data de referência.

**Pendências no GitHub:** não existem issues ou tarefas pendentes registradas pelo grupo atual.

**Dívidas técnicas:** não existem dívidas técnicas identificadas e não resolvidas pela equipe na entrega.

Uma observação importante: a equipe de desenvolvimento não teve acesso direto ao servidor de produção durante o semestre. Por isso, algumas verificações operacionais dependem do coordenador, que possui acesso à infraestrutura.

## 3. Recomendações para continuidade

Não existem pendências de desenvolvimento deixadas pela equipe. Entretanto, existem atividades de acompanhamento que podem ser realizadas pela próxima equipe ou pela coordenação, conforme a necessidade do programa.

| Recomendação | Motivo | Responsável sugerido |
|---|---|---|
| Conferir periodicamente o worker e o Celery Beat | Garantir que os avisos diários continuem sendo processados. | Coordenador ou responsável pela infraestrutura. |
| Validar os indicadores com a secretaria | Comparar os resultados apresentados com os controles administrativos utilizados pelo programa. | Secretaria e coordenação. |
| Conferir os dados fictícios eventualmente existentes em produção | Evitar que registros de demonstração sejam interpretados como registros acadêmicos reais. | Coordenador. |
| Revisar os perfis de acesso periodicamente | Garantir que somente os usuários autorizados visualizem listas e planilhas com informações acadêmicas. | Coordenação. |
| Acompanhar o desempenho dos painéis | Verificar se o tempo de resposta continua adequado com o crescimento da base de alunos e professores. | Próxima equipe e coordenação. |
| Atualizar o calendário a cada semestre | Os cálculos dependem das datas dos marcos acadêmicos. | Secretaria. |

Essas recomendações não representam bugs ou funcionalidades incompletas. São cuidados normais de operação e evolução de um sistema acadêmico.

## 4. Decisões e particularidades que precisam ser conhecidas

Algumas decisões foram tomadas durante o desenvolvimento e precisam ser preservadas para evitar mudanças que alterem os resultados esperados.

### 4.1. Cálculo dos prazos

- A página do aluno, pertencente ao sistema original, conta a prorrogação mantendo o dia do mês. O módulo de Acompanhamento utiliza a regra confirmada pela coordenação, que pode produzir uma data diferente.
- Por exemplo, para um ingresso em fevereiro de 2025 e três meses de prorrogação, a página original pode mostrar 28/05/2027, enquanto o módulo apresenta 31/05/2027.
- O módulo segue a regra definida e validada com a coordenação. Não se deve alterar esse comportamento sem nova confirmação.
- A lista considera a trajetória ativa mais recente do aluno, enquanto determinados painéis contabilizam todas as trajetórias ativas. Assim, os resultados podem divergir se um aluno possuir mais de uma trajetória ativa.

### 4.2. Painel por Professor

- O histórico do aluno é associado ao orientador atual.
- Quando há mudança de orientação, os registros históricos passam a ser contabilizados para o orientador atual.
- A coorientação não é contabilizada no painel, conforme decisão da coordenação.
- Atualmente, o acesso ao painel é destinado ao coordenador e à Coordenação PPG.

### 4.3. Período de registros

O início dos registros está definido pela constante `INICIO_DOS_REGISTROS`, localizada em:

```text
acompanhamento/servicos.py
```

Caso seja necessário alterar o período considerado pelos indicadores, verificar essa constante e os testes relacionados antes de modificar o comportamento.

### 4.4. Controle de acesso

O módulo aproveita mecanismos de autorização já existentes no aplicativo `processos`.

Uma particularidade é a utilização da função privada `_has_gestao_access`, pertencente ao aplicativo `processos`.

Existe um teste para verificar se as regras utilizadas pelo módulo continuam consistentes com as regras originais.

Evite duplicar ou modificar regras de autorização sem compreender primeiro como o AcadFlow controla os acessos.

### 4.5. Dashboard

Desde a versão 1.4.1, o acesso ao Dashboard foi reorganizado e seu atalho está na página de Acompanhamento.

A rota `/coordenacao/dashboard/` continua existindo mesmo quando o módulo é desativado, mas o atalho deixa de aparecer no menu.

## 5. Funcionalidades que podem ser consideradas em evoluções futuras

Durante o desenvolvimento e as conversas com a coordenação, surgiram algumas possibilidades de evolução.

Elas não representam pendências da entrega atual.

| Possibilidade | Contexto |
|---|---|
| Curvas de indicadores semestre a semestre | Permitir acompanhar a evolução histórica dos indicadores acadêmicos. |
| Melhorias adicionais no Dashboard | Ampliar a visualização dos indicadores administrativos. |
| Acesso individual dos professores aos próprios indicadores | Atualmente, o Painel por Professor é destinado ao coordenador e à Coordenação PPG. |
| Avisos antecipados de vencimento | Possibilidade de notificar sobre prazos antes que o aluno fique irregular. Foi uma proposta da equipe, não uma exigência da entrega. |
| Inclusão da coorientação nos indicadores | Não implementada por decisão da coordenação. |

Qualquer evolução deve ser discutida com a coordenação antes de iniciar o desenvolvimento.

## 6. O que a próxima equipe precisa saber

### 6.1. Sobre o repositório

- O coordenador do PPGEC, Prof. Leandro Honorato, também realiza alterações diretamente na `main` do repositório oficial.
- Em 16/11/2026, foi publicada a versão 1.4.1 com mudanças em funcionalidades de processos, textos do módulo e organização do Dashboard.
- Antes de iniciar qualquer trabalho, sincronize o fork com a `main` oficial.
- A equipe trabalha principalmente nas branches `develop` e `homolog` do fork.
- A integração com a `stage` e a `main` do repositório oficial depende da aprovação do coordenador.
- Ao abrir um Pull Request, explique as alterações, os testes realizados e eventuais mudanças nas migrations.
- O fork é público. Nunca inclua dados reais de alunos, senhas, tokens ou informações pessoais em códigos, testes, issues ou Pull Requests.

### 6.2. Sobre o servidor

- O acesso ao servidor depende da VPN da POLI, cuja liberação é realizada pela DTI.
- Durante o semestre, a equipe não teve acesso direto ao servidor. As operações foram realizadas pelo coordenador, a partir de comandos enviados pela equipe.
- Solicitações de execução de comandos no servidor devem ser acompanhadas de instruções claras e, sempre que possível, de procedimentos para desfazer alterações.
- O endereço `acadflow.poli.br` pode apresentar problemas de DNS dentro da própria rede da POLI. Em redes externas, como a conexão móvel, o acesso pode funcionar normalmente.
- Antes de tratar um relato de indisponibilidade como falha geral do sistema, verifique de qual rede o usuário estava tentando acessar.
- A imagem da aplicação é construída após a integração na `main` oficial, conforme o fluxo de implantação.
- O Watchtower é utilizado na atualização dos contêineres da aplicação.
- O comando `mostrar_aviso_do_dia` pode apresentar nomes de alunos reais. Sua saída deve ser tratada como informação restrita e nunca compartilhada em grupos públicos ou no GitHub.

### 6.3. Sobre as dependências

Durante o desenvolvimento, foi identificada uma incompatibilidade entre versões do Kombu e do transporte Redis.

Na versão anterior, o worker do Celery apresentava reinicializações contínuas.

A correção foi incorporada à versão 1.4.1, com versões fixadas no `requirements.txt`:

```text
celery==5.4.0
kombu[redis]==5.6.2
redis==6.1.0
```

O Dockerfile também realiza verificações de compatibilidade durante a construção da imagem.

**Não atualize essas dependências isoladamente sem executar os testes e verificar o funcionamento do worker.**

### 6.4. Sobre os testes

- Utilize `python manage.py test acompanhamento` para executar os testes do módulo.
- Utilize `python manage.py test processos` para verificar as funcionalidades relacionadas ao aplicativo original.
- Utilize `python manage.py check` para verificar a configuração do Django.
- Utilize `python manage.py makemigrations --check --dry-run` para identificar alterações nos modelos que ainda não possuem migrations.
- Os testes das telas do módulo precisam considerar `ACOMPANHAMENTO_ATIVO=True`.
- O ensaio de produção utiliza Docker Compose e testa as migrations sobre uma base correspondente à `main` oficial.
- No Windows, o ensaio de produção pode exigir atenção à variável `COMPOSE_PATH_SEPARATOR` e aos finais de linha dos arquivos utilizados nos contêineres.

As instruções completas estão no [Documento Técnico](entrega-modulo-acompanhamento.md).

### 6.5. Sobre os dados fictícios

O projeto possui o comando:

```bash
python manage.py gerar_dados_ficticios --semente 42
```

Ele cria dados de demonstração para testar as funcionalidades do módulo.

A opção `--semente 42` permite reproduzir a geração de dados com os mesmos parâmetros aleatórios.

Também é possível utilizar `--data-referencia` para fixar a data de referência dos cálculos, tornando os testes manuais mais previsíveis.

O comando recusa execução com `DEBUG=False`, evitando a geração acidental de dados fictícios em produção.

Os dados de demonstração devem ser utilizados apenas em ambientes apropriados para testes.

### 6.6. Sobre as pessoas e responsabilidades

| Pessoa | Responsabilidade ou conhecimento |
|---|---|
| Prof. Leandro Honorato | Coordenador do PPGEC. Decide as regras de negócio, realiza os merges no repositório oficial e possui acesso ao servidor. |
| Denis | Bolsista da secretaria. Conhece a rotina dos prazos e os indicadores utilizados pela secretaria. |
| Secretaria do PPGEC | Utiliza as funcionalidades de acompanhamento e mantém o calendário acadêmico atualizado. |
| Coordenação do PPGEC | Valida as regras de negócio e acompanha os indicadores acadêmicos. |

As regras de negócio confirmadas estão registradas no [Documento de Negócio](documento-de-negocio.md).

Qualquer mudança que altere prazos, indicadores, permissões ou critérios de irregularidade deve ser previamente validada pela coordenação.

## 7. Os cinco arquivos mais importantes para começar

Se a nova equipe tiver pouco tempo para entender o código, recomendamos começar por estes arquivos:

| Arquivo | Por que é importante |
|---|---|
| `acompanhamento/situacao.py` | Centraliza os cálculos das situações acadêmicas e dos prazos. |
| `acompanhamento/servicos.py` | Contém os serviços e cálculos utilizados nos painéis e indicadores. |
| `acompanhamento/views.py` | Conecta as requisições dos usuários às funcionalidades e telas do módulo. |
| `acompanhamento/tasks.py` | Define a execução assíncrona dos avisos de irregularidade. |
| `acompanhamento/models.py` | Define os modelos e os registros persistidos pelo módulo. |

Também recomendamos conhecer `acompanhamento/permissoes.py`, `acompanhamento/chave.py` e `ppgec/celery.py` antes de modificar o controle de acesso ou o serviço de avisos.

## 8. Por onde começar

Se você acabou de entrar no projeto, siga esta ordem:

1. Leia o [Documento de Negócio](documento-de-negocio.md) para entender o problema, o público atendido e as regras acadêmicas.
2. Leia o [Manual de Uso da Secretaria](manual-de-uso-secretaria.md) para conhecer a utilização das funcionalidades.
3. Leia o [Documento Técnico](entrega-modulo-acompanhamento.md) para compreender a arquitetura, as dependências, os testes e a implantação.
4. Sincronize o fork com a `main` oficial.
5. Siga o [Guia do Ambiente Local](guia-do-ambiente-local.md) para configurar o ambiente.
6. Gere os dados fictícios e execute o sistema com o módulo habilitado.
7. Acesse `/acompanhamento/` e explore as funcionalidades.
8. Execute os testes automatizados antes de iniciar qualquer alteração.
9. Converse com o coordenador e com a secretaria antes de modificar regras de negócio.

## 9. Se tivéssemos 10 minutos com a nova equipe...

Diríamos o seguinte:

O módulo já está desenvolvido, integrado e em produção. Não é necessário começar do zero nem reestruturar o aplicativo.

A primeira coisa é entender como o AcadFlow funciona, porque o Acompanhamento não é um sistema independente: ele utiliza modelos, permissões, rotas e serviços que já existiam.

Tenha cuidado especial com `situacao.py`, pois a lista, o cartão, a planilha e os avisos dependem da consistência dos cálculos realizados ali.

Não altere regras acadêmicas por conta própria. Os prazos e critérios foram discutidos com a secretaria e confirmados pela coordenação.

Também não atualize as dependências do Celery, Kombu e Redis sem testar. Já tivemos um problema de compatibilidade que foi resolvido na versão 1.4.1.

Por fim, mantenha os testes passando, respeite o fluxo de Pull Requests e converse com o coordenador antes de realizar alterações em produção.

**O principal aprendizado da equipe E4 foi que a continuidade do AcadFlow depende tanto de compreender o código quanto de preservar as regras acadêmicas e administrativas que justificam sua existência.**

---

**Documento:** Passagem de Conhecimento – Módulo de Acompanhamento Acadêmico  
**Versão do módulo:** 1.4.1  
**Data de referência:** 21/11/2026  
**Equipe responsável pela entrega:** E4 – Engenharia de Software – UPE.
