# Documento de negócio: módulo de acompanhamento acadêmico

**Projeto:** AcadFlow – Sistema de Gestão de Processos do Programa de Pós-Graduação em Engenharia da Computação (PPGEC) da UPE  
**Módulo:** Acompanhamento Acadêmico  
**Equipe:** Equipe E4  
**Disciplina:** Engenharia de Software  
**Período:** Segundo semestre de 2026  
**Versão do sistema:** 1.4.1  
**Data de referência:** 21/11/2026

## Identificação da equipe

O módulo de Acompanhamento Acadêmico foi desenvolvido pela Equipe E4, composta por oito integrantes, com responsabilidades distribuídas entre gestão do produto, desenvolvimento, arquitetura, análise de dados, automação e garantia de qualidade.

| Integrante | Papel | Responsabilidades |
|---|---|---|
| Carlos Bentzen | Product Owner | Alinhamento com o cliente, levantamento e validação de requisitos e priorização das demandas. |
| Gabriela Pereira | Scrum Master | Facilitação dos rituais ágeis, acompanhamento das atividades e organização do fluxo de trabalho da equipe. |
| Gustavo Souto | Tech Lead | Definição da arquitetura da solução, orientação técnica da equipe e revisão de código e Pull Requests no GitHub. |
| Daniel Medeiros | Lead Frontend | Desenvolvimento e manutenção das interfaces, telas e componentes da aplicação utilizando as tecnologias integradas ao Django. |
| Marllon Kauan | Lead Backend | Desenvolvimento das funcionalidades no backend com Python e Django, implementação das regras de negócio e integração com o banco de dados. |
| Antonio Carlos | Especialista BI | Desenvolvimento e aprimoramento de dashboards, indicadores e métricas para acompanhamento acadêmico. |
| Dyhego Alexandre | Especialista em Automação | Análise dos fluxos de processos e apoio à implementação e manutenção de mecanismos de automação do sistema. |
| Sergio da Silva | QA & DevOps | Configuração e manutenção dos ambientes Docker, pipelines de integração e entrega contínua no GitHub Actions e atividades de garantia da qualidade. |

## Apresentação do documento

Este documento apresenta o módulo de Acompanhamento Acadêmico do AcadFlow, explicando o problema que ele resolve, seus usuários, objetivos, funcionalidades e regras de negócio.

As informações sobre instalação, configuração, arquitetura e manutenção estão no [Documento Técnico](entrega-modulo-acompanhamento.md).

As pendências, limitações conhecidas e orientações para continuidade estão no [Documento de Passagem de Conhecimento](passagem-de-conhecimento.md).

As orientações de utilização das telas estão no [Manual da Secretaria](manual-de-uso-secretaria.md).

## Histórico de alterações

Este documento é a evolução da Especificação de Requisitos do módulo, versão 0.2, de 06/10/2026.

| Data | O que mudou |
|---|---|
| 06/10/2026 | Especificação de Requisitos enviada ao coordenador para validação, com decisões pendentes. |
| 12/10/2026 | Regras de prazo respondidas pela secretaria. |
| 16/10/2026 | Regras de prazo confirmadas pelo coordenador. |
| 27/10/2026 | Situações na tela reduzidas a três: irregular, regular e não foi possível calcular. |
| 06/11/2026 | Painel por Professor incluído, com as regras confirmadas pelo coordenador. |
| 16/11/2026 | Dashboard incorporado à página de Acompanhamento, conforme decisão do coordenador. |
| 21/11/2026 | Documento reescrito para descrever o que está em produção. As decisões pendentes saíram, porque todas foram respondidas. Incluídas as alterações referentes à reorganização do Dashboard e atualização das informações do módulo. |

## 1. O problema

Até 2026, a secretaria do PPGEC controlava fora do sistema quais alunos estavam com prazo vencido. Para saber quem não apresentou o projeto de dissertação, quem perdeu a qualificação ou quem passou do prazo de defesa, era preciso abrir aluno por aluno.

A coordenação também não tinha números do programa: quantos alunos de cada turma concluíram, quantos foram desligados, como os processos andam e como os orientandos se dividem entre os professores.

Esses dois pedidos saíram da reunião com o coordenador em 22/09/2026. O primeiro, de maior prioridade, é saber quem está fora do prazo. O segundo são os painéis de indicadores.

### 1.1. Objetivo do módulo

O módulo de Acompanhamento Acadêmico tem como objetivo centralizar informações sobre a situação acadêmica dos discentes do PPGEC, permitindo que a secretaria e a coordenação identifiquem alunos em situação irregular, acompanhem prazos regimentais e consultem indicadores relacionados às trajetórias acadêmicas e aos processos do programa.

A solução busca facilitar o acompanhamento dos alunos durante o mestrado e o doutorado, reduzir a necessidade de consultas individuais e oferecer informações organizadas para apoiar a gestão acadêmica e a tomada de decisões.

### 1.2. Valor entregue

O módulo oferece à secretaria e à coordenação uma visão centralizada da situação dos alunos, permitindo identificar atrasos, acompanhar marcos acadêmicos e consultar indicadores de desempenho do programa.

Entre os principais benefícios estão:

- Redução do trabalho manual de identificação de alunos com prazos vencidos.
- Maior visibilidade sobre a situação acadêmica dos discentes.
- Centralização das informações relacionadas a prazos e processos.
- Facilidade para consultar e exportar listas de alunos irregulares.
- Acompanhamento dos resultados das turmas ao longo do tempo.
- Visualização de indicadores relacionados aos docentes e seus orientandos.
- Apoio à tomada de decisões administrativas e acadêmicas.

O módulo funciona como ferramenta de apoio à gestão, sem substituir as decisões administrativas da secretaria ou da coordenação.

## 2. Para quem

| Quem | Para que usa |
|---|---|
| Secretaria do PPGEC | Ver a lista de alunos irregulares, baixar a planilha, cadastrar as datas do semestre e receber o aviso diário por e-mail. |
| Coordenação do PPGEC | Acompanhar os painéis, receber o aviso diário e ver os números de cada professor. |
| Docente coordenador | Tudo o que a coordenação vê, inclusive o Painel por Professor. |
| Servidores, bolsistas e voluntários com acesso de gestão | Mesmas telas da secretaria, exceto o Painel por Professor. |

Alunos e docentes sem papel de gestão não veem o módulo. O módulo não envia nada a aluno nem a orientador.

## 3. O que o módulo oferece

| Funcionalidade | O que faz |
|---|---|
| Página de Acompanhamento | Reúne as telas do módulo em cartões e mostra o total de alunos irregulares e quando o último aviso rodou. |
| Cartão "Alunos irregulares" | Na página inicial do sistema, mostra o total e abre a lista já filtrada. |
| Lista de alunos por marco | Na tela de alunos, filtra por marco, situação e semestre, e mostra data limite, situação e motivo. |
| Planilha em Excel | Baixa a lista filtrada, com os filtros, o total e a data no topo. |
| Calendário de Marcos | Cadastro das datas limite de projeto e qualificação de cada semestre. |
| Aviso diário | E-mail às 7h para a secretaria e a coordenação quando algum aluno passa a estar irregular. |
| Painel de Sucesso por Turma | Gráfico com concluídos, desligados, trancados, ativos regulares e irregulares e alunos com prorrogação de cada turma. |
| Painel de Processos | Processos abertos, em atraso e tempo médio até a finalização, por semestre ou por intervalo de datas. |
| Painel de Docentes | Total de docentes, permanentes e colaboradores, internos e externos e distribuição por polo. |
| Painel por Professor | Números de cada orientador, gráfico por turma dos alunos dele e previsão de conclusão dos alunos ativos. |
| Dashboard | Tela original do sistema, com docentes e orientandos. Desde 16/11/2026, é aberta pela página de Acompanhamento. |

## 4. Regras de negócio

As regras de prazo foram respondidas pela secretaria em 12/10/2026 e confirmadas pelo coordenador em 16/10/2026. As do Painel por Professor foram confirmadas em 06/11/2026.

### 4.1. Uso e acesso

| ID | Regra |
|---|---|
| RN-01 | Só a gestão usa o módulo, pela mesma regra que o sistema já aplica à tela de alunos: perfil Servidor ou Bolsista/Voluntário, membro ativo da Secretaria PPGEC ou da Coordenação PPG, ou docente com a marcação de coordenador. |
| RN-02 | O Painel por Professor é só do docente coordenador e dos membros da Coordenação PPG. Para os outros, o cartão não aparece e a tela nega o acesso. |
| RN-03 | A situação é indicativa e serve para a secretaria conferir. O módulo não desliga aluno, não altera cadastro e não avisa aluno nem orientador. |
| RN-04 | A situação é calculada na hora da consulta, a partir do cadastro. Corrigido o cadastro, a situação muda na consulta seguinte. Por isso, registrar logo cada apresentação, prorrogação, trancamento e defesa é o que mantém a lista correta. |
| RN-05 | O módulo e o aviso podem ser ligados e desligados no servidor, sem mudar o código. Desligado, o módulo não aparece para ninguém. |

### 4.2. Quem entra no cálculo

| ID | Regra |
|---|---|
| RN-06 | Só entram alunos com trajetória ativa de mestrado ou doutorado. Concluídos, desligados, trancados, em homologação, removidos, alunos especiais e pós-doutorado ficam de fora. |
| RN-07 | Quem já defendeu tem a trajetória concluída e sai da lista da defesa. |

### 4.3. Projeto de dissertação e qualificação

| ID | Regra |
|---|---|
| RN-08 | No mestrado, o marco é a apresentação do projeto de dissertação. Vale a data limite do projeto registrada na trajetória. Sem ela, vale a data do Calendário de Marcos para o semestre do projeto. |
| RN-09 | No doutorado, o marco é a qualificação, no quinto semestre contado do ingresso. Vale a data do Calendário de Marcos para esse semestre. |
| RN-10 | Os dias de trancamento somam à data limite. |
| RN-11 | Conceito A ou B: marco cumprido. A marcação "Projeto/qualificação concluído" na trajetória também conta como cumprido. |
| RN-12 | Conceito C: uma única repetição, em até 3 meses no mestrado ou 6 no doutorado. |
| RN-13 | Conceito D: irregular, sem nova chance. |
| RN-14 | Sem apresentação até a data limite: uma nova chance, no mesmo prazo de 3 ou 6 meses. Se o prazo passar, ou se apresentar e não for aprovado, fica irregular sem nova chance. |

### 4.4. Defesa

| ID | Regra |
|---|---|
| RN-15 | O prazo regimental termina no último dia do mês em que se completam 24 meses (mestrado) ou 48 meses (doutorado) do mês de ingresso. |
| RN-16 | As prorrogações somam meses e valem até o fim do mês em que terminam. Depois, somam-se os dias de trancamento. Exemplo: ingresso em fevereiro de 2025, no mestrado, com 3 meses de prorrogação, tem limite em 31/05/2027. |
| RN-17 | O aluno está regular até o limite, inclusive no próprio dia. A partir do dia seguinte, sem defesa registrada, fica irregular. |

### 4.5. Situação na tela

| Situação | Quando aparece |
|---|---|
| Irregular | Atraso no marco, com ou sem nova chance. O motivo explica o caso e, quando há, mostra o prazo da reapresentação. |
| Regular | Em dia ou com o marco já cumprido. |
| Não foi possível calcular | Falta um dado para o cálculo, como a data do semestre no calendário ou o mês de ingresso. |

### 4.6. Aviso diário

| ID | Regra |
|---|---|
| RN-18 | O aviso vai para os membros ativos da Secretaria PPGEC e da Coordenação PPG que têm e-mail cadastrado. Nunca vai para aluno ou orientador. |
| RN-19 | Roda todo dia às 7h, horário de Recife. Só sai e-mail quando há aluno que passou a estar irregular ou aviso pendente de envio. |
| RN-20 | Cada aluno entra uma vez por marco e situação. Se ele sair da irregularidade e voltar, entra de novo. |
| RN-21 | O primeiro e-mail é o levantamento inicial, com todos os alunos que já estavam irregulares quando o aviso foi ligado. |
| RN-22 | O e-mail traz, para cada aluno, nome, nível, marco, situação, data limite, prazo da reapresentação e motivo, com link para a página do aluno. |
| RN-23 | Se o envio falhar, o aviso fica pendente e entra no e-mail do dia seguinte. Se o aviso ficar dias sem rodar, a execução seguinte recupera tudo. |

### 4.7. Lista e planilha

| ID | Regra |
|---|---|
| RN-24 | A lista na tela, o cartão de irregulares e a planilha usam o mesmo cálculo e mostram os mesmos alunos. |
| RN-25 | A planilha traz aluno, marco, situação, data limite, prazo da reapresentação, semestre do marco, detalhe, matrícula, e-mail, polo, nível, ingresso e orientador. |
| RN-26 | Cada planilha baixada fica registrada com quem baixou, quando e com quais filtros, porque ela tem dados pessoais. |

### 4.8. Painéis

| ID | Regra |
|---|---|
| RN-27 | Painel de Docentes: docente ativo é o que tem conta ativa. Docente não permanente conta como colaborador. Externos formam a categoria Externo à UPE, e internos sem polo aparecem como polo não informado. |
| RN-28 | Painel de Processos: o período padrão é o semestre corrente. Se as duas datas do intervalo forem preenchidas, o intervalo vale no lugar do semestre. O tempo médio conta só os processos finalizados no período. |
| RN-29 | Painel de Sucesso por Turma: a turma é o semestre de ingresso. Concluídos, desligados, trancados e ativos somam sempre 100%. Ativos regulares e irregulares somam a taxa de ativos. Com prorrogação é contado à parte. |
| RN-30 | Ativos regulares inclui quem aparece como "Não foi possível calcular", e a tela informa isso. |
| RN-31 | Turmas anteriores a 2026.2 aparecem com asterisco e aviso, porque registros anteriores ao AcadFlow podem estar incompletos. |

### 4.9. Painel por Professor

| ID | Regra |
|---|---|
| RN-32 | O professor é o orientador da trajetória. Coorientação fica fora. Alunos sem orientador aparecem em "Sem orientador(a)". |
| RN-33 | Prazo vencido: alunos cujo prazo regimental da defesa já passou, sem contar prorrogações nem trancamentos. |
| RN-34 | Taxa de sucesso: alunos que defenderam sobre os que têm prazo vencido. Sem nenhum aluno com prazo vencido, a tela mostra "Sem base", e não 0%. |
| RN-35 | Taxa de prorrogação: alunos com pelo menos uma prorrogação sobre o total de alunos do professor. |
| RN-36 | Previsão de conclusão: só alunos ativos. Usa o limite de defesa com prorrogações e trancamentos e mostra o semestre. Limite já passado aparece como "Prazo vencido", e aluno sem ingresso, como "Sem previsão". |
| RN-37 | O sistema guarda só o orientador atual. Se o orientador mudou, todo o histórico do aluno conta para o novo. |

## 5. O que o módulo não faz

| Item | Situação |
|---|---|
| Aviso ao aluno ou ao orientador | Fora. Notificar o aluno continua com a secretaria. |
| Desligar aluno ou alterar cadastro | Fora. Continua sendo decisão humana. |
| Aviso antes do vencimento do prazo | Não pedido para esta etapa. |
| Curvas semestre a semestre | Pedido da coordenação, sem prioridade definida. |
| Melhorias no Dashboard | Pedido da coordenação, sem prioridade definida. |
| Cada professor ver os próprios números | Não incluído. Hoje só o coordenador e a Coordenação PPG veem o Painel por Professor. |
| Tempo de tramitação por setor e taxa de deferimento | Exigiria ajustes no registro de processos. |

## 6. Riscos identificados

A continuidade e a confiabilidade do módulo dependem de alguns fatores que devem ser considerados pelas equipes responsáveis por sua manutenção.

| Risco | Possível impacto | Consideração para mitigação |
|---|---|---|
| Dados acadêmicos incompletos ou desatualizados | Cálculos incorretos ou impossibilidade de determinar a situação de determinados alunos. | Manter os cadastros atualizados e conferir os dados utilizados nos cálculos. |
| Mudanças nas regras acadêmicas | Resultados divergentes entre o sistema e os regulamentos institucionais. | Validar alterações com a secretaria e a coordenação antes de modificar as regras implementadas. |
| Indisponibilidade da infraestrutura | Interrupção do acesso ao módulo e de funcionalidades dependentes de serviços auxiliares. | Manter procedimentos de monitoramento e manutenção da infraestrutura. |
| Falhas no envio de e-mails | A secretaria e a coordenação podem deixar de receber avisos sobre alunos irregulares. | Monitorar a execução do aviso e verificar as pendências de envio. |
| Histórico acadêmico incompleto | Indicadores de turmas antigas podem não representar integralmente a realidade do programa. | Considerar as ressalvas apresentadas nos painéis e validar os dados históricos. |
| Alterações sem cobertura adequada de testes | Regressões em cálculos, filtros e indicadores já implementados. | Executar os testes automatizados e validar os resultados após mudanças. |
| Dependência de conhecimento institucional | Regras não documentadas ou decisões não confirmadas podem gerar implementações incorretas. | Consultar os responsáveis institucionais e manter a documentação atualizada. |

Os riscos relacionados a problemas técnicos conhecidos, limitações de implementação e procedimentos de manutenção estão detalhados no [Documento de Passagem de Conhecimento](passagem-de-conhecimento.md).

## 7. Termos

| Termo | Significado |
|---|---|
| Gestão | Usuários com acesso de gestão no AcadFlow, conforme a RN-01. |
| Trajetória | Registro de cada curso do aluno, com nível, ingresso, orientador e status. |
| Turma | Alunos com o mesmo semestre de ingresso, como 2025.1. |
| Marco | Projeto de dissertação (mestrado), qualificação (doutorado) ou defesa. |
| Data limite | Último dia para cumprir o marco, já com trancamentos e prorrogações, quando for o caso. |
| Reapresentação | Nova chance depois da data limite ou de um conceito C. |
| Prazo regimental | Prazo da defesa sem prorrogações nem trancamentos: 24 meses no mestrado e 48 no doutorado. |
| Prorrogação | Meses acrescentados ao prazo da defesa, concedidos pelo colegiado. |
| Trancamento | Período de suspensão, cujos dias somam aos prazos. |
| Levantamento inicial | Primeiro e-mail do aviso, com todos os irregulares daquele momento. |
| Sem base | Taxa que não pode ser calculada porque nenhum aluno chegou ao fim do prazo. |
| Previsão de conclusão | Semestre em que termina o limite de defesa de um aluno ativo. |

---

**Documento:** Documento de Negócio – Módulo de Acompanhamento Acadêmico  
**Versão:** 1.4.1  
**Data:** 21/11/2026  
**Equipe responsável:** Equipe E4 – Disciplina de Engenharia de Software.
