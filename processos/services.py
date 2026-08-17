from datetime import datetime, timedelta
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import (
    AlteracaoMatricula,
    Aluno,
    AulaPresencialOferta,
    Docente,
    ItemSolicitacaoMatricula,
    OfertaDisciplina,
    Processo,
    ReservaAmbiente,
    SetorMembro,
    SolicitacaoMatricula,
    TrajetoriaAcademica,
    User,
)


def _fase_evento_matricula(periodo, *, administrativo=False):
    if periodo.status == periodo.Status.MODIFICACAO_MATRICULA:
        return AlteracaoMatricula.Fase.MODIFICACAO
    if administrativo:
        return AlteracaoMatricula.Fase.ADMINISTRATIVA
    return AlteracaoMatricula.Fase.MATRICULA


def _snapshot_item_matricula(item):
    if not item:
        return {}
    return {
        "item_id": item.pk,
        "oferta_id": item.oferta_id,
        "disciplina_codigo": item.oferta.disciplina.codigo,
        "disciplina_nome": item.oferta.disciplina.nome,
        "status": item.status,
    }


def _registrar_alteracao_matricula(
    *, solicitacao, acao, realizado_por, fase, item=None, estado_anterior=None, estado_novo=None, justificativa=""
):
    return AlteracaoMatricula.objects.create(
        solicitacao=solicitacao,
        item=item,
        oferta=item.oferta if item else None,
        acao=acao,
        fase=fase,
        realizado_por=realizado_por,
        estado_anterior=estado_anterior or {},
        estado_novo=_snapshot_item_matricula(item) if estado_novo is None else estado_novo,
        justificativa=justificativa,
    )


def _is_secretaria_member(user):
    return user.is_authenticated and SetorMembro.objects.filter(
        usuario=user,
        data_saida__isnull=True,
        setor__ativo=True,
        setor__nome="Secretaria PPGEC",
    ).exists()


def processos_atrasados_base_queryset():
    return Processo.objects.filter(
        prazo_limite__lt=timezone.localdate(),
    ).exclude(status=Processo.StatusProcesso.FINALIZADO)


def processos_atrasados_queryset(user):
    queryset = processos_atrasados_base_queryset()
    if not user.is_authenticated:
        return queryset.none()

    if user.tipo_usuario in User.tipos_com_acesso_servidor() or _is_secretaria_member(user):
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
    if user.is_authenticated and (
        user.tipo_usuario == User.TipoUsuario.DOCENTE
        or user.tipo_usuario in User.tipos_com_acesso_servidor()
        or _is_secretaria_member(user)
    ):
        if user.tipo_usuario in User.tipos_com_acesso_servidor() or Docente.objects.filter(
            pk=user.pk,
            coordenador=True,
        ).exists() or _is_secretaria_member(user):
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
        itens_com_matricula = (
            ItemSolicitacaoMatricula.objects.select_related("oferta", "oferta__disciplina")
            .prefetch_related("oferta__encontros")
            .filter(
                solicitacao__aluno=aluno,
                solicitacao__periodo=periodo,
                status__in=[
                    ItemSolicitacaoMatricula.Status.SOLICITADO,
                    ItemSolicitacaoMatricula.Status.HOMOLOGADO,
                ],
            )
        )
        if ignorar_solicitacao:
            itens_com_matricula = itens_com_matricula.exclude(solicitacao=ignorar_solicitacao)
        for item in itens_com_matricula:
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


def solicitacao_matricula_feita_no_prazo(solicitacao):
    if not solicitacao.solicitada_em:
        return False
    data_solicitacao = timezone.localtime(solicitacao.solicitada_em).date()
    return solicitacao.periodo.matricula_inicio <= data_solicitacao <= solicitacao.periodo.matricula_fim


def datas_encontro_no_periodo(encontro):
    periodo = encontro.oferta.periodo
    if not periodo.data_inicio or not periodo.data_fim:
        return []
    data = periodo.data_inicio
    while data.weekday() != encontro.dia_semana:
        data += timedelta(days=1)
    datas = []
    while data <= periodo.data_fim:
        datas.append(data)
        data += timedelta(days=7)
    return datas


def minutos_encontro(encontro):
    inicio = datetime.combine(timezone.localdate(), encontro.hora_inicio)
    fim = datetime.combine(timezone.localdate(), encontro.hora_fim)
    return int((fim - inicio).total_seconds() // 60)


def carga_horaria_total_oferta_minutos(oferta):
    if oferta.disciplina.carga_horaria:
        return oferta.disciplina.carga_horaria * 60
    total = 0
    for encontro in oferta.encontros.all():
        total += minutos_encontro(encontro) * len(datas_encontro_no_periodo(encontro))
    return total


def carga_horaria_presencial_oferta_minutos(oferta):
    return sum(aula.carga_horaria_minutos for aula in oferta.aulas_presenciais.all())


def percentual_presencial_oferta(oferta):
    total = carga_horaria_total_oferta_minutos(oferta)
    if total <= 0:
        return 0
    return round((carga_horaria_presencial_oferta_minutos(oferta) / total) * 100, 1)


def oferta_hibrida_conforme(oferta):
    if oferta.modalidade != OfertaDisciplina.Modalidade.HIBRIDA:
        return True
    return percentual_presencial_oferta(oferta) >= 25


def ofertas_hibridas_nao_conformes():
    ofertas = (
        OfertaDisciplina.objects.filter(modalidade=OfertaDisciplina.Modalidade.HIBRIDA)
        .select_related("periodo", "disciplina", "docente_responsavel", "docente_colaborador")
        .prefetch_related("encontros", "aulas_presenciais__encontro")
        .order_by("-periodo__nome", "disciplina__nome")
    )
    return [oferta for oferta in ofertas if not oferta_hibrida_conforme(oferta)]


@transaction.atomic
def salvar_planejamento_presencial_oferta(*, oferta, usuario, selecoes):
    oferta = (
        OfertaDisciplina.objects.select_for_update()
        .select_related("periodo", "disciplina", "docente_responsavel")
        .prefetch_related("encontros")
        .get(pk=oferta.pk)
    )
    if oferta.modalidade != OfertaDisciplina.Modalidade.HIBRIDA:
        raise ValidationError("Apenas disciplinas híbridas possuem planejamento de aulas presenciais.")

    titulo = f"Aula presencial - {oferta.disciplina.codigo} - {oferta.disciplina.nome}"
    chaves = set()

    for aula in oferta.aulas_presenciais.select_related("reserva"):
        if aula.reserva:
            aula.reserva.excluir(usuario=usuario, justificativa="Alteração do planejamento presencial da oferta.")
        aula.delete()

    for selecao in selecoes:
        encontro = None
        if selecao.get("encontro_id"):
            encontro = oferta.encontros.get(pk=selecao["encontro_id"])
        data = selecao["data"]
        sala = selecao["sala"]
        hora_inicio = selecao["hora_inicio"] or getattr(encontro, "hora_inicio", None)
        hora_fim = selecao["hora_fim"] or getattr(encontro, "hora_fim", None)
        if not data or not hora_inicio or not hora_fim:
            raise ValidationError("Informe data, horário inicial e horário final para todas as aulas presenciais.")
        if hora_fim <= hora_inicio:
            raise ValidationError("O horário final deve ser posterior ao horário inicial.")
        chave = (data, hora_inicio, hora_fim)
        if chave in chaves:
            raise ValidationError("Há aulas presenciais duplicadas com a mesma data e horário.")
        chaves.add(chave)

        inicio = timezone.make_aware(datetime.combine(data, hora_inicio))
        fim = timezone.make_aware(datetime.combine(data, hora_fim))
        reserva = ReservaAmbiente(
            sala=sala,
            docente=oferta.docente_responsavel,
            criado_por=usuario,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo=titulo,
            inicio=inicio,
            fim=fim,
        )
        reserva.save()
        AulaPresencialOferta.objects.create(
            oferta=oferta,
            encontro=encontro,
            data=data,
            hora_inicio=hora_inicio,
            hora_fim=hora_fim,
            sala=sala,
            reserva=reserva,
            criado_por=usuario,
        )

    return oferta


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
        raise ValidationError("Aluno de Pós-Doutorado não realiza matrícula em disciplinas.")
    if tipo_aluno != tipo_aluno_esperado:
        raise ValidationError("O tipo de aluno deve ser obtido da trajetória acadêmica ativa.")
    if not periodo.aceita_solicitacao_matricula:
        raise ValidationError("O período não está aberto para matrícula ou modificação.")

    em_modificacao = periodo.status == periodo.Status.MODIFICACAO_MATRICULA
    solicitacao_existente = (
        SolicitacaoMatricula.objects.select_for_update()
        .select_related("periodo")
        .filter(periodo=periodo, aluno=aluno)
        .first()
    )
    if em_modificacao and (
        not solicitacao_existente or not solicitacao_matricula_feita_no_prazo(solicitacao_existente)
    ):
        raise ValidationError(
            "A modificação de matrícula está disponível apenas para quem enviou a solicitação no prazo de matrícula."
        )

    if tipo_matricula == SolicitacaoMatricula.TipoMatricula.VINCULO:
        solicitacao = solicitacao_existente
        criada = solicitacao is None
        if criada:
            solicitacao = SolicitacaoMatricula.objects.create(
                periodo=periodo,
                aluno=aluno,
                tipo_aluno=tipo_aluno,
                status=SolicitacaoMatricula.Status.RASCUNHO,
            )
        if solicitacao.status == SolicitacaoMatricula.Status.CANCELADA:
            raise ValidationError("A solicitação deste período está cancelada.")
        itens_ativos = list(solicitacao.itens.filter(status__in=[
            ItemSolicitacaoMatricula.Status.SOLICITADO,
            ItemSolicitacaoMatricula.Status.HOMOLOGADO,
            ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA,
        ]))
        if itens_ativos and not em_modificacao:
            raise ValidationError("Não é possível solicitar matrícula vínculo com disciplinas ativas neste período.")
        for item in itens_ativos:
            cancelar_item_matricula(item=item, usuario=aluno)
        tipo_anterior = solicitacao.tipo_matricula
        solicitacao.tipo_matricula = SolicitacaoMatricula.TipoMatricula.VINCULO
        solicitacao.tipo_aluno = tipo_aluno
        solicitacao.observacao_aluno = observacao
        solicitacao.status = SolicitacaoMatricula.Status.SOLICITADA
        solicitacao.solicitada_em = solicitacao.solicitada_em or timezone.now()
        solicitacao.save()
        fase = _fase_evento_matricula(periodo)
        if criada:
            _registrar_alteracao_matricula(
                solicitacao=solicitacao,
                acao=AlteracaoMatricula.Acao.SOLICITACAO_CRIADA,
                realizado_por=aluno,
                fase=fase,
            )
        elif tipo_anterior != solicitacao.tipo_matricula:
            _registrar_alteracao_matricula(
                solicitacao=solicitacao,
                acao=AlteracaoMatricula.Acao.TIPO_MATRICULA_ALTERADO,
                realizado_por=aluno,
                fase=fase,
                estado_anterior={"tipo_matricula": tipo_anterior},
                estado_novo={"tipo_matricula": solicitacao.tipo_matricula},
            )
        _registrar_alteracao_matricula(
            solicitacao=solicitacao,
            acao=AlteracaoMatricula.Acao.MATRICULA_VINCULO_SOLICITADA,
            realizado_por=aluno,
            fase=fase,
        )
        return solicitacao

    ofertas = list(
        OfertaDisciplina.objects.select_for_update()
        .select_related("disciplina", "periodo")
        .prefetch_related("encontros")
        .filter(pk__in=[oferta.pk for oferta in ofertas], periodo=periodo)
    )
    if not ofertas:
        raise ValidationError("Selecione ao menos uma disciplina ofertada.")

    solicitacao = solicitacao_existente
    criada = solicitacao is None
    if criada:
        solicitacao = SolicitacaoMatricula.objects.create(
            periodo=periodo,
            aluno=aluno,
            tipo_aluno=tipo_aluno,
            status=SolicitacaoMatricula.Status.RASCUNHO,
        )

    validar_choque_ofertas(
        ofertas,
        aluno=aluno,
        periodo=periodo,
        ignorar_solicitacao=solicitacao if em_modificacao else None,
    )
    if (
        solicitacao.status == SolicitacaoMatricula.Status.CANCELADA
        and periodo.status != periodo.Status.MODIFICACAO_MATRICULA
    ):
        raise ValidationError("A solicitação deste período está cancelada.")

    tipo_anterior = solicitacao.tipo_matricula
    solicitacao.tipo_matricula = SolicitacaoMatricula.TipoMatricula.DISCIPLINAS
    solicitacao.tipo_aluno = tipo_aluno
    solicitacao.observacao_aluno = observacao
    solicitacao.status = SolicitacaoMatricula.Status.SOLICITADA
    solicitacao.solicitada_em = solicitacao.solicitada_em or timezone.now()
    solicitacao.save()

    fase = _fase_evento_matricula(periodo)
    if criada:
        _registrar_alteracao_matricula(
            solicitacao=solicitacao,
            acao=AlteracaoMatricula.Acao.SOLICITACAO_CRIADA,
            realizado_por=aluno,
            fase=fase,
        )
    elif tipo_anterior != solicitacao.tipo_matricula:
        _registrar_alteracao_matricula(
            solicitacao=solicitacao,
            acao=AlteracaoMatricula.Acao.TIPO_MATRICULA_ALTERADO,
            realizado_por=aluno,
            fase=fase,
            estado_anterior={"tipo_matricula": tipo_anterior},
            estado_novo={"tipo_matricula": solicitacao.tipo_matricula},
        )

    if em_modificacao:
        ofertas_selecionadas = {oferta.pk for oferta in ofertas}
        itens_removidos = list(
            solicitacao.itens.select_for_update()
            .filter(status__in=[
                ItemSolicitacaoMatricula.Status.SOLICITADO,
                ItemSolicitacaoMatricula.Status.HOMOLOGADO,
                ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA,
            ])
            .exclude(oferta_id__in=ofertas_selecionadas)
        )
        for item in itens_removidos:
            cancelar_item_matricula(item=item, usuario=aluno)

    for oferta in ofertas:
        item_existente = solicitacao.itens.select_for_update().filter(oferta=oferta).first()
        if item_existente and item_existente.status != ItemSolicitacaoMatricula.Status.CANCELADO:
            continue
        status = ItemSolicitacaoMatricula.Status.SOLICITADO
        if oferta.vagas_disponiveis(tipo_aluno) <= 0:
            if not aceitar_lista_espera:
                raise ValidationError(f"Não há vagas disponíveis em {oferta.disciplina.nome}.")
            status = ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA
        if item_existente:
            estado_anterior = _snapshot_item_matricula(item_existente)
            item_existente.status = status
            item_existente.save()
            item = item_existente
            acao = AlteracaoMatricula.Acao.DISCIPLINA_REINCLUIDA
        else:
            estado_anterior = {}
            item = ItemSolicitacaoMatricula.objects.create(
                solicitacao=solicitacao,
                oferta=oferta,
                status=status,
                incluido_na_fase=(
                    ItemSolicitacaoMatricula.FaseInclusao.MODIFICACAO
                    if fase == AlteracaoMatricula.Fase.MODIFICACAO
                    else ItemSolicitacaoMatricula.FaseInclusao.MATRICULA
                ),
            )
            acao = AlteracaoMatricula.Acao.DISCIPLINA_INCLUIDA
        _registrar_alteracao_matricula(
            solicitacao=solicitacao,
            item=item,
            acao=acao,
            realizado_por=aluno,
            fase=fase,
            estado_anterior=estado_anterior,
        )

    solicitacao.atualizar_status_por_itens()
    return solicitacao


@transaction.atomic
def indeferir_solicitacao_vinculo(*, solicitacao, usuario, motivo=""):
    solicitacao = SolicitacaoMatricula.objects.select_for_update().select_related("periodo").get(pk=solicitacao.pk)
    if solicitacao.tipo_matricula != SolicitacaoMatricula.TipoMatricula.VINCULO:
        raise ValidationError("A solicitação não é de matrícula vínculo.")
    if solicitacao.status == SolicitacaoMatricula.Status.CANCELADA:
        raise ValidationError("Não é possível indeferir solicitação cancelada.")
    status_anterior = solicitacao.status
    solicitacao.status = SolicitacaoMatricula.Status.INDEFERIDA
    solicitacao.observacao_secretaria = motivo
    solicitacao.save()
    _registrar_alteracao_matricula(
        solicitacao=solicitacao,
        acao=AlteracaoMatricula.Acao.MATRICULA_VINCULO_INDEFERIDA,
        realizado_por=usuario,
        fase=_fase_evento_matricula(solicitacao.periodo, administrativo=True),
        estado_anterior={"status": status_anterior},
        estado_novo={"status": solicitacao.status},
        justificativa=motivo,
    )
    return solicitacao


@transaction.atomic
def indeferir_item_matricula(*, item, usuario, motivo=""):
    item = ItemSolicitacaoMatricula.objects.select_for_update().select_related(
        "solicitacao", "solicitacao__periodo", "oferta__disciplina"
    ).get(pk=item.pk)
    if item.status == ItemSolicitacaoMatricula.Status.CANCELADO:
        raise ValidationError("Não é possível indeferir item cancelado.")
    estado_anterior = _snapshot_item_matricula(item)
    item.status = ItemSolicitacaoMatricula.Status.INDEFERIDO
    item.indeferido_em = timezone.now()
    item.indeferido_por = usuario
    item.motivo_indeferimento = motivo
    item.save()
    _registrar_alteracao_matricula(
        solicitacao=item.solicitacao,
        item=item,
        acao=AlteracaoMatricula.Acao.DISCIPLINA_INDEFERIDA,
        realizado_por=usuario,
        fase=_fase_evento_matricula(item.solicitacao.periodo, administrativo=True),
        estado_anterior=estado_anterior,
        justificativa=motivo,
    )
    item.solicitacao.atualizar_status_por_itens()
    return item


@transaction.atomic
def cancelar_item_matricula(*, item, usuario=None):
    item = ItemSolicitacaoMatricula.objects.select_for_update().select_related(
        "solicitacao", "solicitacao__periodo", "solicitacao__aluno", "oferta__disciplina"
    ).get(pk=item.pk)
    if item.status == ItemSolicitacaoMatricula.Status.CANCELADO:
        return item
    estado_anterior = _snapshot_item_matricula(item)
    item.status = ItemSolicitacaoMatricula.Status.CANCELADO
    item.save()
    _registrar_alteracao_matricula(
        solicitacao=item.solicitacao,
        item=item,
        acao=AlteracaoMatricula.Acao.DISCIPLINA_CANCELADA,
        realizado_por=usuario or item.solicitacao.aluno,
        fase=_fase_evento_matricula(item.solicitacao.periodo, administrativo=usuario is not None),
        estado_anterior=estado_anterior,
    )
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
        .select_related("solicitacao", "solicitacao__periodo", "oferta__disciplina")
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
    estado_anterior = _snapshot_item_matricula(proximo)
    proximo.status = ItemSolicitacaoMatricula.Status.SOLICITADO
    proximo.save(update_fields=["status", "atualizado_em"])
    _registrar_alteracao_matricula(
        solicitacao=proximo.solicitacao,
        item=proximo,
        acao=AlteracaoMatricula.Acao.LISTA_ESPERA_PROMOVIDA,
        realizado_por=usuario,
        fase=_fase_evento_matricula(proximo.solicitacao.periodo, administrativo=True),
        estado_anterior=estado_anterior,
    )
    proximo.solicitacao.atualizar_status_por_itens(usuario=usuario)
    return proximo


def _xlsx_coluna(indice):
    letras = ""
    while indice:
        indice, resto = divmod(indice - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def _xlsx_planilha_xml(linhas):
    total_colunas = max((len(linha) for linha in linhas), default=1)
    ultima_coluna = _xlsx_coluna(total_colunas)
    larguras = []
    for indice in range(total_colunas):
        maior = max((len(str(linha[indice])) for linha in linhas if indice < len(linha) and linha[indice] is not None), default=10)
        larguras.append(min(max(maior + 2, 12), 45))
    colunas_xml = "".join(
        f'<col min="{indice}" max="{indice}" width="{largura}" customWidth="1"/>'
        for indice, largura in enumerate(larguras, start=1)
    )
    rows = []
    for row_idx, linha in enumerate(linhas, start=1):
        cells = []
        for col_idx, valor in enumerate(linha, start=1):
            ref = f"{_xlsx_coluna(col_idx)}{row_idx}"
            texto = escape("" if valor is None else str(valor))
            estilo = ' s="1"' if row_idx == 1 else ""
            cells.append(f'<c r="{ref}" t="inlineStr"{estilo}><is><t>{texto}</t></is></c>')
        rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{ultima_coluna}{max(len(linhas), 1)}"/>'
        '<sheetViews><sheetView workbookViewId="0" showGridLines="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>'
        f'<cols>{colunas_xml}</cols>'
        f'<sheetData>{"".join(rows)}</sheetData>'
        f'<autoFilter ref="A1:{ultima_coluna}{max(len(linhas), 1)}"/>'
        "</worksheet>"
    )


def _xlsx_estilos_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Aptos"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )


def gerar_xlsx_lista_oferta(oferta):
    itens = (
        oferta.itens_matricula.select_related("solicitacao", "solicitacao__aluno", "indeferido_por")
        .prefetch_related("solicitacao__aluno__trajetorias")
        .order_by("status", "solicitado_em", "solicitacao__aluno__nome")
    )
    linhas = [[
        "Matrícula",
        "Nome",
        "E-mail",
        "Tipo de aluno",
        "Trajetória acadêmica mais recente",
        "Status",
        "Solicitado em",
        "Indeferido em",
        "Indeferido por",
        "Motivo do indeferimento",
    ]]
    for item in itens:
        trajetoria_recente = next(iter(item.solicitacao.aluno.trajetorias.all()), None)
        linhas.append([
            item.solicitacao.aluno.matricula,
            item.solicitacao.aluno.nome,
            item.solicitacao.aluno.email,
            item.solicitacao.get_tipo_aluno_display(),
            trajetoria_recente.get_nivel_curso_display() if trajetoria_recente else "",
            item.get_status_display(),
            timezone.localtime(item.solicitado_em).strftime("%d/%m/%Y %H:%M") if item.solicitado_em else "",
            timezone.localtime(item.indeferido_em).strftime("%d/%m/%Y %H:%M") if item.indeferido_em else "",
            item.indeferido_por.nome if item.indeferido_por else "",
            item.motivo_indeferimento,
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
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
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
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ))
        xlsx.writestr("xl/worksheets/sheet1.xml", _xlsx_planilha_xml(linhas))
        xlsx.writestr("xl/styles.xml", _xlsx_estilos_xml())
    return buffer.getvalue()


def _gerar_xlsx_multiplas_planilhas(planilhas):
    """Gera um XLSX simples, com uma planilha para cada par (nome, linhas)."""
    nomes_usados = set()
    planilhas_normalizadas = []
    for indice, (nome, linhas) in enumerate(planilhas, start=1):
        nome_limpo = "".join("-" if char in "[]:*?/\\" else char for char in nome).strip()[:31] or f"Planilha {indice}"
        nome_base = nome_limpo
        sufixo = 2
        while nome_limpo.casefold() in nomes_usados:
            complemento = f" ({sufixo})"
            nome_limpo = f"{nome_base[:31-len(complemento)]}{complemento}"
            sufixo += 1
        nomes_usados.add(nome_limpo.casefold())
        planilhas_normalizadas.append((nome_limpo, linhas))

    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as xlsx:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(planilhas_normalizadas) + 1)
        )
        xlsx.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f'{overrides}</Types>'
        ))
        xlsx.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'
        ))
        sheets = "".join(
            f'<sheet name="{escape(nome)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, (nome, _linhas) in enumerate(planilhas_normalizadas, start=1)
        )
        xlsx.writestr("xl/workbook.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{sheets}</sheets></workbook>'
        ))
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(planilhas_normalizadas) + 1)
        )
        rels += (
            f'<Relationship Id="rId{len(planilhas_normalizadas) + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
        )
        xlsx.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{rels}</Relationships>'
        ))
        for i, (_nome, linhas) in enumerate(planilhas_normalizadas, start=1):
            xlsx.writestr(f"xl/worksheets/sheet{i}.xml", _xlsx_planilha_xml(linhas))
        xlsx.writestr("xl/styles.xml", _xlsx_estilos_xml())
    return buffer.getvalue()


def _nivel_aluno_matricula(solicitacao):
    trajetoria = next(
        (
            trajetoria
            for trajetoria in solicitacao.aluno.trajetorias.all()
            if trajetoria.status == TrajetoriaAcademica.Status.ATIVA
        ),
        None,
    )
    if trajetoria:
        return trajetoria.get_nivel_curso_display()
    if solicitacao.tipo_aluno == SolicitacaoMatricula.TipoAluno.ESPECIAL:
        return Aluno.NivelCurso.ALUNO_ESPECIAL.label
    return ""


def gerar_xlsx_solicitacoes_periodo(periodo, estado="consolidada"):
    if estado not in {"originais", "modificacoes", "consolidada"}:
        raise ValidationError("Tipo de planilha de matrícula inválido.")

    cabecalho = ["Matrícula", "Nome", "Polo", "E-mail", "Tipo de aluno", "Nível", "Status", "Solicitado em", "Observação"]
    if estado == "modificacoes":
        linhas = [[
            "Data e hora",
            "Matrícula",
            "Nome",
            "Nível",
            "Disciplina",
            "Ação",
            "Estado anterior",
            "Estado novo",
            "Realizado por",
            "Justificativa",
        ]]
        alteracoes = (
            AlteracaoMatricula.objects.filter(
                solicitacao__periodo=periodo,
                fase=AlteracaoMatricula.Fase.MODIFICACAO,
            )
            .select_related("solicitacao__aluno", "oferta__disciplina", "realizado_por")
            .prefetch_related("solicitacao__aluno__trajetorias")
            .order_by("criado_em", "id")
        )
        for alteracao in alteracoes:
            anterior = alteracao.estado_anterior.get("status", "")
            novo = alteracao.estado_novo.get("status", "")
            linhas.append([
                timezone.localtime(alteracao.criado_em).strftime("%d/%m/%Y %H:%M"),
                alteracao.solicitacao.aluno.matricula,
                alteracao.solicitacao.aluno.nome,
                _nivel_aluno_matricula(alteracao.solicitacao),
                (
                    f"{alteracao.oferta.disciplina.codigo} - {alteracao.oferta.disciplina.nome}"
                    if alteracao.oferta_id else "Matrícula vínculo"
                ),
                alteracao.get_acao_display(),
                dict(ItemSolicitacaoMatricula.Status.choices).get(anterior, anterior),
                dict(ItemSolicitacaoMatricula.Status.choices).get(novo, novo),
                alteracao.realizado_por.nome,
                alteracao.justificativa,
            ])
        return _gerar_xlsx_multiplas_planilhas([("Modificações", linhas)])

    planilhas = []
    disciplinas = (
        OfertaDisciplina.objects.filter(periodo=periodo, itens_matricula__isnull=False)
        .select_related("disciplina")
        .order_by("disciplina__codigo", "disciplina__nome")
        .values_list("disciplina_id", "disciplina__codigo", "disciplina__nome")
        .distinct()
    )
    for disciplina_id, codigo, nome in disciplinas:
        linhas = [cabecalho]
        itens = (
            ItemSolicitacaoMatricula.objects.filter(
                oferta__periodo=periodo,
                oferta__disciplina_id=disciplina_id,
            )
            .select_related("solicitacao", "solicitacao__aluno", "solicitacao__aluno__polo_atuacao")
            .prefetch_related("solicitacao__aluno__trajetorias")
            .order_by("solicitacao__aluno__nome")
        )
        if estado == "originais":
            itens = itens.filter(incluido_na_fase=ItemSolicitacaoMatricula.FaseInclusao.MATRICULA)
        else:
            itens = itens.filter(status__in=[
                ItemSolicitacaoMatricula.Status.SOLICITADO,
                ItemSolicitacaoMatricula.Status.HOMOLOGADO,
            ])
        for item in itens:
            solicitacao = item.solicitacao
            status = item.get_status_display()
            if estado == "originais":
                evento_inicial = item.alteracoes.filter(
                    acao=AlteracaoMatricula.Acao.DISCIPLINA_INCLUIDA,
                    fase=AlteracaoMatricula.Fase.MATRICULA,
                ).first()
                status_inicial = evento_inicial.estado_novo.get("status") if evento_inicial else ""
                status = dict(ItemSolicitacaoMatricula.Status.choices).get(
                    status_inicial,
                    dict(ItemSolicitacaoMatricula.Status.choices)[ItemSolicitacaoMatricula.Status.SOLICITADO],
                )
            linhas.append([
                solicitacao.aluno.matricula,
                solicitacao.aluno.nome,
                solicitacao.aluno.polo_atuacao.nome if solicitacao.aluno.polo_atuacao else "",
                solicitacao.aluno.email,
                solicitacao.get_tipo_aluno_display(),
                _nivel_aluno_matricula(solicitacao),
                status,
                timezone.localtime(item.solicitado_em).strftime("%d/%m/%Y %H:%M") if item.solicitado_em else "",
                solicitacao.observacao_aluno,
            ])
        planilhas.append((f"{codigo} {nome}".strip(), linhas))

    vinculos = SolicitacaoMatricula.objects.filter(
        periodo=periodo,
        tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
    ).select_related("aluno", "aluno__polo_atuacao").prefetch_related("aluno__trajetorias").order_by("aluno__nome")
    if estado == "originais":
        vinculos = vinculos.filter(
            Q(
                alteracoes__acao=AlteracaoMatricula.Acao.MATRICULA_VINCULO_SOLICITADA,
                alteracoes__fase=AlteracaoMatricula.Fase.MATRICULA,
            )
            | Q(solicitada_em__date__lt=periodo.modificacao_inicio)
        ).distinct()
    else:
        vinculos = vinculos.exclude(status__in=[
            SolicitacaoMatricula.Status.CANCELADA,
            SolicitacaoMatricula.Status.INDEFERIDA,
        ])
    linhas_vinculo = [cabecalho]
    for solicitacao in vinculos:
        linhas_vinculo.append([
            solicitacao.aluno.matricula,
            solicitacao.aluno.nome,
            solicitacao.aluno.polo_atuacao.nome if solicitacao.aluno.polo_atuacao else "",
            solicitacao.aluno.email,
            solicitacao.get_tipo_aluno_display(),
            _nivel_aluno_matricula(solicitacao),
            solicitacao.get_status_display(),
            timezone.localtime(solicitacao.solicitada_em).strftime("%d/%m/%Y %H:%M") if solicitacao.solicitada_em else "",
            solicitacao.observacao_aluno,
        ])
    planilhas.append(("Matrícula vínculo", linhas_vinculo))
    return _gerar_xlsx_multiplas_planilhas(planilhas)
