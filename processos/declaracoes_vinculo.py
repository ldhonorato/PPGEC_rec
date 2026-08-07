"""Importacao em lote das declaracoes de vinculo.

A secretaria emite os documentos a mao e traz a pasta de PDFs para o sistema.
Cada arquivo chega nomeado pelo CPF do aluno, e e esse nome que diz de quem e o
documento -- o unico dado de ligacao que existe entre a pasta e o cadastro.
"""

from django.db import transaction
from django.utils import timezone

from .models import (
    Aluno,
    DeclaracaoDeVinculo,
    PeriodoLetivo,
    SolicitacaoMatricula,
    validar_cpf_brasileiro,
)


EXTENSOES_ACEITAS = {".pdf"}
TAMANHO_MAXIMO = 5 * 1024 * 1024


def _cpf_do_nome(nome_do_arquivo):
    """Os digitos do nome, sem a extensao.

    Aceita 123.456.789-01.pdf, 12345678901.pdf e 12345678901_2026-2.pdf: a
    pasta vem de mais de uma maquina e a formatacao varia. O que nao se aceita e
    um nome com contagem de digitos diferente de 11, que nao e um CPF por mais
    que se limpe.
    """
    base = (nome_do_arquivo or "").rsplit(".", 1)[0]
    return "".join(char for char in base if char.isdigit())


def _extensao(nome_do_arquivo):
    partes = (nome_do_arquivo or "").rsplit(".", 1)
    return f".{partes[-1].lower()}" if len(partes) == 2 else ""


def importar_declaracoes_de_vinculo(*, periodo, arquivos, enviado_por, substituir=False):
    """Grava uma declaracao por arquivo e devolve o que aconteceu com cada um.

    Nada e gravado pela metade: um arquivo que nao case com aluno nenhum nao
    interrompe os demais, e vira uma linha do relatorio. O relatorio importa
    tanto quanto a importacao -- e nele que aparece o que precisa de decisao
    humana, e e a unica forma de saber que um aluno ficou sem declaracao.

    substituir=False protege o que ja existe: reenviar por engano a pasta
    inteira nao troca as declaracoes do semestre. Com True, a anterior e
    apagada e a nova entra no lugar.
    """
    resultados = []
    for arquivo in arquivos:
        nome = getattr(arquivo, "name", "") or "(sem nome)"
        resultado = {"arquivo": nome, "importado": False}

        extensao = _extensao(nome)
        if extensao not in EXTENSOES_ACEITAS:
            resultado["motivo"] = "A declaração precisa ser um PDF."
            resultados.append(resultado)
            continue

        if getattr(arquivo, "size", 0) > TAMANHO_MAXIMO:
            resultado["motivo"] = "Arquivo maior que 5 MB."
            resultados.append(resultado)
            continue

        cpf = _cpf_do_nome(nome)
        if len(cpf) != 11 or not validar_cpf_brasileiro(cpf):
            resultado["motivo"] = "O nome do arquivo não é um CPF válido."
            resultados.append(resultado)
            continue

        aluno = Aluno.objects.filter(cpf=cpf).first()
        if aluno is None:
            resultado["motivo"] = "Nenhum aluno cadastrado com este CPF."
            resultados.append(resultado)
            continue

        resultado["aluno"] = aluno.nome
        existente = DeclaracaoDeVinculo.objects.filter(aluno=aluno, periodo=periodo).first()
        if existente and not substituir:
            resultado["motivo"] = f"Já existe declaração de {aluno.nome} para {periodo.nome}."
            resultados.append(resultado)
            continue

        with transaction.atomic():
            if existente:
                # O arquivo antigo sai junto do registro: mante-lo no bucket
                # guardaria um documento que nenhuma tela alcanca -- e, no S3,
                # que ninguem lembra de pagar.
                existente.arquivo.delete(save=False)
                existente.delete()
            DeclaracaoDeVinculo.objects.create(
                aluno=aluno,
                periodo=periodo,
                arquivo=arquivo,
                enviado_por=enviado_por,
            )

        resultado["importado"] = True
        resultado["substituiu"] = bool(existente)
        # Nao impede o envio -- a secretaria sabe o que emitiu, e a matricula
        # pode entrar depois, quando a declaracao aparece sozinha. Mas nao pode
        # passar calado: sem solicitacao de matricula no periodo o aluno nao
        # alcanca o que acabou de ser enviado, e o relatorio e o unico lugar
        # onde isso aparece.
        resultado["invisivel_ao_aluno"] = not DeclaracaoDeVinculo.aluno_tem_vinculo_no_periodo(
            aluno.pk, periodo.pk
        )
        resultados.append(resultado)

    return resultados


def periodo_em_curso(data_base=None):
    """O semestre a que a declaracao vigente se refere.

    O projeto nao tinha essa nocao: cada tela escolhia o periodo por conta --
    "os que aceitam matricula", "o mais recente", o que veio na URL. Nenhuma
    delas serve aqui, porque a matricula abre e fecha dentro do semestre e o
    vinculo vale o semestre inteiro.

    A regra e a data: o periodo cujo intervalo contem hoje. As duas datas sao
    opcionais no modelo, e periodo sem data nao pode ser "o de agora" -- daria
    um vigente que muda conforme quem cadastrou lembrou de preencher. Quando
    nenhum intervalo contem hoje -- a folga entre dois semestres --, cai para o
    mais recente que ainda nao foi encerrado, que e o que a secretaria chamaria
    de periodo atual.
    """
    data_base = data_base or timezone.localdate()
    atual = (
        PeriodoLetivo.objects.filter(
            data_inicio__isnull=False,
            data_fim__isnull=False,
            data_inicio__lte=data_base,
            data_fim__gte=data_base,
        )
        .order_by("-nome")
        .first()
    )
    if atual is not None:
        return atual
    return (
        PeriodoLetivo.objects.filter(encerrado_manualmente_em__isnull=True)
        .exclude(status=PeriodoLetivo.Status.ENCERRADO)
        .order_by("-nome")
        .first()
    )


def declaracoes_do_aluno(aluno):
    """As declaracoes que o aluno pode abrir, da mais recente para a mais antiga.

    Filtra pela mesma regra que protege o arquivo: ter solicitado matricula no
    semestre da declaracao. Se a tela listasse o que a permissao recusa, o aluno
    veria o documento na lista e receberia 404 ao clicar -- pior do que nao ver.
    """
    periodos_com_vinculo = SolicitacaoMatricula.objects.filter(aluno=aluno).values("periodo_id")
    return (
        DeclaracaoDeVinculo.objects.filter(aluno=aluno, periodo_id__in=periodos_com_vinculo)
        .select_related("periodo")
        .order_by("-periodo__nome")
    )


def declaracao_vigente(aluno, periodo_em_curso):
    """A declaracao do periodo em curso, ou None.

    Devolver a mais recente quando falta a do semestre seria pior do que
    devolver nada: o aluno baixaria a de 2026.1 sem perceber e a apresentaria em
    2026.2, vencida, sem nenhum aviso de que era a errada.
    """
    if periodo_em_curso is None:
        return None
    if not DeclaracaoDeVinculo.aluno_tem_vinculo_no_periodo(aluno.pk, periodo_em_curso.pk):
        return None
    return DeclaracaoDeVinculo.objects.filter(aluno=aluno, periodo=periodo_em_curso).first()
