from datetime import timedelta
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import (
    Aluno,
    Docente,
    ItemSolicitacaoMatricula,
    OfertaDisciplina,
    Processo,
    SolicitacaoMatricula,
    TrajetoriaAcademica,
    User,
)


def processos_atrasados_base_queryset():
    return Processo.objects.filter(
        prazo_limite__lt=timezone.localdate(),
    ).exclude(status=Processo.StatusProcesso.FINALIZADO)


def processos_atrasados_queryset(user):
    queryset = processos_atrasados_base_queryset()
    if not user.is_authenticated:
        return queryset.none()

    if user.tipo_usuario == User.TipoUsuario.SERVIDOR:
        return queryset

    if user.tipo_usuario == User.TipoUsuario.DOCENTE:
        is_coordenador = Docente.objects.filter(pk=user.pk, coordenador=True).exists()
        if is_coordenador:
            return queryset

        orientandos = Aluno.objects.filter(
            trajetorias__status=TrajetoriaAcademica.Status.ATIVA,
        ).filter(
            Q(trajetorias__orientador=user) | Q(trajetorias__coorientador=user)
        ).values("id")
        return queryset.filter(
            Q(usuario_criado_por=user)
            | Q(usuario_criado_por__in=orientandos)
            | Q(setor_atual__nome__icontains="pleno")
        )

    return queryset.filter(usuario_criado_por=user)


def processos_atrasados_url(user):
    if user.is_authenticated and user.tipo_usuario in {User.TipoUsuario.SERVIDOR, User.TipoUsuario.DOCENTE}:
        if user.tipo_usuario == User.TipoUsuario.SERVIDOR or Docente.objects.filter(
            pk=user.pk,
            coordenador=True,
        ).exists():
            return f"{reverse('coordenacao_processos')}?atrasados=1"
    return f"{reverse('menu_meus_processos')}?my_atrasados=1"


def prazo_limite_padrao(tipo_processo, data_base=None):
    data_base = data_base or timezone.localdate()
    return data_base + timedelta(days=Processo.prazo_dias_para_tipo(tipo_processo))


def encontros_tem_choque(encontro_a, encontro_b):
    return (
        encontro_a.dia_semana == encontro_b.dia_semana
        and encontro_a.hora_inicio < encontro_b.hora_fim
        and encontro_a.hora_fim > encontro_b.hora_inicio
    )


def validar_choque_ofertas(ofertas, *, aluno=None, periodo=None, ignorar_solicitacao=None):
    ofertas = list(ofertas)
    encontros = []
    for oferta in ofertas:
        for encontro in oferta.encontros.all():
            encontros.append((oferta, encontro))

    if aluno and periodo:
        itens_homologados = (
            ItemSolicitacaoMatricula.objects.select_related("oferta", "oferta__disciplina")
            .prefetch_related("oferta__encontros")
            .filter(
                solicitacao__aluno=aluno,
                solicitacao__periodo=periodo,
                status=ItemSolicitacaoMatricula.Status.HOMOLOGADO,
            )
        )
        if ignorar_solicitacao:
            itens_homologados = itens_homologados.exclude(solicitacao=ignorar_solicitacao)
        for item in itens_homologados:
            if item.oferta_id not in {oferta.id for oferta in ofertas}:
                for encontro in item.oferta.encontros.all():
                    encontros.append((item.oferta, encontro))

    for indice, (oferta_a, encontro_a) in enumerate(encontros):
        for oferta_b, encontro_b in encontros[indice + 1:]:
            if oferta_a.id == oferta_b.id:
                continue
            if encontros_tem_choque(encontro_a, encontro_b):
                raise ValidationError(
                    "Choque de horário entre "
                    f"{oferta_a.disciplina.nome} e {oferta_b.disciplina.nome} "
                    f"em {encontro_a.get_dia_semana_display()}."
                )


def alunos_ativos_sem_matricula(periodo):
    return (
        Aluno.objects.filter(trajetorias__status=TrajetoriaAcademica.Status.ATIVA)
        .exclude(solicitacoes_matricula__periodo=periodo)
        .distinct()
        .order_by("nome", "email")
    )


def tipo_aluno_matricula_por_trajetoria(trajetoria):
    if trajetoria.nivel_curso == Aluno.NivelCurso.POSDOUTORADO:
        return None
    if trajetoria.nivel_curso == Aluno.NivelCurso.ALUNO_ESPECIAL:
        return SolicitacaoMatricula.TipoAluno.ESPECIAL
    return SolicitacaoMatricula.TipoAluno.REGULAR


@transaction.atomic
def salvar_solicitacao_matricula(
    *,
    aluno,
    periodo,
    tipo_aluno,
    ofertas,
    aceitar_lista_espera=False,
    observacao="",
    tipo_matricula=SolicitacaoMatricula.TipoMatricula.DISCIPLINAS,
):
    if aluno.tipo_usuario != User.TipoUsuario.ALUNO:
        raise ValidationError("A solicitação de matrícula deve ser feita por aluno.")
    trajetoria_ativa = aluno.trajetoria_ativa()
    if not trajetoria_ativa:
        raise ValidationError("A solicitação de matrícula exige trajetória acadêmica ativa.")
    tipo_aluno_esperado = tipo_aluno_matricula_por_trajetoria(trajetoria_ativa)
    if tipo_aluno_esperado is None:
        raise ValidationError("Aluno de pós-doutorado não realiza matrícula em disciplinas.")
    if tipo_aluno != tipo_aluno_esperado:
        raise ValidationError("O tipo de aluno deve ser obtido da trajetória acadêmica ativa.")
    if not periodo.aceita_solicitacao_matricula:
        raise ValidationError("O período não está aberto para matrícula ou modificação.")

    if tipo_matricula == SolicitacaoMatricula.TipoMatricula.VINCULO:
        solicitacao, _ = SolicitacaoMatricula.objects.select_for_update().get_or_create(
            periodo=periodo,
            aluno=aluno,
            defaults={"tipo_aluno": tipo_aluno, "status": SolicitacaoMatricula.Status.RASCUNHO},
        )
        if solicitacao.status == SolicitacaoMatricula.Status.CANCELADA:
            raise ValidationError("A solicitação deste período está cancelada.")
        if solicitacao.itens.exclude(status=ItemSolicitacaoMatricula.Status.CANCELADO).exists():
            raise ValidationError("Não é possível solicitar matrícula vínculo com disciplinas ativas neste período.")
        solicitacao.tipo_matricula = SolicitacaoMatricula.TipoMatricula.VINCULO
        solicitacao.tipo_aluno = tipo_aluno
        solicitacao.observacao_aluno = observacao
        solicitacao.status = SolicitacaoMatricula.Status.SOLICITADA
        solicitacao.solicitada_em = solicitacao.solicitada_em or timezone.now()
        solicitacao.save()
        return solicitacao

    ofertas = list(
        OfertaDisciplina.objects.select_for_update()
        .select_related("disciplina", "periodo")
        .prefetch_related("encontros")
        .filter(pk__in=[oferta.pk for oferta in ofertas], periodo=periodo)
    )
    if not ofertas:
        raise ValidationError("Selecione ao menos uma disciplina ofertada.")

    validar_choque_ofertas(ofertas, aluno=aluno, periodo=periodo)

    solicitacao, _ = SolicitacaoMatricula.objects.select_for_update().get_or_create(
        periodo=periodo,
        aluno=aluno,
        defaults={"tipo_aluno": tipo_aluno, "status": SolicitacaoMatricula.Status.RASCUNHO},
    )
    if solicitacao.status == SolicitacaoMatricula.Status.CANCELADA:
        raise ValidationError("A solicitação deste período está cancelada.")

    solicitacao.tipo_matricula = SolicitacaoMatricula.TipoMatricula.DISCIPLINAS
    solicitacao.tipo_aluno = tipo_aluno
    solicitacao.observacao_aluno = observacao
    solicitacao.status = SolicitacaoMatricula.Status.SOLICITADA
    solicitacao.solicitada_em = solicitacao.solicitada_em or timezone.now()
    solicitacao.save()

    for oferta in ofertas:
        if solicitacao.itens.filter(oferta=oferta).exclude(status=ItemSolicitacaoMatricula.Status.CANCELADO).exists():
            continue
        status = ItemSolicitacaoMatricula.Status.SOLICITADO
        if oferta.vagas_disponiveis(tipo_aluno) <= 0:
            if not aceitar_lista_espera:
                raise ValidationError(f"Não há vagas disponíveis em {oferta.disciplina.nome}.")
            status = ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA
        ItemSolicitacaoMatricula.objects.create(
            solicitacao=solicitacao,
            oferta=oferta,
            status=status,
        )

    solicitacao.atualizar_status_por_itens()
    return solicitacao


@transaction.atomic
def homologar_solicitacao_vinculo(*, solicitacao, usuario):
    solicitacao = SolicitacaoMatricula.objects.select_for_update().get(pk=solicitacao.pk)
    if solicitacao.tipo_matricula != SolicitacaoMatricula.TipoMatricula.VINCULO:
        raise ValidationError("A solicitação não é de matrícula vínculo.")
    if solicitacao.status in {SolicitacaoMatricula.Status.CANCELADA, SolicitacaoMatricula.Status.INDEFERIDA}:
        raise ValidationError("Não é possível homologar solicitação cancelada ou indeferida.")
    solicitacao.status = SolicitacaoMatricula.Status.HOMOLOGADA
    solicitacao.homologada_em = timezone.now()
    solicitacao.homologada_por = usuario
    solicitacao.save()
    return solicitacao


@transaction.atomic
def indeferir_solicitacao_vinculo(*, solicitacao, usuario, motivo=""):
    solicitacao = SolicitacaoMatricula.objects.select_for_update().get(pk=solicitacao.pk)
    if solicitacao.tipo_matricula != SolicitacaoMatricula.TipoMatricula.VINCULO:
        raise ValidationError("A solicitação não é de matrícula vínculo.")
    if solicitacao.status == SolicitacaoMatricula.Status.CANCELADA:
        raise ValidationError("Não é possível indeferir solicitação cancelada.")
    solicitacao.status = SolicitacaoMatricula.Status.INDEFERIDA
    solicitacao.observacao_secretaria = motivo
    solicitacao.save()
    return solicitacao


@transaction.atomic
def homologar_item_matricula(*, item, usuario):
    item = (
        ItemSolicitacaoMatricula.objects.select_for_update()
        .select_related("solicitacao", "oferta", "oferta__disciplina")
        .get(pk=item.pk)
    )
    OfertaDisciplina.objects.select_for_update().get(pk=item.oferta_id)
    if item.status == ItemSolicitacaoMatricula.Status.HOMOLOGADO:
        return item
    if item.status in {ItemSolicitacaoMatricula.Status.CANCELADO, ItemSolicitacaoMatricula.Status.INDEFERIDO}:
        raise ValidationError("Não é possível homologar item cancelado ou indeferido.")
    if item.oferta.vagas_disponiveis(item.solicitacao.tipo_aluno) <= 0:
        raise ValidationError("Não há vagas disponíveis para homologar esta matrícula.")

    validar_choque_ofertas(
        [item.oferta],
        aluno=item.solicitacao.aluno,
        periodo=item.solicitacao.periodo,
        ignorar_solicitacao=item.solicitacao,
    )
    item.status = ItemSolicitacaoMatricula.Status.HOMOLOGADO
    item.homologado_em = timezone.now()
    item.homologado_por = usuario
    item.save()
    item.solicitacao.atualizar_status_por_itens(usuario=usuario)
    return item


@transaction.atomic
def indeferir_item_matricula(*, item, usuario, motivo=""):
    item = ItemSolicitacaoMatricula.objects.select_for_update().select_related("solicitacao").get(pk=item.pk)
    if item.status == ItemSolicitacaoMatricula.Status.CANCELADO:
        raise ValidationError("Não é possível indeferir item cancelado.")
    item.status = ItemSolicitacaoMatricula.Status.INDEFERIDO
    item.indeferido_em = timezone.now()
    item.indeferido_por = usuario
    item.motivo_indeferimento = motivo
    item.save()
    item.solicitacao.atualizar_status_por_itens()
    return item


@transaction.atomic
def cancelar_item_matricula(*, item, usuario=None):
    item = ItemSolicitacaoMatricula.objects.select_for_update().select_related("solicitacao", "oferta").get(pk=item.pk)
    item.status = ItemSolicitacaoMatricula.Status.CANCELADO
    item.save()
    item.solicitacao.atualizar_status_por_itens()
    promover_proximo_lista_espera(oferta=item.oferta, tipo_aluno=item.solicitacao.tipo_aluno, usuario=usuario)
    return item


@transaction.atomic
def promover_proximo_lista_espera(*, oferta, tipo_aluno, usuario):
    oferta = OfertaDisciplina.objects.select_for_update().get(pk=oferta.pk)
    if oferta.vagas_disponiveis(tipo_aluno) <= 0:
        return None
    proximo = (
        ItemSolicitacaoMatricula.objects.select_for_update()
        .select_related("solicitacao", "oferta")
        .filter(
            oferta=oferta,
            solicitacao__tipo_aluno=tipo_aluno,
            status=ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA,
        )
        .order_by("solicitado_em", "id")
        .first()
    )
    if not proximo:
        return None
    return homologar_item_matricula(item=proximo, usuario=usuario)


def _xlsx_coluna(indice):
    letras = ""
    while indice:
        indice, resto = divmod(indice - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def _xlsx_planilha_xml(linhas):
    rows = []
    for row_idx, linha in enumerate(linhas, start=1):
        cells = []
        for col_idx, valor in enumerate(linha, start=1):
            ref = f"{_xlsx_coluna(col_idx)}{row_idx}"
            texto = escape("" if valor is None else str(valor))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{texto}</t></is></c>')
        rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(rows)}</sheetData>'
        "</worksheet>"
    )


def gerar_xlsx_lista_oferta(oferta):
    itens = (
        oferta.itens_matricula.select_related("solicitacao", "solicitacao__aluno", "homologado_por")
        .order_by("status", "solicitado_em", "solicitacao__aluno__nome")
    )
    linhas = [[
        "Matrícula",
        "Nome",
        "E-mail",
        "Tipo de aluno",
        "Status",
        "Solicitado em",
        "Homologado em",
        "Homologado por",
    ]]
    for item in itens:
        linhas.append([
            item.solicitacao.aluno.matricula,
            item.solicitacao.aluno.nome,
            item.solicitacao.aluno.email,
            item.solicitacao.get_tipo_aluno_display(),
            item.get_status_display(),
            timezone.localtime(item.solicitado_em).strftime("%d/%m/%Y %H:%M") if item.solicitado_em else "",
            timezone.localtime(item.homologado_em).strftime("%d/%m/%Y %H:%M") if item.homologado_em else "",
            item.homologado_por.nome if item.homologado_por else "",
        ])

    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as xlsx:
        xlsx.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ))
        xlsx.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ))
        xlsx.writestr("xl/workbook.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Alunos" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ))
        xlsx.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ))
        xlsx.writestr("xl/worksheets/sheet1.xml", _xlsx_planilha_xml(linhas))
    return buffer.getvalue()
