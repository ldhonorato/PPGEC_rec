import re
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.conf import settings
from django.core.files.storage import default_storage
from django.http import Http404, HttpResponse, JsonResponse
from django.views.static import serve
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time

from .forms import (
    AlunoCadastroForm,
    ImportacaoIngressantesForm,
    AlunoComentarioForm,
    AlunoCpfForm,
    AlunoDadosForm,
    AlunoDefesaForm,
    AlunoDepositoFinalForm,
    AlunoIniciarDoutoradoForm,
    AlunoPrazoForm,
    AlunoQualificacaoForm,
    AlunoStatusForm,
    EstagioDocenciaUpdateForm,
    NovoEstagioDocenciaForm,
    AtenderSolicitacaoAssinaturaForm,
    ManifestarCienteOrientadorForm,
    ComentarioProcessoForm,
    DisciplinaForm,
    DisciplinaTrajetoriaForm,
    DisponibilidadeSalaLoteForm,
    DocumentoCadastroForm,
    EncaminhamentoForm,
    FinalizarProcessoForm,
    HorasComplementaresAdministrativoForm,
    LancamentoHorasComplementaresForm,
    OfertaDisciplinaForm,
    PeriodoLetivoForm,
    ProcessoAberturaForm,
    PublicacaoTrajetoriaForm,
    ReservaAmbienteExclusaoForm,
    ReservaAmbienteForm,
    SalaForm,
    SolicitacaoMatriculaForm,
    SolicitacaoAssinaturaForm,
    SolicitacaoBancaForm,
    SolicitarCienteOrientadorForm,
    SetorComissaoForm,
    TrajetoriaAcademicaForm,
    TrajetoriaStatusForm,
    UserProfileForm,
)
from .importacao_ingressantes import importar_ingressantes
from .models import (
    AlteracaoAluno,
    Aluno,
    Disciplina,
    DisciplinaTrajetoria,
    DisponibilidadeSala,
    ComentarioProcesso,
    Docente,
    Documento,
    EncontroOferta,
    ItemSolicitacaoMatricula,
    EstagioDocencia,
    LancamentoHorasComplementares,
    ManifestacaoProcesso,
    MembroBanca,
    OfertaDisciplina,
    PeriodoLetivo,
    Polo,
    Processo,
    PublicacaoTrajetoria,
    ReservaAmbiente,
    Sala,
    Setor,
    SetorMembro,
    SolicitacaoMatricula,
    SolicitacaoAssinatura,
    SolicitacaoBanca,
    TrajetoriaAcademica,
    TramitacaoProcesso,
    User,
)

from .tasks import (
    send_email_novo_processo_aluno,
    send_email_novo_processo_orientador,
    send_email_solicitacao_ciencia,
    send_email_devolucao_requerente,
    send_email_movimentacao_aluno,
    send_email_movimentacao_orientador,
    send_email_conclusao_aluno,
    send_email_conclusao_orientador,
    send_email_movimentacao_pleno,
    send_email_processo_comentado_pleno,
    send_email_novo_processo_secretaria,
    send_email_mudanca_setor,
    send_email_status_atualizado,
    send_email_solicitacao_assinatura,
    send_email_alunos_sem_matricula,
    send_email_secretaria_planejamento_presencial,
)
from .services import (
    alunos_ativos_sem_matricula,
    cancelar_item_matricula,
    carga_horaria_presencial_oferta_minutos,
    carga_horaria_total_oferta_minutos,
    gerar_xlsx_lista_oferta,
    gerar_xlsx_solicitacoes_periodo,
    indeferir_item_matricula,
    indeferir_solicitacao_vinculo,
    datas_encontro_no_periodo,
    oferta_hibrida_conforme,
    ofertas_hibridas_nao_conformes,
    percentual_presencial_oferta,
    salvar_planejamento_presencial_oferta,
    salvar_solicitacao_matricula,
    tipo_aluno_matricula_por_trajetoria,
)


def _parse_date_input(value):
    if not value:
        return None
    data = parse_date(value)
    if data:
        return data
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None


ITENS_POR_PAGINA = 25


def _paginar(request, queryset, por_pagina=None):
    """Recorta a listagem na pagina pedida em ?page=.

    Devolve o objeto Page, que serve tanto para iterar as linhas quanto para o
    include includes/paginacao.html montar a navegacao.

    Pagina invalida (fora do intervalo ou nao numerica) cai na primeira em vez
    de levantar erro: o parametro vem da URL e pode chegar editado a mao ou
    apontando para uma pagina que sumiu depois de um filtro.
    """
    # A constante e lida aqui dentro, e nao como valor padrao do argumento:
    # valor padrao e resolvido na definicao da funcao, o que congelaria o
    # numero e impediria qualquer sobrescrita depois -- inclusive nos testes.
    paginador = Paginator(queryset, por_pagina or ITENS_POR_PAGINA)
    try:
        return paginador.page(int(request.GET.get("page", 1)))
    except (ValueError, TypeError, EmptyPage, PageNotAnInteger):
        return paginador.page(1)


def _is_docente(user):
    return user.is_authenticated and user.tipo_usuario == User.TipoUsuario.DOCENTE


def _e_orientador_do_aluno(user, aluno_id):
    """Diz se o docente orienta ou coorienta o aluno informado.

    O vinculo mora em TrajetoriaAcademica, nao no Aluno -- consultar
    Aluno.orientador nao funciona, o campo nao existe mais.

    Considera qualquer trajetoria, nao so a ativa: o orientador continua
    respondendo pelo historico de quem ja concluiu, e a tela "Meus
    Orientandos" tambem lista as orientacoes encerradas.
    """
    if not _is_docente(user):
        return False
    return TrajetoriaAcademica.objects.filter(
        Q(orientador=user) | Q(coorientador=user),
        aluno_id=aluno_id,
    ).exists()


def _is_servidor(user):
    return user.is_authenticated and user.tipo_usuario == User.TipoUsuario.SERVIDOR


def _is_secretaria_member(user):
    return user.is_authenticated and SetorMembro.objects.filter(
        usuario=user,
        data_saida__isnull=True,
        setor__ativo=True,
        setor__nome="Secretaria PPGEC",
    ).exists()


def _is_coordenador(user):
    if not user.is_authenticated or user.tipo_usuario != User.TipoUsuario.DOCENTE:
        return False
    try:
        return bool(user.docente.coordenador)
    except Docente.DoesNotExist:
        return False


def _has_gestao_access(user):
    return _is_coordenador(user) or _is_servidor(user) or _is_secretaria_member(user)


def _can_view_dashboard(user):
    return _has_gestao_access(user)


def _can_view_processos(user):
    return _has_gestao_access(user)


def _can_add_processo(user):
    if not user.is_authenticated or _is_servidor(user):
        return False
    if user.tipo_usuario == User.TipoUsuario.ALUNO:
        return not Aluno.objects.filter(
            pk=user.pk,
            status_aluno=Aluno.StatusAluno.EM_AVALIACAO,
        ).exists()
    return True


def _can_view_processo_detalhe(user, processo):
    if not user.is_authenticated:
        return False
    if processo.usuario_criado_por_id == user.id:
        return True
    if user.tipo_usuario == User.TipoUsuario.ALUNO:
        return False
    if _can_view_processos(user):
        return True
    if processo.setor_atual_id in {setor.id for setor in _setores_caixa(user)}:
        return True
    if _is_docente(user):
        if _is_processo_no_pleno(processo) and _is_membro_setor_nome(user, Setor.NOME_PLENO):
            return True
        return Aluno.objects.filter(
            Q(trajetorias__orientador=user) | Q(trajetorias__coorientador=user),
            pk=processo.usuario_criado_por_id,
        ).exists()
    return False


def _is_requerente_do_processo(user, processo):
    return user.is_authenticated and processo.usuario_criado_por_id == user.id


def _setores_membro_queryset(user):
    if not user.is_authenticated:
        return Setor.objects.none()
    return Setor.objects.filter(membros__usuario=user, membros__data_saida__isnull=True, ativo=True).distinct()


def _is_membro_setor_nome(user, nome):
    return _setores_membro_queryset(user).filter(nome=nome).exists()


def _setores_caixa(user):
    setores = []
    if _is_servidor(user):
        setores.extend(Setor.objects.filter(nome="Secretaria PPGEC", ativo=True))
    if _is_coordenador(user):
        setores.extend(Setor.objects.filter(nome="Coordenação PPG", ativo=True))
    setores.extend(_setores_membro_queryset(user))

    unique = {}
    for setor in setores:
        unique[setor.id] = setor
    return list(unique.values())


def _can_view_caixa(user):
    return bool(_setores_caixa(user))


def _assinaturas_destinadas_queryset(user):
    if not user.is_authenticated:
        return SolicitacaoAssinatura.objects.none()
    setores_ids = _setores_membro_queryset(user).values_list("id", flat=True)
    return SolicitacaoAssinatura.objects.filter(
        Q(docente=user) | Q(setor_id__in=setores_ids)
    )


def _can_view_assinaturas(user):
    return (
        _has_gestao_access(user)
        or _is_docente(user)
        or _assinaturas_destinadas_queryset(user).exists()
        or SolicitacaoAssinatura.objects.filter(criado_por=user).exists()
    )


def _assinaturas_pendentes_queryset(user):
    return _assinaturas_destinadas_queryset(user).filter(
        status=SolicitacaoAssinatura.Status.PENDENTE
    ).select_related("criado_por", "docente", "setor").order_by("-criado_em")


def _can_view_solicitacao_assinatura(user, solicitacao):
    if not user.is_authenticated:
        return False
    if _has_gestao_access(user) or solicitacao.criado_por_id == user.id:
        return True
    if solicitacao.docente_id == user.id:
        return True
    if solicitacao.setor_id:
        return _setores_membro_queryset(user).filter(id=solicitacao.setor_id).exists()
    return False


def _can_atender_solicitacao_assinatura(user, solicitacao):
    if not user.is_authenticated or not solicitacao.is_pendente:
        return False
    if solicitacao.docente_id == user.id:
        return True
    if solicitacao.setor_id:
        return _setores_membro_queryset(user).filter(id=solicitacao.setor_id).exists()
    return False


def _can_manage_restricted_docs(user):
    return _has_gestao_access(user)


def _can_manage_matriculas(user):
    return _has_gestao_access(user)


def _can_create_oferta(user):
    return _can_manage_matriculas(user) or _is_docente(user)


def _montar_horarios_semanais_ofertas(ofertas):
    dias = [
        (EncontroOferta.DiaSemana.SEGUNDA, "Segunda"),
        (EncontroOferta.DiaSemana.TERCA, "Terça"),
        (EncontroOferta.DiaSemana.QUARTA, "Quarta"),
        (EncontroOferta.DiaSemana.QUINTA, "Quinta"),
        (EncontroOferta.DiaSemana.SEXTA, "Sexta"),
        (EncontroOferta.DiaSemana.SABADO, "Sábado"),
    ]
    dias_validos = {dia_value for dia_value, _ in dias}
    periodos = {}
    for oferta in ofertas:
        periodo_id = oferta.periodo_id
        if periodo_id not in periodos:
            periodos[periodo_id] = {
                "periodo": oferta.periodo,
                "eventos_por_dia": {dia_value: [] for dia_value, _ in dias},
            }
        for encontro in oferta.encontros.all():
            if encontro.dia_semana not in dias_validos:
                continue
            periodos[periodo_id]["eventos_por_dia"][encontro.dia_semana].append(
                {"oferta": oferta, "encontro": encontro}
            )

    horarios_semanais = []
    for dados in periodos.values():
        todos_eventos = [
            evento
            for eventos in dados["eventos_por_dia"].values()
            for evento in eventos
        ]
        if not todos_eventos:
            horarios_semanais.append(
                {"periodo": dados["periodo"], "dias": [], "marcas_hora": [], "altura_horario": 0}
            )
            continue

        inicio_minutos = min(evento["encontro"].hora_inicio.hour * 60 + evento["encontro"].hora_inicio.minute for evento in todos_eventos)
        fim_minutos = max(evento["encontro"].hora_fim.hour * 60 + evento["encontro"].hora_fim.minute for evento in todos_eventos)
        inicio_horario = (inicio_minutos // 60) * 60
        fim_horario = ((fim_minutos + 59) // 60) * 60
        total_minutos = max(fim_horario - inicio_horario, 60)
        pixels_por_minuto = 1.15
        altura_horario = max(int(total_minutos * pixels_por_minuto), 160)
        # Altura de uma faixa de uma hora, em px, para o CSS desenhar as linhas
        # divisorias. Vinha fixa em 69px no CSS (60 x 1.15), o que so casava com
        # as marcas -- que sao posicionadas em % -- enquanto o piso de 160px nao
        # entrasse. Num horario de uma hora so, a faixa mede 160px e as linhas
        # continuavam a cada 69px, cruzando a grade fora das horas cheias.
        altura_hora = altura_horario / (total_minutos / 60)

        marcas_hora = []
        hora_atual = inicio_horario
        while hora_atual <= fim_horario:
            topo = ((hora_atual - inicio_horario) / total_minutos) * 100
            marcas_hora.append(
                {
                    "label": f"{hora_atual // 60:02d}:00",
                    "top": f"{topo:.3f}%",
                    # A marca e centrada na linha da hora. Na ultima, que fica em
                    # 100%, metade do texto caia fora da grade e era cortada pelo
                    # arredondamento da borda -- o horario final aparecia pela
                    # metade. Nela o rotulo sobe inteiro para dentro.
                    "no_fim": hora_atual == fim_horario,
                }
            )
            hora_atual += 60

        dias_render = []
        for dia_value, dia_label in dias:
            eventos = sorted(
                dados["eventos_por_dia"][dia_value],
                key=lambda evento: (evento["encontro"].hora_inicio, evento["encontro"].hora_fim),
            )
            ativos = []
            eventos_render = []
            for evento in eventos:
                encontro = evento["encontro"]
                evento_inicio = encontro.hora_inicio.hour * 60 + encontro.hora_inicio.minute
                evento_fim = encontro.hora_fim.hour * 60 + encontro.hora_fim.minute
                ativos = [ativo for ativo in ativos if ativo["fim"] > evento_inicio]
                colunas_ocupadas = {ativo["coluna"] for ativo in ativos}
                coluna = 0
                while coluna in colunas_ocupadas:
                    coluna += 1
                ativos.append({"fim": evento_fim, "coluna": coluna})
                total_colunas = max([ativo["coluna"] for ativo in ativos] + [coluna]) + 1
                for evento_render in eventos_render:
                    if evento_render["fim_minutos"] > evento_inicio:
                        evento_render["total_colunas"] = max(evento_render["total_colunas"], total_colunas)

                eventos_render.append(
                    {
                        "oferta": evento["oferta"],
                        "encontro": encontro,
                        "fim_minutos": evento_fim,
                        "top": f"{((evento_inicio - inicio_horario) / total_minutos) * 100:.3f}%",
                        "height": f"{max(((evento_fim - evento_inicio) / total_minutos) * 100, 8):.3f}%",
                        "coluna": coluna,
                        "total_colunas": total_colunas,
                    }
                )

            for evento_render in eventos_render:
                largura = 100 / evento_render["total_colunas"]
                evento_render["left"] = f"{evento_render['coluna'] * largura:.3f}%"
                evento_render["width"] = f"calc({largura:.3f}% - 0.25rem)"

            dias_render.append({"label": dia_label, "eventos": eventos_render})

        horarios_semanais.append(
            {
                "periodo": dados["periodo"],
                "dias": dias_render,
                "marcas_hora": marcas_hora,
                "altura_horario": altura_horario,
                "altura_hora": f"{altura_hora:.2f}",
            }
        )
    return sorted(horarios_semanais, key=lambda item: item["periodo"].nome, reverse=True), dias


def _nomes_setores_caixa(user):
    return [setor.nome for setor in _setores_caixa(user)]


def _is_setor_pleno_nome(nome: str) -> bool:
    return "pleno" in (nome or "").lower()


def _semestre_valido(valor: str) -> bool:
    return bool(re.fullmatch(r"\d{4}\.[12]", (valor or "").strip()))


def _trajetoria_form_initial(trajetoria):
    if trajetoria.coorientador_id:
        tipo_coorientador = TrajetoriaAcademicaForm.TipoCoorientador.CADASTRADO
    elif trajetoria.coorientador_externo_nome:
        tipo_coorientador = TrajetoriaAcademicaForm.TipoCoorientador.EXTERNO
    else:
        tipo_coorientador = TrajetoriaAcademicaForm.TipoCoorientador.NENHUM

    return {
        "trajetoria_id": trajetoria.id,
        "nivel_curso": trajetoria.nivel_curso,
        "status": trajetoria.status,
        "ingresso": trajetoria.ingresso,
        "prazo_qualificacao": trajetoria.prazo_qualificacao,
        "prazo_defesa": trajetoria.prazo_defesa,
        "reingressante": trajetoria.reingressante,
        "isQualificado": trajetoria.isQualificado,
        "orientador": trajetoria.orientador_id,
        "tipo_coorientador": tipo_coorientador,
        "coorientador": trajetoria.coorientador_id,
        "coorientador_externo_nome": trajetoria.coorientador_externo_nome,
        "coorientador_externo_email": trajetoria.coorientador_externo_email,
        "coorientador_externo_instituicao": trajetoria.coorientador_externo_instituicao,
        "numero_defesa": trajetoria.numero_defesa,
        "data_defesa": trajetoria.data_defesa,
        "deposito_versao_final": trajetoria.deposito_versao_final,
    }


def _registrar_alteracao_aluno(
    *,
    aluno: Aluno,
    tipo: str,
    valor_anterior: str,
    valor_novo: str,
    comentario: str,
    alterado_por: User,
):
    AlteracaoAluno.objects.create(
        aluno=aluno,
        tipo=tipo,
        valor_anterior=valor_anterior,
        valor_novo=valor_novo,
        comentario=comentario.strip(),
        alterado_por=alterado_por,
    )


def _registrar_alteracao_trajetoria(
    trajetoria,
    tipo: str,
    valor_anterior: str,
    valor_novo: str,
    comentario: str,
    alterado_por: User,
):
    _registrar_alteracao_aluno(
        aluno=trajetoria.aluno,
        tipo=tipo,
        valor_anterior=valor_anterior,
        valor_novo=valor_novo,
        comentario=comentario,
        alterado_por=alterado_por,
    )


def _trajetoria_label(trajetoria: TrajetoriaAcademica | None) -> str:
    if not trajetoria:
        return "-"
    qualificacao = "Sim" if trajetoria.isQualificado else "Nao"
    return (
        f"{trajetoria.get_nivel_curso_display()};"
        f"status={trajetoria.get_status_display()};"
        f"ingresso={trajetoria.ingresso or '-'};"
        f"{trajetoria.qualificacao_label_lower}={trajetoria.prazo_qualificacao or '-'};"
        f"{trajetoria.qualificacao_label}={qualificacao};"
        f"defesa={trajetoria.prazo_defesa or '-'};"
        f"orientador={_docente_label(trajetoria.orientador)};"
        f"coorientador={_coorientador_label(trajetoria)};"
        f"reingressante={'Sim' if trajetoria.reingressante else 'Nao'}"
    )


def _trajetoria_campo_label(trajetoria: TrajetoriaAcademica, campo: str, valor: str) -> str:
    return (
        f"{trajetoria.get_nivel_curso_display()};"
        f"ingresso={trajetoria.ingresso or '-'};"
        f"{campo}={valor or '-'}"
    )


def _defesa_display(trajetoria: TrajetoriaAcademica) -> str:
    data = trajetoria.data_defesa.isoformat() if trajetoria.data_defesa else "-"
    return f"{trajetoria.numero_defesa or '-'} - {data}"


def _estagio_docencia_label(estagio: EstagioDocencia | None) -> str:
    if not estagio:
        return "-"
    inicio = estagio.inicio.isoformat() if estagio.inicio else "-"
    termino = estagio.termino.isoformat() if estagio.termino else "-"
    return (
        f"supervisor={estagio.supervisor or '-'};"
        f"status={estagio.get_status_display()};"
        f"inicio={inicio};"
        f"termino={termino}"
    )


def _trajetoria_campo_historico(trajetoria: TrajetoriaAcademica, campo: str) -> tuple[str, str]:
    if campo == "status":
        return "Status", trajetoria.get_status_display()
    if campo == "nivel_curso":
        return "Nivel", trajetoria.get_nivel_curso_display()
    if campo == "prazo_qualificacao":
        return f"Prazo {trajetoria.qualificacao_label_lower}", trajetoria.prazo_qualificacao or "-"
    if campo == "prazo_defesa":
        return "Prazo defesa", trajetoria.prazo_defesa or "-"
    if campo == "reingressante":
        return "Reingressante", _bool_label(trajetoria.reingressante)
    if campo == "isQualificado":
        return trajetoria.qualificacao_label, _bool_label(trajetoria.isQualificado)
    if campo == "orientador":
        rotulo = "Supervisor" if trajetoria.usa_supervisao else "Orientador"
        return rotulo, _docente_label(trajetoria.orientador)
    if campo == "coorientador":
        return "Coorientador", _coorientador_label(trajetoria)
    if campo == "defesa":
        return "Defesa", _defesa_display(trajetoria)
    if campo == "deposito_versao_final":
        return "Depósito final", _bool_label(trajetoria.deposito_versao_final)
    return "Alteracao", "-"


def _dados_aluno_label(aluno: Aluno) -> str:
    return f"nome={aluno.nome or '-'};email={aluno.email or '-'};matricula={aluno.matricula or '-'}"


def _parse_label_fields(valor: str) -> dict:
    campos = {}
    for index, parte in enumerate((valor or "").split(";")):
        parte = parte.strip()
        if not parte:
            continue
        if "=" in parte:
            chave, conteudo = parte.split("=", 1)
            campos[chave.strip()] = conteudo.strip()
        elif index == 0:
            campos["nivel"] = parte
    return campos


def _campo_alteracao_label(campo: str) -> str:
    labels = {
        "nivel": "Nivel",
        "Nivel": "Nivel",
        "status": "Status",
        "Status": "Status",
        "ingresso": "Ingresso",
        "Ingresso": "Ingresso",
        "defesa": "Defesa",
        "Defesa": "Defesa",
        "orientador": "Orientador",
        "Orientador": "Orientador",
        "coorientador": "Coorientador",
        "Coorientador": "Coorientador",
        "reingressante": "Reingressante",
        "Reingressante": "Reingressante",
        "nome": "Nome",
        "email": "Email",
        "matricula": "Matricula",
        "Depósito final": "Depósito final",
    }
    if campo.lower().startswith("prazo "):
        return "Prazo de qualificação/projeto"
    return labels.get(campo, campo.replace("_", " ").capitalize())


def _alteracao_aluno_display(alteracao: AlteracaoAluno) -> dict:
    anterior = _parse_label_fields(alteracao.valor_anterior)
    novo = _parse_label_fields(alteracao.valor_novo)
    nivel = novo.get("nivel") or anterior.get("nivel")
    ingresso = novo.get("ingresso") or anterior.get("ingresso")

    if nivel and ingresso:
        trajetoria = f"{nivel} - Ingresso {ingresso}"
    elif nivel:
        trajetoria = nivel
    else:
        trajetoria = "Dados do aluno"

    alteracoes = []
    for campo in sorted(set(anterior) | set(novo)):
        valor_anterior = anterior.get(campo, "-") or "-"
        valor_novo = novo.get(campo, "-") or "-"
        if valor_anterior != valor_novo:
            alteracoes.append((_campo_alteracao_label(campo), valor_anterior, valor_novo))

    if len(alteracoes) == 1:
        campo, _valor_anterior, valor_novo = alteracoes[0]
        texto_alteracao = f"Alteração no {campo} ({valor_novo})"
    elif alteracoes:
        texto_alteracao = "; ".join(
            f"{campo}: {valor_anterior} -> {valor_novo}"
            for campo, valor_anterior, valor_novo in alteracoes
        )
    else:
        texto_alteracao = "Alteração registrada"

    return {
        "obj": alteracao,
        "trajetoria": trajetoria,
        "alteracao": texto_alteracao,
    }


def _bool_label(valor: bool) -> str:
    return "Sim" if valor else "Nao"


def _trajetoria_ativa(aluno: Aluno) -> TrajetoriaAcademica:
    return aluno.trajetoria_ativa()


def _trajetoria_referencia_listagem(aluno: Aluno) -> TrajetoriaAcademica:
    trajetorias = list(aluno.trajetorias.all())
    for trajetoria in trajetorias:
        if trajetoria.status == TrajetoriaAcademica.Status.ATIVA:
            return trajetoria
    for trajetoria in trajetorias:
        if trajetoria.status == TrajetoriaAcademica.Status.CONCLUIDA:
            return trajetoria
    return None


def _status_trajetoria_listagem(status: str) -> str:
    status_map = {
        Aluno.StatusAluno.ATIVO: TrajetoriaAcademica.Status.ATIVA,
        Aluno.StatusAluno.DEFENDEU: TrajetoriaAcademica.Status.CONCLUIDA,
        Aluno.StatusAluno.DESLIGADO: TrajetoriaAcademica.Status.DESLIGADA,
        "ATIVA": TrajetoriaAcademica.Status.ATIVA,
        "CONCLUIDA": TrajetoriaAcademica.Status.CONCLUIDA,
        "DESLIGADA": TrajetoriaAcademica.Status.DESLIGADA,
        "TRANCADA": TrajetoriaAcademica.Status.TRANCADA,
    }
    return status_map.get(status, status)


def _status_trajetoria_display(trajetoria: TrajetoriaAcademica) -> str:
    status_map = {
        TrajetoriaAcademica.Status.ATIVA: "Ativo",
        TrajetoriaAcademica.Status.CONCLUIDA: "Concluido",
        TrajetoriaAcademica.Status.DESLIGADA: "Desligado",
        TrajetoriaAcademica.Status.TRANCADA: "Trancado",
    }
    return status_map.get(trajetoria.status, trajetoria.get_status_display())


def _sincronizar_trajetoria_ativa(aluno: Aluno) -> TrajetoriaAcademica:
    trajetoria = _trajetoria_ativa(aluno)
    if not trajetoria:
        return None
    if aluno.status_aluno == Aluno.StatusAluno.DEFENDEU:
        trajetoria.status = TrajetoriaAcademica.Status.CONCLUIDA
    elif aluno.status_aluno == Aluno.StatusAluno.DESLIGADO:
        trajetoria.status = TrajetoriaAcademica.Status.DESLIGADA
    else:
        trajetoria.status = TrajetoriaAcademica.Status.ATIVA
    trajetoria.save()
    return trajetoria


def _is_processo_no_pleno(processo: Processo) -> bool:
    return _is_setor_pleno_nome(processo.setor_atual.nome)


def _can_manage_caixa_actions(user, processo: Processo) -> bool:
    if processo.setor_atual_id in {setor.id for setor in _setores_caixa(user)}:
        return True
    if _is_servidor(user):
        return processo.setor_atual.nome == "Secretaria PPGEC"
    if _is_coordenador(user):
        return processo.setor_atual.nome == "Coordenação PPG"
    return False


def _menu_lateral_home(user):
    if user.tipo_usuario == User.TipoUsuario.DOCENTE:
        items = [
            {"label": "Meus Processos", "href": "/menu/meus-processos/"},
            {"label": "Processos dos orientandos", "href": "/menu/processos-orientandos/"},
            {"label": "Ciências manifestadas", "href": "/menu/ciencias-manifestadas/"},
            {"label": "Meus Orientandos", "href": "/menu/meus-orientandos/"},
        ]
        if _is_membro_setor_nome(user, Setor.NOME_PLENO):
            items.insert(1, {"label": "Processos no Pleno", "href": "/menu/processos-pleno/"})
        return items
    if user.tipo_usuario == User.TipoUsuario.ALUNO:
        items = [
            {"label": "Documento de vínculo (TODO)", "href": "/aluno/documento-vinculo/"},
            {"label": "Documento de histórico", "href": "/aluno/documento-historico/"},
            {"label": "Meus Processos", "href": "/menu/meus-processos/"},
        ]
        if _can_add_processo(user):
            items.append({"label": "Novo processo", "href": "/processos/novo/"})
        return items
    return []


from django.core.mail import send_mail


def teste_email(request):
    send_mail(
        subject="✅ Teste de envio - AcadFlow PPGEC",
        message="""
Olá!

Este é um e-mail de teste enviado pelo sistema AcadFlow PPGEC.

O objetivo deste disparo é validar:

• A identidade visual e funcionamento do envio de e-mails;
• A entrega correta nos provedores Gmail e Outlook;
• A verificação de possíveis marcações como Spam.

Se você recebeu esta mensagem corretamente, o sistema está funcionando normalmente.

Atenciosamente,
Equipe AcadFlow - PPGEC
        """,
        from_email="EMAIL@GMAIL.COM",
        recipient_list=["EMAIL"],
        fail_silently=False,
    )
    return JsonResponse({"status": "success", "message": "E-mail enviado com sucesso!"})


@login_required
def matriculas_periodos_view(request):
    if not _can_manage_matriculas(request.user):
        raise PermissionDenied("Apenas secretaria e coordenação podem gerenciar períodos letivos.")

    form = PeriodoLetivoForm()
    edit_form = None
    periodo_editando = None

    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "criar_periodo":
            form = PeriodoLetivoForm(request.POST)
            if form.is_valid():
                periodo = form.save(commit=False)
                periodo.criado_por = request.user
                periodo.status = periodo.calcular_status_por_data()
                periodo.save()
                messages.success(request, "Período letivo criado.")
                return redirect("matriculas_periodos")
            messages.error(request, "Não foi possível criar o período letivo.")
        elif acao == "editar_periodo":
            periodo_editando = get_object_or_404(PeriodoLetivo, pk=request.POST.get("periodo_id"))
            edit_form = PeriodoLetivoForm(request.POST, instance=periodo_editando)
            if edit_form.is_valid():
                periodo = edit_form.save(commit=False)
                periodo.status = periodo.calcular_status_por_data()
                periodo.save()
                messages.success(request, "Período letivo atualizado.")
                return redirect("matriculas_periodos")
            messages.error(request, "Não foi possível atualizar o período letivo.")
        elif acao == "encerrar_periodo":
            periodo = get_object_or_404(PeriodoLetivo, pk=request.POST.get("periodo_id"))
            periodo.encerrado_manualmente_em = timezone.now()
            periodo.encerrado_manualmente_por = request.user
            periodo.status = PeriodoLetivo.Status.ENCERRADO
            periodo.save(update_fields=["encerrado_manualmente_em", "encerrado_manualmente_por", "status", "atualizado_em"])
            messages.success(request, "Período encerrado manualmente.")
            return redirect("matriculas_periodos")
        elif acao == "reabrir_periodo":
            periodo = get_object_or_404(PeriodoLetivo, pk=request.POST.get("periodo_id"))
            periodo.encerrado_manualmente_em = None
            periodo.encerrado_manualmente_por = None
            periodo.status = periodo.calcular_status_por_data()
            periodo.save(update_fields=["encerrado_manualmente_em", "encerrado_manualmente_por", "status", "atualizado_em"])
            messages.success(request, "Período reaberto.")
            return redirect("matriculas_periodos")
        elif acao == "enviar_email_sem_matricula":
            periodo = get_object_or_404(PeriodoLetivo, pk=request.POST.get("periodo_id"))
            total_pendentes = alunos_ativos_sem_matricula(periodo).count()
            send_email_alunos_sem_matricula.delay(periodo.pk)
            messages.success(request, f"Envio de e-mail agendado para {total_pendentes} aluno(s) sem matrícula.")
            return redirect("matriculas_periodos")
        elif acao == "indeferir_vinculo":
            solicitacao = get_object_or_404(SolicitacaoMatricula, pk=request.POST.get("solicitacao_id"))
            try:
                indeferir_solicitacao_vinculo(
                    solicitacao=solicitacao,
                    usuario=request.user,
                    motivo=request.POST.get("motivo", ""),
                )
                messages.success(request, "Matrícula vínculo indeferida.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            return redirect("matriculas_periodos")

    periodos = list(PeriodoLetivo.objects.select_related("criado_por").prefetch_related("ofertas").order_by("-nome"))
    for periodo in periodos:
        periodo.alunos_sem_matricula = list(alunos_ativos_sem_matricula(periodo))
        periodo.solicitacoes_vinculo = list(
            SolicitacaoMatricula.objects.filter(
                periodo=periodo,
                tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
            )
            .select_related("aluno")
            .order_by("status", "aluno__nome")
        )
        periodo.solicitacoes_disciplinas_total = SolicitacaoMatricula.objects.filter(
            periodo=periodo,
            tipo_matricula=SolicitacaoMatricula.TipoMatricula.DISCIPLINAS,
        ).count()
    return render(
        request,
        "processos/matriculas_periodos.html",
        {
            "form": form,
            "edit_form": edit_form,
            "periodo_editando": periodo_editando,
            "periodos": periodos,
        },
    )


@login_required
def matriculas_disciplinas_view(request):
    if not _can_manage_matriculas(request.user):
        raise PermissionDenied("Apenas secretaria e coordenação podem gerenciar disciplinas.")

    disciplina_form = DisciplinaForm()
    disciplina_edit_form = None
    disciplina_editando = None
    modal_aberto = ""

    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "criar_disciplina":
            disciplina_form = DisciplinaForm(request.POST)
            if disciplina_form.is_valid():
                disciplina_form.save()
                messages.success(request, "Disciplina cadastrada.")
                return redirect("matriculas_disciplinas")
            messages.error(request, "Não foi possível cadastrar a disciplina.")
            modal_aberto = "nova-disciplina"
        elif acao == "editar_disciplina":
            disciplina_editando = get_object_or_404(Disciplina, pk=request.POST.get("disciplina_id"))
            disciplina_edit_form = DisciplinaForm(request.POST, instance=disciplina_editando)
            modal_aberto = f"editar-disciplina-{disciplina_editando.pk}"
            if disciplina_edit_form.is_valid():
                disciplina_edit_form.save()
                messages.success(request, "Disciplina atualizada.")
                return redirect("matriculas_disciplinas")
            messages.error(request, "Não foi possível atualizar a disciplina.")

    disciplinas = Disciplina.objects.order_by("codigo", "nome")

    return render(
        request,
        "processos/matriculas_disciplinas.html",
        {
            "disciplina_form": disciplina_form,
            "disciplina_edit_form": disciplina_edit_form,
            "disciplina_editando": disciplina_editando,
            "disciplinas": disciplinas,
            "tipos_disciplina": Disciplina.Tipo.choices,
            "modal_aberto": modal_aberto,
        },
    )


@login_required
def matriculas_ofertas_view(request):
    if not _can_create_oferta(request.user):
        raise PermissionDenied("Apenas docentes, secretaria e coordenação podem cadastrar ofertas.")

    periodos = list(PeriodoLetivo.objects.order_by("-nome"))
    periodo_id = request.POST.get("periodo") if request.method == "POST" else request.GET.get("periodo")
    periodo_selecionado = None
    if periodo_id:
        periodo_selecionado = get_object_or_404(PeriodoLetivo, pk=periodo_id)
    elif periodos:
        periodo_selecionado = periodos[0]

    oferta_form = OfertaDisciplinaForm(user=request.user, initial={"periodo": periodo_selecionado})
    oferta_editando = None
    modal_aberto = ""

    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao in {"criar_oferta", "editar_oferta"}:
            instance = None
            if acao == "editar_oferta":
                instance = get_object_or_404(OfertaDisciplina, pk=request.POST.get("oferta_id"))
                if not (
                    _can_manage_matriculas(request.user)
                    or instance.docente_responsavel_id == request.user.id
                    or instance.docente_colaborador_id == request.user.id
                ):
                    raise PermissionDenied("Você não pode editar esta oferta.")
                oferta_editando = instance
                modal_aberto = f"editar-oferta-{instance.pk}"
            oferta_form = OfertaDisciplinaForm(request.POST, instance=instance, user=request.user)
            if oferta_form.is_valid():
                oferta = oferta_form.save()
                messages.success(request, "Oferta salva.")
                return redirect(f"{reverse('matriculas_ofertas')}?periodo={oferta.periodo_id}")
            messages.error(request, "Não foi possível salvar a oferta.")
            if acao == "criar_oferta":
                modal_aberto = "nova-oferta"

    ofertas = (
        OfertaDisciplina.objects.select_related("periodo", "disciplina", "docente_responsavel", "docente_colaborador")
        .prefetch_related("encontros", "itens_matricula", "aulas_presenciais__encontro")
        .annotate(
            matriculas_solicitadas=Count(
                "itens_matricula",
                filter=Q(
                    itens_matricula__status__in=[
                        ItemSolicitacaoMatricula.Status.SOLICITADO,
                        ItemSolicitacaoMatricula.Status.HOMOLOGADO,
                    ]
                ),
                distinct=True,
            ),
            matriculas_regulares=Count(
                "itens_matricula",
                filter=Q(
                    itens_matricula__status__in=[
                        ItemSolicitacaoMatricula.Status.SOLICITADO,
                        ItemSolicitacaoMatricula.Status.HOMOLOGADO,
                    ],
                    itens_matricula__solicitacao__tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
                ),
                distinct=True,
            ),
            matriculas_especiais=Count(
                "itens_matricula",
                filter=Q(
                    itens_matricula__status__in=[
                        ItemSolicitacaoMatricula.Status.SOLICITADO,
                        ItemSolicitacaoMatricula.Status.HOMOLOGADO,
                    ],
                    itens_matricula__solicitacao__tipo_aluno=SolicitacaoMatricula.TipoAluno.ESPECIAL,
                ),
                distinct=True,
            ),
            lista_espera=Count(
                "itens_matricula",
                filter=Q(itens_matricula__status=ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA),
                distinct=True,
            ),
            lista_espera_regulares=Count(
                "itens_matricula",
                filter=Q(
                    itens_matricula__status=ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA,
                    itens_matricula__solicitacao__tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
                ),
                distinct=True,
            ),
            lista_espera_especiais=Count(
                "itens_matricula",
                filter=Q(
                    itens_matricula__status=ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA,
                    itens_matricula__solicitacao__tipo_aluno=SolicitacaoMatricula.TipoAluno.ESPECIAL,
                ),
                distinct=True,
            ),
        )
        .order_by("-periodo__nome", "disciplina__nome")
    )
    if periodo_selecionado:
        ofertas = ofertas.filter(periodo=periodo_selecionado)
    ofertas = list(ofertas)
    filtro_nao_conformes = request.GET.get("nao_conformes") == "1"
    for oferta in ofertas:
        oferta.percentual_presencial = percentual_presencial_oferta(oferta)
        oferta.presencial_conforme = oferta_hibrida_conforme(oferta)
    total_nao_conformes = sum(
        1
        for oferta in ofertas
        if oferta.modalidade == OfertaDisciplina.Modalidade.HIBRIDA and not oferta.presencial_conforme
    )
    if filtro_nao_conformes:
        ofertas = [oferta for oferta in ofertas if oferta.modalidade == OfertaDisciplina.Modalidade.HIBRIDA and not oferta.presencial_conforme]
    horarios_semanais_periodos, dias_horarios = _montar_horarios_semanais_ofertas(ofertas)

    return render(
        request,
        "processos/matriculas_ofertas.html",
        {
            "oferta_form": oferta_form,
            "ofertas": ofertas,
            "periodos": periodos,
            "periodo_selecionado": periodo_selecionado,
            "horarios_semanais_periodos": horarios_semanais_periodos,
            "dias_horarios": dias_horarios,
            "can_manage_matriculas": _can_manage_matriculas(request.user),
            "oferta_editando": oferta_editando,
            "modal_aberto": modal_aberto,
            "filtro_nao_conformes": filtro_nao_conformes,
            "total_nao_conformes": total_nao_conformes,
        },
    )


@login_required
def matricula_oferta_planejamento_presencial_view(request, oferta_id):
    oferta = get_object_or_404(
        OfertaDisciplina.objects.select_related("periodo", "disciplina", "docente_responsavel", "docente_colaborador")
        .prefetch_related("encontros", "aulas_presenciais__encontro", "aulas_presenciais__sala"),
        pk=oferta_id,
    )
    if not (
        _can_manage_matriculas(request.user)
        or oferta.docente_responsavel_id == request.user.id
        or oferta.docente_colaborador_id == request.user.id
    ):
        raise PermissionDenied("Você não pode planejar aulas presenciais desta oferta.")
    if oferta.modalidade != OfertaDisciplina.Modalidade.HIBRIDA:
        messages.info(request, "Apenas disciplinas híbridas exigem planejamento de aulas presenciais.")
        return redirect("matriculas_ofertas")

    if request.method == "POST":
        selecoes = []
        datas = request.POST.getlist("aula_data")
        encontros = request.POST.getlist("aula_encontro")
        horas_inicio = request.POST.getlist("aula_hora_inicio")
        horas_fim = request.POST.getlist("aula_hora_fim")
        salas = request.POST.getlist("aula_sala")
        total_linhas = max(len(datas), len(encontros), len(horas_inicio), len(horas_fim), len(salas))
        for index in range(total_linhas):
            data = _parse_date_input(datas[index]) if index < len(datas) and datas[index] else None
            if not data:
                continue
            sala_id = salas[index] if index < len(salas) else ""
            sala = Sala.objects.filter(pk=sala_id, ativa=True).first()
            if not sala:
                messages.error(request, "Informe um ambiente para todas as aulas presenciais.")
                return redirect("matricula_oferta_planejamento_presencial", oferta_id=oferta.pk)
            encontro_id = encontros[index] if index < len(encontros) and encontros[index] else None
            selecoes.append(
                {
                    "encontro_id": int(encontro_id) if encontro_id else None,
                    "data": data,
                    "hora_inicio": parse_time(horas_inicio[index]) if index < len(horas_inicio) else None,
                    "hora_fim": parse_time(horas_fim[index]) if index < len(horas_fim) else None,
                    "sala": sala,
                }
            )
        try:
            salvar_planejamento_presencial_oferta(oferta=oferta, usuario=request.user, selecoes=selecoes)
            send_email_secretaria_planejamento_presencial.delay(oferta.pk, request.user.pk)
            messages.success(request, "Planejamento presencial salvo.")
            return redirect("matricula_oferta_planejamento_presencial", oferta_id=oferta.pk)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))

    datas_planejamento = []
    for encontro in oferta.encontros.all():
        for data in datas_encontro_no_periodo(encontro):
            datas_planejamento.append(
                {
                    "data": data.strftime("%d/%m/%Y"),
                    "data_iso": data.isoformat(),
                    "label": f"{data:%d/%m/%Y} - {encontro.get_dia_semana_display()}",
                    "encontro_id": encontro.pk,
                    "hora_inicio": encontro.hora_inicio.strftime("%H:%M"),
                    "hora_fim": encontro.hora_fim.strftime("%H:%M"),
                }
            )

    aulas_form_rows = [
        {
            "data": aula.data.strftime("%d/%m/%Y"),
            "encontro_id": aula.encontro_id or "",
            "hora_inicio": aula.hora_inicio.strftime("%H:%M"),
            "hora_fim": aula.hora_fim.strftime("%H:%M"),
            "sala_id": aula.sala_id,
        }
        for aula in oferta.aulas_presenciais.all()
    ]
    carga_total_minutos = carga_horaria_total_oferta_minutos(oferta)
    carga_presencial_minutos = carga_horaria_presencial_oferta_minutos(oferta)

    return render(
        request,
        "processos/matricula_oferta_planejamento_presencial.html",
        {
            "oferta": oferta,
            "salas": Sala.objects.filter(ativa=True, polo__ativo=True).select_related("polo").order_by("polo__nome", "nome"),
            "datas_planejamento": datas_planejamento,
            "aulas_form_rows": aulas_form_rows,
            "carga_total_horas": round(carga_total_minutos / 60, 1),
            "carga_presencial_horas": round(carga_presencial_minutos / 60, 1),
            "carga_minima_horas": round((carga_total_minutos * 0.25) / 60, 1),
            "percentual_presencial": percentual_presencial_oferta(oferta),
            "conforme": oferta_hibrida_conforme(oferta),
        },
    )


@login_required
def matricula_solicitar_view(request, periodo_id=None):
    if request.user.tipo_usuario != User.TipoUsuario.ALUNO:
        raise PermissionDenied("Apenas alunos podem solicitar matrícula.")

    trajetoria_ativa = request.user.aluno.trajetoria_ativa()
    tipo_aluno = tipo_aluno_matricula_por_trajetoria(trajetoria_ativa) if trajetoria_ativa else None
    pode_solicitar_matricula = bool(trajetoria_ativa and tipo_aluno)
    hoje_servidor = timezone.localdate()
    periodos_abertos = [periodo for periodo in PeriodoLetivo.objects.order_by("-nome") if periodo.aceita_solicitacao_matricula]
    proximo_periodo = (
        PeriodoLetivo.objects.filter(matricula_inicio__gt=hoje_servidor, encerrado_manualmente_em__isnull=True)
        .order_by("matricula_inicio", "nome")
        .first()
    )
    periodo = None
    if periodo_id:
        periodo_selecionado = get_object_or_404(PeriodoLetivo, pk=periodo_id)
        if periodo_selecionado.aceita_solicitacao_matricula:
            periodo = periodo_selecionado
        else:
            proximo_periodo = periodo_selecionado if periodo_selecionado.matricula_inicio > hoje_servidor else proximo_periodo
    else:
        periodo = periodos_abertos[0] if periodos_abertos else None
    form = SolicitacaoMatriculaForm(periodo=periodo) if pode_solicitar_matricula else None

    if request.method == "POST":
        if not pode_solicitar_matricula:
            raise PermissionDenied("Você não está apto a solicitar matrícula.")
        periodo = get_object_or_404(PeriodoLetivo, pk=request.POST.get("periodo_id"))
        form = SolicitacaoMatriculaForm(request.POST, periodo=periodo)
        if form.is_valid():
            try:
                solicitacao = salvar_solicitacao_matricula(
                    aluno=request.user.aluno,
                    periodo=periodo,
                    tipo_matricula=(
                        SolicitacaoMatricula.TipoMatricula.VINCULO
                        if form.cleaned_data["matricula_vinculo"]
                        else SolicitacaoMatricula.TipoMatricula.DISCIPLINAS
                    ),
                    tipo_aluno=tipo_aluno,
                    ofertas=form.cleaned_data["ofertas"],
                    aceitar_lista_espera=form.cleaned_data["aceitar_lista_espera"],
                    observacao=form.cleaned_data["observacao"],
                )
                messages.success(request, "Solicitação de matrícula registrada.")
                return redirect("matricula_minha_solicitacao", solicitacao_id=solicitacao.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
        messages.error(request, "Não foi possível registrar a solicitação.")

    # Grade semanal das disciplinas do periodo. A coordenacao e o docente ja
    # enxergavam onde cada oferta cai na semana; o aluno, que e quem monta a
    # propria grade, so via o dia e a hora escritos em cada disciplina e tinha
    # que cruzar os choques de horario de cabeca.
    horario_semanal = None
    if periodo and form is not None:
        ofertas_do_periodo = form.fields["ofertas"].queryset.prefetch_related("encontros")
        horarios, _ = _montar_horarios_semanais_ofertas(ofertas_do_periodo)
        horario_semanal = horarios[0] if horarios else None

    return render(
        request,
        "processos/matricula_solicitar.html",
        {
            "periodos_abertos": periodos_abertos,
            "proximo_periodo": proximo_periodo,
            "periodo": periodo,
            "horario_semanal": horario_semanal,
            "form": form,
            "trajetoria_ativa": trajetoria_ativa,
            "pode_solicitar_matricula": pode_solicitar_matricula,
            "nivel_trajetoria_display": trajetoria_ativa.get_nivel_curso_display() if trajetoria_ativa else "",
            "tipo_aluno_display": dict(SolicitacaoMatricula.TipoAluno.choices).get(tipo_aluno),
        },
    )


@login_required
def matriculas_minhas_view(request):
    if request.user.tipo_usuario != User.TipoUsuario.ALUNO:
        raise PermissionDenied("Apenas alunos acessam suas matrículas.")
    solicitacoes = (
        SolicitacaoMatricula.objects.filter(aluno=request.user)
        .select_related("periodo")
        .prefetch_related("itens__oferta__disciplina", "itens__oferta__encontros")
        .order_by("-periodo__nome")
    )
    return render(request, "processos/matriculas_minhas.html", {"solicitacoes": solicitacoes})


@login_required
def matriculas_solicitacoes_view(request):
    if not _can_manage_matriculas(request.user):
        raise PermissionDenied("Apenas secretaria e coordenação podem gerir solicitações de matrícula.")

    periodos = list(PeriodoLetivo.objects.order_by("-nome"))
    periodo_id = request.POST.get("periodo_id") or request.GET.get("periodo")
    periodo = None
    if periodo_id:
        periodo = get_object_or_404(PeriodoLetivo, pk=periodo_id)
    elif periodos:
        periodo = periodos[0]

    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            if acao in {"indeferir_item", "cancelar_item"}:
                item = get_object_or_404(ItemSolicitacaoMatricula, pk=request.POST.get("item_id"))
                periodo = item.solicitacao.periodo
                if acao == "indeferir_item":
                    indeferir_item_matricula(item=item, usuario=request.user, motivo=request.POST.get("motivo", ""))
                    messages.success(request, "Item de matrícula indeferido.")
                else:
                    cancelar_item_matricula(item=item, usuario=request.user)
                    messages.success(request, "Item de matrícula cancelado.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        parametros = [f"periodo={periodo.pk}"] if periodo else []
        disciplina_param = request.POST.get("disciplina", "").strip()
        if disciplina_param:
            parametros.append(f"disciplina={disciplina_param}")
        query = f"?{'&'.join(parametros)}" if parametros else ""
        return redirect(f"{reverse('matriculas_solicitacoes')}{query}")

    disciplinas = []
    itens_disciplina = ItemSolicitacaoMatricula.objects.none()
    solicitacoes_vinculo = SolicitacaoMatricula.objects.none()
    disciplina_selecionada = request.GET.get("disciplina", "").strip()
    disciplina_obj = None
    resumo_solicitacoes = {
        "matriculas_regulares": 0,
        "matriculas_especiais": 0,
        "espera_regulares": 0,
        "espera_especiais": 0,
    }
    if periodo:
        itens_periodo = ItemSolicitacaoMatricula.objects.filter(oferta__periodo=periodo)
        disciplinas = list(
            Disciplina.objects.filter(ofertas__periodo=periodo, ofertas__itens_matricula__isnull=False)
            .annotate(total_solicitacoes=Count("ofertas__itens_matricula", distinct=True))
            .order_by("codigo", "nome")
            .distinct()
        )
        total_vinculos = SolicitacaoMatricula.objects.filter(
            periodo=periodo,
            tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
        ).count()
        for tipo_aluno, chave_matricula, chave_espera in (
            (SolicitacaoMatricula.TipoAluno.REGULAR, "matriculas_regulares", "espera_regulares"),
            (SolicitacaoMatricula.TipoAluno.ESPECIAL, "matriculas_especiais", "espera_especiais"),
        ):
            itens_tipo = itens_periodo.filter(solicitacao__tipo_aluno=tipo_aluno)
            resumo_solicitacoes[chave_matricula] = itens_tipo.filter(
                status__in=[ItemSolicitacaoMatricula.Status.SOLICITADO, ItemSolicitacaoMatricula.Status.HOMOLOGADO]
            ).count()
            resumo_solicitacoes[chave_espera] = itens_tipo.filter(
                status=ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA
            ).count()

        if disciplina_selecionada == "vinculo":
            solicitacoes_vinculo = SolicitacaoMatricula.objects.filter(
                periodo=periodo,
                tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
            ).select_related("aluno", "periodo").order_by("status", "aluno__nome")
        elif disciplina_selecionada.isdigit():
            disciplina_obj = next(
                (disciplina for disciplina in disciplinas if disciplina.pk == int(disciplina_selecionada)),
                None,
            )
            if disciplina_obj:
                itens_disciplina = itens_periodo.filter(
                    oferta__disciplina=disciplina_obj,
                ).select_related(
                    "solicitacao",
                    "solicitacao__aluno",
                    "oferta",
                    "oferta__docente_responsavel",
                    "oferta__docente_colaborador",
                ).order_by("status", "solicitacao__aluno__nome")
            for item in itens_disciplina:
                item.editavel = item.status not in {
                    ItemSolicitacaoMatricula.Status.INDEFERIDO,
                    ItemSolicitacaoMatricula.Status.CANCELADO,
                }
                if item.status in {
                    ItemSolicitacaoMatricula.Status.SOLICITADO,
                    ItemSolicitacaoMatricula.Status.HOMOLOGADO,
                }:
                    item.badge_class = "badge-ok"
                elif item.status in {
                    ItemSolicitacaoMatricula.Status.INDEFERIDO,
                    ItemSolicitacaoMatricula.Status.CANCELADO,
                }:
                    item.badge_class = "badge-no"
                else:
                    item.badge_class = "badge-info"

    return render(
        request,
        "processos/matriculas_solicitacoes.html",
        {
            "periodos": periodos,
            "periodo": periodo,
            "disciplinas": disciplinas,
            "disciplina_selecionada": disciplina_selecionada,
            "disciplina_obj": disciplina_obj,
            "itens_disciplina": itens_disciplina,
            "solicitacoes_vinculo": solicitacoes_vinculo,
            "total_vinculos": total_vinculos if periodo else 0,
            "resumo_solicitacoes": resumo_solicitacoes,
        },
    )


@login_required
def matriculas_solicitacoes_exportar_view(request):
    if not _can_manage_matriculas(request.user):
        raise PermissionDenied("Apenas secretaria e coordenação podem exportar solicitações de matrícula.")
    periodo = get_object_or_404(PeriodoLetivo, pk=request.GET.get("periodo"))
    conteudo = gerar_xlsx_solicitacoes_periodo(periodo)
    response = HttpResponse(
        conteudo,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="solicitacoes_matricula_{periodo.nome}.xlsx"'
    return response


@login_required
def matricula_minha_solicitacao_view(request, solicitacao_id):
    solicitacao = get_object_or_404(
        SolicitacaoMatricula.objects.select_related("periodo", "aluno").prefetch_related(
            "itens__oferta__disciplina",
            "itens__oferta__docente_responsavel",
            "itens__oferta__docente_colaborador",
            "itens__oferta__encontros",
        ),
        pk=solicitacao_id,
    )
    if solicitacao.aluno_id != request.user.id and not _can_manage_matriculas(request.user):
        raise PermissionDenied("Você não pode visualizar esta solicitação.")
    return render(request, "processos/matricula_minha_solicitacao.html", {"solicitacao": solicitacao})


@login_required
def matricula_oferta_alunos_view(request, oferta_id):
    if not _can_manage_matriculas(request.user):
        raise PermissionDenied("Apenas secretaria e coordenação podem gerir matrículas.")

    oferta = get_object_or_404(
        OfertaDisciplina.objects.select_related(
            "periodo",
            "disciplina",
            "docente_responsavel",
            "docente_colaborador",
        ).prefetch_related("encontros"),
        pk=oferta_id,
    )
    if request.method == "POST":
        acao = request.POST.get("acao")
        item = get_object_or_404(ItemSolicitacaoMatricula, pk=request.POST.get("item_id")) if request.POST.get("item_id") else None
        try:
            if acao == "indeferir" and item:
                indeferir_item_matricula(item=item, usuario=request.user, motivo=request.POST.get("motivo", ""))
                messages.success(request, "Solicitação indeferida.")
            elif acao == "cancelar" and item:
                cancelar_item_matricula(item=item, usuario=request.user)
                messages.success(request, "Matrícula cancelada.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("matricula_oferta_alunos", oferta_id=oferta.pk)

    itens = (
        ItemSolicitacaoMatricula.objects.filter(oferta=oferta)
        .select_related("solicitacao", "solicitacao__aluno", "indeferido_por")
        .order_by("status", "solicitado_em", "solicitacao__aluno__nome")
    )
    return render(
        request,
        "processos/matricula_oferta_alunos.html",
        {
            "oferta": oferta,
            "itens": itens,
            "tipos_aluno": SolicitacaoMatricula.TipoAluno.choices,
            "vagas_regulares_disponiveis": oferta.vagas_disponiveis(SolicitacaoMatricula.TipoAluno.REGULAR),
            "vagas_especiais_disponiveis": oferta.vagas_disponiveis(SolicitacaoMatricula.TipoAluno.ESPECIAL),
        },
    )


@login_required
def matricula_oferta_exportar_view(request, oferta_id):
    if not _can_manage_matriculas(request.user):
        raise PermissionDenied("Apenas secretaria e coordenação podem exportar listas.")
    oferta = get_object_or_404(OfertaDisciplina.objects.select_related("disciplina", "periodo"), pk=oferta_id)
    conteudo = gerar_xlsx_lista_oferta(oferta)
    filename = f"matricula_{oferta.periodo.nome}_{oferta.disciplina.codigo or oferta.pk}.xlsx"
    response = HttpResponse(
        conteudo,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _check_item(texto: str, cumprido: bool = False, detalhe: str = ""):
    return {"texto": texto, "cumprido": cumprido, "detalhe": detalhe}


def _progresso_item(texto: str, atual, total, detalhe: str = ""):
    percentual = 0
    if total:
        percentual = min(int((float(atual) / float(total)) * 100), 100)
    return {
        "texto": texto,
        "atual": atual,
        "total": total,
        "percentual": percentual,
        "detalhe": detalhe,
        "cumprido": atual >= total,
    }


def _checklist_integralizacao_trajetoria(trajetoria: TrajetoriaAcademica):
    if not trajetoria:
        return None

    creditos_aprovados = (
        trajetoria.disciplinas.filter(situacao=DisciplinaTrajetoria.Situacao.APROVADA).aggregate(
            total=Sum("creditos")
        )["total"]
        or 0
    )
    resumo_horas = LancamentoHorasComplementares.resumo_trajetoria(trajetoria)
    horas_complementares = resumo_horas["horas_computadas"]
    total_publicacoes = trajetoria.publicacoes.count()
    estagios = list(trajetoria.estagios_docencia.all())
    estagio_recente = estagios[-1] if estagios else None
    estagios_doutorado = estagios[:2]
    tem_defesa = bool(trajetoria.numero_defesa or trajetoria.data_defesa)

    if trajetoria.nivel_curso == Aluno.NivelCurso.DOUTORADO:
        grupos = [
            {
                "titulo": "Créditos e disciplinas",
                "itens": [
                    _check_item("Cursar 3 disciplinas obrigatórias (12 créditos)."),
                    _check_item("Cursar 3 disciplinas eletivas (12 créditos), podendo ser da área ou gerais."),
                    _check_item("Cursar Revisão Sistemática da Literatura (4 créditos)."),
                    _check_item("Desenvolver e aprovar o Projeto de Pesquisa (4 créditos)."),
                ],
            },
            {
                "titulo": "Seminários / Horas complementares",
                "tipo": "progresso",
                "progresso": _progresso_item(
                    "45h regimentais",
                    horas_complementares,
                    45,
                    "Seminário de Complementação",
                ),
            },
            {
                "titulo": "Estágio de Docência",
                "itens": [
                    _check_item(
                        f"Estágio 1: {estagios_doutorado[0].get_status_display() if len(estagios_doutorado) >= 1 else 'Não cadastrado'}.",
                        len(estagios_doutorado) >= 1 and estagios_doutorado[0].status == EstagioDocencia.Status.CONCLUIDO,
                    ),
                    _check_item(
                        f"Estágio 2: {estagios_doutorado[1].get_status_display() if len(estagios_doutorado) >= 2 else 'Não cadastrado'}.",
                        len(estagios_doutorado) >= 2 and estagios_doutorado[1].status == EstagioDocencia.Status.CONCLUIDO,
                    ),
                ],
            },
            {
                "titulo": "Qualificação de Doutorado",
                "itens": [
                    _check_item(
                        f"Prazo: {trajetoria.prazo_qualificacao or '-'}. Status: {'Concluída' if trajetoria.isQualificado else 'Pendente'}.",
                        trajetoria.isQualificado,
                    ),
                ],
            },
            {
                "titulo": "Publicações",
                "itens": [
                    _check_item(
                        f"Publicações registradas na trajetória: {total_publicacoes}.",
                        total_publicacoes > 0,
                    ),
                ],
            },
            {
                "titulo": "Defesa",
                "itens": [
                    _check_item(
                        f"Prazo: {trajetoria.prazo_defesa or '-'}. Status: {'Defendida' if tem_defesa else 'Pendente'}.",
                        tem_defesa,
                    ),
                    _check_item(
                        f"Depósito final: {'Realizado' if trajetoria.deposito_versao_final else 'Pendente'}.",
                        trajetoria.deposito_versao_final,
                    ),
                ],
            },
        ]
        titulo = "Checklist de Integralização - Doutorado"
    elif trajetoria.nivel_curso == Aluno.NivelCurso.MESTRADO:
        grupos = [
            {
                "titulo": "Créditos e disciplinas",
                "itens": [
                    _check_item("Cursar 2 disciplinas obrigatórias (8 créditos)."),
                    _check_item("Cursar 2 disciplinas eletivas da área (8 créditos)."),
                    _check_item(
                        "Cursar 2 disciplinas eletivas gerais (8 créditos). Pode substituir uma eletiva geral por uma da área, ou vice-versa, mediante justificativa e anuência do orientador.",
                    ),
                ],
            },
            {
                "titulo": "Seminários / Horas complementares",
                "tipo": "progresso",
                "progresso": _progresso_item(
                    "45h regimentais",
                    horas_complementares,
                    45,
                    "Seminário de Complementação",
                ),
            },
            {
                "titulo": "Estágio de Docência",
                "itens": [
                    _check_item(
                        f"Status: {estagio_recente.get_status_display() if estagio_recente else 'Não cadastrado'}.",
                        bool(estagio_recente and estagio_recente.status == EstagioDocencia.Status.CONCLUIDO),
                    ),
                ],
            },
            {
                "titulo": "Projeto de Dissertação",
                "itens": [
                    _check_item(
                        f"Prazo: {trajetoria.prazo_qualificacao or '-'}. Status: {'Concluído' if trajetoria.isQualificado else 'Pendente'}.",
                        trajetoria.isQualificado,
                    ),
                ],
            },
            {
                "titulo": "Publicações",
                "itens": [
                    _check_item(
                        f"Publicações registradas na trajetória: {total_publicacoes}.",
                        total_publicacoes > 0,
                    ),
                ],
            },
            {
                "titulo": "Defesa",
                "itens": [
                    _check_item(
                        f"Prazo: {trajetoria.prazo_defesa or '-'}. Status: {'Defendida' if tem_defesa else 'Pendente'}.",
                        tem_defesa,
                    ),
                    _check_item(
                        f"Depósito final: {'Realizado' if trajetoria.deposito_versao_final else 'Pendente'}.",
                        trajetoria.deposito_versao_final,
                    ),
                ],
            },
        ]
        titulo = "Checklist de Integralização - Mestrado"
    else:
        return None

    # Progresso geral. Sem ele o aluno precisa contar item por item para saber
    # onde esta: sao 8 linhas, quase todas "Pendente" no comeco do curso.
    itens_com_marca = [item for grupo in grupos for item in grupo.get("itens", [])]
    cumpridos = sum(1 for item in itens_com_marca if item.get("cumprido"))

    return {
        "titulo": titulo,
        "trajetoria": trajetoria,
        "creditos_aprovados": creditos_aprovados,
        "horas_complementares": horas_complementares,
        "total_publicacoes": total_publicacoes,
        "grupos": grupos,
        "itens_cumpridos": cumpridos,
        "itens_total": len(itens_com_marca),
        "percentual_concluido": round(100 * cumpridos / len(itens_com_marca)) if itens_com_marca else 0,
    }


def _resumo_checklist_banca(trajetoria: TrajetoriaAcademica):
    checklist = _checklist_integralizacao_trajetoria(trajetoria)
    if not checklist:
        return None
    itens = []
    for grupo in checklist["grupos"]:
        if grupo.get("tipo") == "progresso":
            progresso = grupo["progresso"]
            itens.append(
                {
                    "grupo": grupo["titulo"],
                    "texto": f"{progresso['texto']}: {progresso['atual']}h de {progresso['total']}h",
                    "cumprido": progresso["cumprido"],
                }
            )
            continue
        for item in grupo.get("itens", []):
            itens.append(
                {
                    "grupo": grupo["titulo"],
                    "texto": item["texto"],
                    "cumprido": item["cumprido"],
                }
            )
    return {"trajetoria": trajetoria, "titulo": checklist["titulo"], "itens": itens}


@login_required
def home_view(request):
    is_coordenador = _is_coordenador(request.user)
    has_gestao_access = _has_gestao_access(request.user)
    can_view_dashboard = _can_view_dashboard(request.user)
    can_view_processos = _can_view_processos(request.user)
    can_view_caixa = _can_view_caixa(request.user)
    meus_processos_base = Processo.objects.filter(usuario_criado_por=request.user)
    meus_processos_requerente = meus_processos_base.filter(setor_atual__nome="Requerente")
    assinaturas_pendentes = _assinaturas_pendentes_queryset(request.user)
    context = {
        "meus_processos_requerente": meus_processos_requerente,
        "assinaturas_pendentes": assinaturas_pendentes,
        "is_coordenador": is_coordenador,
        "has_gestao_access": has_gestao_access,
        "can_view_dashboard": can_view_dashboard,
        "can_view_processos": can_view_processos,
        "can_view_caixa": can_view_caixa,
        "can_add_processo": _can_add_processo(request.user),
        "show_side_menu": request.user.tipo_usuario in [User.TipoUsuario.DOCENTE, User.TipoUsuario.ALUNO],
        "side_menu_title": "Menu",
        "side_menu_items": _menu_lateral_home(request.user),
    }

    if request.user.tipo_usuario == User.TipoUsuario.DOCENTE:
        orientandos = (
            Aluno.objects.filter(
                trajetorias__orientador=request.user,
                trajetorias__status=TrajetoriaAcademica.Status.ATIVA,
            )
            .distinct()
            .order_by("nome")
        )
        processos_orientandos = (
            Processo.objects.select_related("usuario_criado_por", "setor_atual")
            .filter(usuario_criado_por__in=orientandos.values("id"))
            .order_by("-data_criacao")
        )
        cientes_pendentes_orientador = (
            ManifestacaoProcesso.objects.select_related("processo", "solicitado_por")
            .filter(
                tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
                status=ManifestacaoProcesso.StatusManifestacao.PENDENTE,
                responsavel=request.user,
            )
            .order_by("-data_solicitacao")
        )
        context["orientandos"] = orientandos
        context["processos_orientandos"] = processos_orientandos
        context["cientes_pendentes_orientador"] = cientes_pendentes_orientador

    if has_gestao_access:
        # Os quatro cartoes da visao geral da gestao exibiam "1" fixo -- nao
        # eram metricas, eram atalhos com um numero inventado. Um numero que
        # nao significa nada ensina o usuario a ignorar todos os numeros da
        # tela, inclusive os verdadeiros.
        setores_do_usuario = _setores_caixa(request.user)
        context["gestao_metricas"] = {
            "alunos_ativos": Aluno.objects.filter(
                trajetorias__status=TrajetoriaAcademica.Status.ATIVA
            ).distinct().count(),
            "processos_abertos": Processo.objects.exclude(
                status=Processo.StatusProcesso.FINALIZADO
            ).count(),
            "na_caixa": Processo.objects.filter(
                setor_atual__in=setores_do_usuario
            ).exclude(status=Processo.StatusProcesso.FINALIZADO).count(),
            "cadastros_a_validar": Aluno.objects.filter(
                status_aluno=Aluno.StatusAluno.EM_AVALIACAO
            ).count(),
        }

    if request.user.tipo_usuario == User.TipoUsuario.ALUNO:
        aluno = getattr(request.user, "aluno", None)
        trajetoria_recente = None
        matricula_atual = None
        if aluno:
            trajetoria_recente = (
                aluno.trajetorias.prefetch_related(
                    "disciplinas",
                    "estagios_docencia",
                    "lancamentos_horas_complementares",
                )
                .order_by("-criado_em")
                .first()
            )
            matricula_atual = (
                SolicitacaoMatricula.objects.select_related("periodo")
                .prefetch_related("itens__oferta__disciplina")
                .filter(aluno=aluno)
                .order_by("-periodo__nome", "-criado_em")
                .first()
            )
        context["aluno_trajetoria_recente"] = trajetoria_recente
        context["aluno_matricula_atual"] = matricula_atual
        context["aluno_checklist_integralizacao"] = _checklist_integralizacao_trajetoria(trajetoria_recente)

    return render(request, "processos/home.html", context)


@login_required
def me_view(request):
    if request.method == "POST":
        if "save_profile" in request.POST:
            profile_form = UserProfileForm(request.POST, instance=request.user)
            password_form = PasswordChangeForm(user=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Perfil atualizado com sucesso.")
                return redirect("me")
        elif "change_password" in request.POST:
            profile_form = UserProfileForm(instance=request.user)
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Senha alterada com sucesso.")
                return redirect("me")
        else:
            profile_form = UserProfileForm(instance=request.user)
            password_form = PasswordChangeForm(user=request.user)
    else:
        profile_form = UserProfileForm(instance=request.user)
        password_form = PasswordChangeForm(user=request.user)

    participacoes_ativas = (
        request.user.participacoes_setor.select_related("setor", "designado_por")
        .filter(data_saida__isnull=True)
        .order_by("setor__nome")
    )
    historico_participacoes = (
        request.user.participacoes_setor.select_related("setor", "designado_por")
        .exclude(data_saida__isnull=True)
        .order_by("-data_saida", "setor__nome")
    )

    return render(
        request,
        "processos/me.html",
        {
            "profile_form": profile_form,
            "password_form": password_form,
            "participacoes_ativas": participacoes_ativas,
            "historico_participacoes": historico_participacoes,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def setores_comissoes_view(request):
    can_edit_setores = _is_coordenador(request.user)
    if not (can_edit_setores or _is_servidor(request.user) or _is_secretaria_member(request.user)):
        raise PermissionDenied("Acesso restrito a coordenadores e servidores.")

    setor_editado = None
    setor_id = request.GET.get("editar") if can_edit_setores else None
    if request.method == "POST":
        if not can_edit_setores:
            raise PermissionDenied("Apenas coordenadores podem alterar setores e comissões.")
        setor_id = request.POST.get("setor_id")
    if setor_id:
        setor_editado = get_object_or_404(Setor, pk=setor_id)

    if request.method == "POST" and can_edit_setores:
        if "encerrar_membro" in request.POST:
            membro = get_object_or_404(
                SetorMembro,
                pk=request.POST.get("membro_id"),
                data_saida__isnull=True,
            )
            membro.encerrar()
            messages.success(request, "Participação encerrada.")
            return redirect("setores_comissoes")

        form = SetorComissaoForm(request.POST, instance=setor_editado)
        if not setor_editado:
            raise PermissionDenied("Use a página Criar Comissão para cadastrar novas comissões.")
        if form.is_valid():
            setor = form.save(commit=False)
            setor.save()

            membros_selecionados = set()
            for campo in ["docentes", "servidores", "alunos"]:
                membros_selecionados.update(form.cleaned_data[campo].values_list("id", flat=True))
            membros_ativos = SetorMembro.objects.filter(setor=setor, data_saida__isnull=True)
            for membro in membros_ativos.exclude(usuario_id__in=membros_selecionados):
                membro.encerrar()
            usuarios_ativos = set(membros_ativos.values_list("usuario_id", flat=True))
            for usuario_id in membros_selecionados - usuarios_ativos:
                SetorMembro.objects.create(
                    setor=setor,
                    usuario_id=usuario_id,
                    designado_por=request.user,
                )

            messages.success(request, "Setor/comissão salvo com sucesso.")
            return redirect("setores_comissoes")
        messages.error(request, "Não foi possível salvar o setor/comissão.")
    else:
        initial = {}
        if setor_editado:
            membros_ativos = setor_editado.membros.filter(data_saida__isnull=True).select_related("usuario")
            initial["docentes"] = [
                membro.usuario_id
                for membro in membros_ativos
                if membro.usuario.tipo_usuario == User.TipoUsuario.DOCENTE
            ]
            initial["servidores"] = [
                membro.usuario_id
                for membro in membros_ativos
                if membro.usuario.tipo_usuario == User.TipoUsuario.SERVIDOR
            ]
            initial["alunos"] = [
                membro.usuario_id
                for membro in membros_ativos
                if membro.usuario.tipo_usuario == User.TipoUsuario.ALUNO
            ]
        form = SetorComissaoForm(instance=setor_editado, initial=initial)

    setores = (
        Setor.objects.prefetch_related(
            Prefetch(
                "membros",
                queryset=SetorMembro.objects.select_related("usuario", "designado_por").order_by("usuario__nome"),
                to_attr="participacoes_prefetch",
            )
        )
        .exclude(nome="Requerente")
        .order_by("tipo", "nome")
    )
    return render(
        request,
        "processos/setores_comissoes.html",
        {
            "form": form,
            "setor_editado": setor_editado,
            "can_edit_setores": can_edit_setores,
            "setores": setores,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def criar_comissao_view(request):
    if not _is_coordenador(request.user):
        raise PermissionDenied("Acesso restrito a coordenadores.")

    if request.method == "POST":
        form = SetorComissaoForm(request.POST)
        if form.is_valid():
            setor = form.save(commit=False)
            setor.tipo = Setor.TipoSetor.COMISSAO
            setor.save()

            membros_selecionados = set()
            for campo in ["docentes", "servidores", "alunos"]:
                membros_selecionados.update(form.cleaned_data[campo].values_list("id", flat=True))
            for usuario_id in membros_selecionados:
                SetorMembro.objects.create(
                    setor=setor,
                    usuario_id=usuario_id,
                    designado_por=request.user,
                )

            messages.success(request, "Comissão criada com sucesso.")
            return redirect("setores_comissoes")
        messages.error(request, "Não foi possível criar a comissão.")
    else:
        form = SetorComissaoForm()

    return render(
        request,
        "processos/criar_comissao.html",
        {
            "form": form,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def coordenacao_dashboard_view(request):
    if not _can_view_dashboard(request.user):
        raise PermissionDenied("Acesso restrito a coordenadores e servidores.")

    trajetorias_ativas = TrajetoriaAcademica.objects.filter(
        status=TrajetoriaAcademica.Status.ATIVA,
    ).select_related("aluno")
    trajetorias_orientacao_ativas = trajetorias_ativas.exclude(
        nivel_curso=Aluno.NivelCurso.POSDOUTORADO,
    )
    trajetorias_supervisao_ativas = trajetorias_ativas.filter(
        nivel_curso=Aluno.NivelCurso.POSDOUTORADO,
    )
    docentes = (
        Docente.objects.prefetch_related(
            Prefetch(
                "trajetorias_orientadas",
                queryset=trajetorias_orientacao_ativas,
                to_attr="trajetorias_orientadas_ativas",
            ),
            Prefetch(
                "trajetorias_coorientadas",
                queryset=trajetorias_ativas,
                to_attr="trajetorias_coorientadas_ativas",
            ),
            Prefetch(
                "trajetorias_orientadas",
                queryset=trajetorias_supervisao_ativas,
                to_attr="trajetorias_supervisionadas_ativas",
            ),
        )
        .annotate(
            total_orientandos=Count(
                "trajetorias_orientadas__aluno",
                filter=Q(trajetorias_orientadas__status=TrajetoriaAcademica.Status.ATIVA)
                & ~Q(trajetorias_orientadas__nivel_curso=Aluno.NivelCurso.POSDOUTORADO),
                distinct=True,
            ),
            total_coorientandos=Count(
                "trajetorias_coorientadas__aluno",
                filter=Q(trajetorias_coorientadas__status=TrajetoriaAcademica.Status.ATIVA),
                distinct=True,
            ),
            total_supervisoes=Count(
                "trajetorias_orientadas__aluno",
                filter=Q(
                    trajetorias_orientadas__status=TrajetoriaAcademica.Status.ATIVA,
                    trajetorias_orientadas__nivel_curso=Aluno.NivelCurso.POSDOUTORADO,
                ),
                distinct=True,
            ),
        )
        .order_by("-total_orientandos", "-total_supervisoes", "nome")
    )
    return render(
        request,
        "processos/coordenacao_dashboard.html",
        {
            "docentes": docentes,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": True,
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def processos_view(request):
    if not _can_view_processos(request.user):
        raise PermissionDenied("Acesso restrito a docentes e servidores.")

    queryset = Processo.objects.select_related("usuario_criado_por", "setor_atual").order_by("-data_criacao")
    tipo = request.GET.get("tipo", "").strip()
    status = request.GET.get("status", "").strip()
    setor_id = request.GET.get("setor", "").strip()
    termo = request.GET.get("q", "").strip()
    somente_atrasados = request.GET.get("atrasados") == "1"

    if somente_atrasados:
        queryset = queryset.filter(prazo_limite__lt=timezone.localdate()).exclude(
            status=Processo.StatusProcesso.FINALIZADO
        )
    if tipo:
        queryset = queryset.filter(tipo=tipo)
    if status:
        queryset = queryset.filter(status=status)
    if setor_id:
        queryset = queryset.filter(setor_atual_id=setor_id)
    if termo:
        queryset = queryset.filter(
            Q(numero__icontains=termo)
            | Q(assunto__icontains=termo)
            | Q(descricao__icontains=termo)
            | Q(usuario_criado_por__nome__icontains=termo)
        )
    pagina = _paginar(request, queryset)
    return render(
        request,
        "processos/processos_lista.html",
        {
            "processos": pagina.object_list,
            "pagina": pagina,
            "tipos": Processo.TipoProcesso.choices,
            "status_list": Processo.StatusProcesso.choices,
            "setores": Setor.objects.order_by("nome"),
            "filtro_tipo": tipo,
            "filtro_status": status,
            "filtro_setor": setor_id,
            "filtro_q": termo,
            "filtro_atrasados": somente_atrasados,
            "filtros_ativos": _filtros_ativos(
                request,
                {
                    "q": ("Busca", None),
                    "status": ("Status", dict(Processo.StatusProcesso.choices).get),
                    "setor": ("Setor", lambda v: _nome_do_setor(v)),
                    "tipo": ("Tipo", dict(Processo.TipoProcesso.choices).get),
                    "atrasados": ("", lambda _: "Somente atrasados"),
                },
            ),
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


def cadastro_aluno_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = AlunoCadastroForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("cadastro_aluno_sucesso")
    else:
        form = AlunoCadastroForm()

    return render(
        request,
        "registration/cadastro_aluno.html",
        {"form": form},
    )


def cadastro_aluno_sucesso_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    return render(request, "registration/cadastro_aluno_sucesso.html")


def _aprovar_cadastro_aluno(*, aluno, usuario):
    aluno.trajetorias.filter(status=TrajetoriaAcademica.Status.ATIVA).update(
        status=TrajetoriaAcademica.Status.CONCLUIDA,
    )
    trajetorias_em_homologacao = aluno.trajetorias.filter(
        status=TrajetoriaAcademica.Status.EM_HOMOLOGACAO
    )
    trajetorias_em_homologacao.exclude(
        nivel_curso=Aluno.NivelCurso.ALUNO_ESPECIAL
    ).update(status=TrajetoriaAcademica.Status.ATIVA)
    trajetorias_em_homologacao.filter(
        nivel_curso=Aluno.NivelCurso.ALUNO_ESPECIAL
    ).update(status=TrajetoriaAcademica.Status.CONCLUIDA)
    aluno.status_aluno = Aluno.StatusAluno.ATIVO
    aluno.save()
    _registrar_alteracao_aluno(
        aluno=aluno,
        tipo=AlteracaoAluno.TipoAlteracao.STATUS,
        valor_anterior="Em avaliação",
        valor_novo=aluno.get_status_aluno_display(),
        comentario="Cadastro aprovado pela secretaria.",
        alterado_por=usuario,
    )


@login_required
def aluno_informar_cpf_view(request):
    if request.user.tipo_usuario != User.TipoUsuario.ALUNO:
        raise PermissionDenied("Acesso restrito a alunos.")
    aluno = get_object_or_404(Aluno, pk=request.user.pk)
    if request.method != "POST":
        return redirect("home")

    form = AlunoCpfForm(request.POST, aluno=aluno)
    if form.is_valid():
        aluno.cpf = form.cleaned_data["cpf"]
        aluno.save(update_fields=["cpf"])
        messages.success(request, "CPF cadastrado com sucesso.")
    else:
        messages.error(request, "Não foi possível cadastrar o CPF. " + " ".join(form["cpf"].errors))
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("home")
    return redirect(next_url)


@login_required
def validar_cadastros_alunos_view(request):
    if not _has_gestao_access(request.user):
        raise PermissionDenied("Acesso restrito à coordenação e secretaria.")

    if request.method == "POST":
        aluno = get_object_or_404(
            Aluno,
            pk=request.POST.get("aluno_id"),
            status_aluno=Aluno.StatusAluno.EM_AVALIACAO,
        )
        acao = request.POST.get("acao", "").strip()
        trajetorias_em_homologacao = aluno.trajetorias.filter(status=TrajetoriaAcademica.Status.EM_HOMOLOGACAO)
        if acao == "aprovar":
            _aprovar_cadastro_aluno(aluno=aluno, usuario=request.user)
            messages.success(request, f"Cadastro de {aluno.nome} aprovado.")
        elif acao == "reprovar":
            trajetorias_em_homologacao.update(status=TrajetoriaAcademica.Status.REMOVIDA)
            aluno.status_aluno = Aluno.StatusAluno.DESLIGADO
            aluno.save()
            _registrar_alteracao_aluno(
                aluno=aluno,
                tipo=AlteracaoAluno.TipoAlteracao.STATUS,
                valor_anterior="Em avaliação",
                valor_novo=aluno.get_status_aluno_display(),
                comentario="Cadastro reprovado pela secretaria.",
                alterado_por=request.user,
            )
            messages.success(request, f"Cadastro de {aluno.nome} reprovado.")
        else:
            messages.error(request, "Ação inválida para validação de cadastro.")
        polo_param = request.POST.get("polo", "").strip()
        destino = reverse("validar_cadastros_alunos")
        return redirect(f"{destino}?polo={polo_param}" if polo_param else destino)

    queryset_base = (
        Aluno.objects.filter(status_aluno=Aluno.StatusAluno.EM_AVALIACAO)
        .prefetch_related("trajetorias__orientador", "trajetorias__coorientador")
        .order_by("date_joined", "nome")
    )
    total_pendentes = queryset_base.count()
    polo_id = request.GET.get("polo", "").strip()
    queryset = queryset_base
    if polo_id:
        queryset = queryset.filter(polo_atuacao_id=polo_id)
    total_filtrado = queryset.count()
    pagina = Paginator(queryset, 20).get_page(request.GET.get("page"))
    for aluno in pagina.object_list:
        trajetoria_atual = aluno.trajetoria_ativa()
        if not trajetoria_atual:
            trajetoria_atual = aluno.trajetorias.order_by("-criado_em").first()
        aluno.trajetoria_atual = trajetoria_atual

    return render(
        request,
        "processos/validar_cadastros_alunos.html",
        {
            "alunos_pendentes": pagina.object_list,
            "page_obj": pagina,
            "polos": Polo.objects.filter(ativo=True).order_by("nome"),
            "filtro_polo": polo_id,
            "total_pendentes": total_pendentes,
            "total_filtrado": total_filtrado,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def importar_ingressantes_view(request):
    if not _has_gestao_access(request.user):
        raise PermissionDenied("Acesso restrito à coordenação e secretaria.")

    resultados = None
    if request.method == "POST":
        form = ImportacaoIngressantesForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                resultados = importar_ingressantes(**form.cleaned_data)
            except ValidationError as exc:
                form.add_error("arquivo", exc)
    else:
        form = ImportacaoIngressantesForm()

    return render(
        request,
        "processos/importar_ingressantes.html",
        {"form": form, "resultados": resultados},
    )


@login_required
def alunos_view(request):
    if not _has_gestao_access(request.user):
        raise PermissionDenied("Acesso restrito a coordenadores e servidores.")

    queryset = Aluno.objects.prefetch_related("trajetorias__orientador").order_by("nome")
    nome = request.GET.get("nome", "").strip()
    nivel = request.GET.get("nivel", "").strip().upper()
    ingresso_inicio_raw = request.GET.get("ingresso_inicio", "").strip()
    ingresso_fim_raw = request.GET.get("ingresso_fim", "").strip()
    status = request.GET.get("status", "").strip().upper()
    periodo_sem_matricula_id = request.GET.get("sem_matricula_periodo", "").strip()
    periodo_sem_matricula = None

    if nome:
        queryset = queryset.filter(nome__icontains=nome)
    if nivel:
        queryset = queryset.filter(trajetorias__nivel_curso=nivel)

    ingresso_inicio = ingresso_inicio_raw if _semestre_valido(ingresso_inicio_raw) else ""
    ingresso_fim = ingresso_fim_raw if _semestre_valido(ingresso_fim_raw) else ""

    if ingresso_inicio:
        queryset = queryset.filter(trajetorias__ingresso__gte=ingresso_inicio)
    if ingresso_fim:
        queryset = queryset.filter(trajetorias__ingresso__lte=ingresso_fim)

    if status:
        queryset = queryset.filter(status_aluno=status)

    if periodo_sem_matricula_id:
        periodo_sem_matricula = get_object_or_404(PeriodoLetivo, pk=periodo_sem_matricula_id)
        alunos_sem_matricula_ids = alunos_ativos_sem_matricula(periodo_sem_matricula).values("pk")
        queryset = queryset.filter(pk__in=alunos_sem_matricula_ids)

    # Pagina antes de anotar a trajetoria: a anotacao faz uma consulta por
    # aluno, entao rodar sobre a lista inteira custaria N consultas para exibir
    # 25 linhas.
    pagina = _paginar(request, queryset.distinct().order_by("nome"))
    alunos = list(pagina.object_list)
    for aluno_item in alunos:
        trajetoria_atual = aluno_item.trajetoria_ativa()
        if not trajetoria_atual:
            trajetoria_atual = aluno_item.trajetorias.order_by("-criado_em").first()
        aluno_item.trajetoria_atual = trajetoria_atual

    return render(
        request,
        "processos/alunos_lista.html",
        {
            "alunos": alunos,
            "pagina": pagina,
            "filtro_nome": nome,
            "filtro_nivel": nivel,
            "filtro_ingresso_inicio": ingresso_inicio_raw,
            "filtro_ingresso_fim": ingresso_fim_raw,
            "filtro_status": status,
            "periodos_letivos": PeriodoLetivo.objects.order_by("-nome"),
            "filtro_sem_matricula_periodo": periodo_sem_matricula_id,
            "periodo_sem_matricula": periodo_sem_matricula,
            "total_alunos_filtrados": pagina.paginator.count,
            "status_list": Aluno.StatusAluno.choices,
            "nivel_list": Aluno.NivelCurso.choices,
            "filtros_ativos": _filtros_ativos(
                request,
                {
                    "nome": ("Nome", None),
                    "status": ("Status", dict(Aluno.StatusAluno.choices).get),
                    "nivel": ("Nível", dict(Aluno.NivelCurso.choices).get),
                    "reingressante": ("Reingressante", lambda v: "Sim" if v == "1" else "Não"),
                    "ingresso_inicio": ("Ingresso de", None),
                    "ingresso_fim": ("Ingresso até", None),
                    "sem_matricula_periodo": ("Sem matrícula em", _nome_do_periodo),
                },
            ),
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


def _linhas_trajetoria(trajetoria):
    """Os campos de uma trajetoria, na ordem em que sao lidos.

    O template listava os onze campos na mao, cada um numa linha com o rotulo, o
    valor e o botao que abre o modal correspondente. Isso amarrava tres coisas:
    quais campos existem, como sao formatados e que a tela e uma lista editavel.

    Como dado, a mesma lista serve as duas leituras da tela -- a coordenacao, que
    edita campo por campo, e o aluno e o orientador, que so leem e nao precisam
    de uma coluna de acoes vazia ao lado de cada linha.

    "campo" e o sufixo do id do modal (modal-trajetoria-<campo>-<id>); vazio
    significa que o campo nao e editavel isoladamente.
    """
    sim_nao = lambda valor: "Sim" if valor else "Não"
    linhas = [{"rotulo": "Ingresso", "valor": trajetoria.ingresso or "—", "campo": ""}]

    if trajetoria.usa_prazos_academicos:
        linhas += [
            {
                "rotulo": f"Prazo {trajetoria.qualificacao_label_lower}",
                "valor": trajetoria.prazo_qualificacao or "—",
                "campo": "prazo-qualificacao",
            },
            {"rotulo": "Prazo defesa", "valor": trajetoria.prazo_defesa or "—", "campo": "prazo-defesa"},
            {"rotulo": "Reingressante", "valor": sim_nao(trajetoria.reingressante), "campo": "reingressante"},
            {
                "rotulo": trajetoria.qualificacao_label,
                "valor": sim_nao(trajetoria.isQualificado),
                "campo": "qualificacao",
            },
            {
                "rotulo": "Orientador",
                "valor": trajetoria.orientador.nome if trajetoria.orientador else "—",
                "campo": "orientador",
            },
            {"rotulo": "Coorientador", "valor": trajetoria.coorientador_display or "—", "campo": "coorientador"},
        ]

    if trajetoria.usa_supervisao:
        linhas.append(
            {
                "rotulo": "Supervisor",
                "valor": trajetoria.orientador.nome if trajetoria.orientador else "—",
                "campo": "orientador",
            }
        )

    if trajetoria.usa_conclusao:
        if trajetoria.numero_defesa or trajetoria.data_defesa:
            partes = [trajetoria.numero_defesa or "—"]
            if trajetoria.data_defesa:
                partes.append(trajetoria.data_defesa.strftime("%d/%m/%Y"))
            valor = " · ".join(partes)
        else:
            valor = "—"
        linhas.append({"rotulo": trajetoria.conclusao_label, "valor": valor, "campo": "defesa"})

    if trajetoria.usa_deposito_final:
        linhas.append(
            {
                "rotulo": "Depósito final",
                "valor": sim_nao(trajetoria.deposito_versao_final),
                "campo": "deposito",
            }
        )

    return linhas


@login_required
def aluno_detalhe_view(request, aluno_id):
    can_manage_aluno = _has_gestao_access(request.user)
    is_self_aluno = request.user.tipo_usuario == User.TipoUsuario.ALUNO and request.user.id == aluno_id
    # O orientador entra como leitor. Antes ficava de fora: a tela "Meus
    # Orientandos" listava os alunos dele, mas abrir qualquer um dava 403 --
    # nao havia caminho nenhum para a trajetoria do proprio orientando.
    is_orientador_do_aluno = _e_orientador_do_aluno(request.user, aluno_id)
    if not (can_manage_aluno or is_self_aluno or is_orientador_do_aluno):
        raise PermissionDenied(
            "Esta ficha é acessível ao próprio aluno, ao orientador dele e à coordenação."
        )

    aluno = get_object_or_404(
        Aluno.objects.prefetch_related(
            "trajetorias__orientador",
            "trajetorias__coorientador",
            "trajetorias__estagios_docencia",
        ),
        pk=aluno_id,
    )
    trajetoria_atual = _trajetoria_ativa(aluno)
    can_edit_publicacoes = can_manage_aluno or is_self_aluno
    can_edit_disciplinas = can_manage_aluno

    if request.method == "POST":
        acao = request.POST.get("acao", "").strip()
        # A guarda cita quem pode fazer o que, em vez de so negar o que nao e
        # gestao: o orientador tambem chega ate aqui agora, e entra como leitor.
        # Sem o "is_self_aluno" explicito ele herdaria a edicao de publicacoes.
        if not can_manage_aluno and not (is_self_aluno and acao == "salvar_publicacao"):
            raise PermissionDenied(
                "Somente a coordenação altera esta ficha. O aluno pode editar as próprias publicações."
            )

        if acao == "alterar_dados":
            form = AlunoDadosForm(request.POST, aluno=aluno)
            if form.is_valid():
                anterior = f"nome={aluno.nome};email={aluno.email};matricula={aluno.matricula or '-'}"
                aluno.nome = form.cleaned_data["nome"]
                aluno.email = form.cleaned_data["email"]
                aluno.matricula = form.cleaned_data["matricula"]
                aluno.cpf = form.cleaned_data["cpf"]
                aluno.genero = form.cleaned_data["genero"]
                aluno.sexo_atribuido_nascimento = form.cleaned_data["sexo_atribuido_nascimento"]
                aluno.polo_atuacao = form.cleaned_data["polo_atuacao"]
                aluno.save()
                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
                    valor_anterior=anterior,
                    valor_novo=f"nome={aluno.nome};email={aluno.email};matricula={aluno.matricula or '-'}",
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user,
                )
                messages.success(request, "Dados do aluno atualizados.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível atualizar os dados do aluno.")

        elif acao == "aprovar_cadastro":
            if aluno.status_aluno != Aluno.StatusAluno.EM_AVALIACAO:
                messages.info(request, "O cadastro deste aluno já foi analisado.")
            else:
                _aprovar_cadastro_aluno(aluno=aluno, usuario=request.user)
                messages.success(request, f"Cadastro de {aluno.nome} aprovado.")
            return redirect("aluno_detalhe", aluno_id=aluno.id)

        elif acao == "nova_trajetoria":
            form = TrajetoriaAcademicaForm(request.POST)
            if form.is_valid():
                dados = form.cleaned_data
                if dados["status"] == TrajetoriaAcademica.Status.ATIVA:
                    aluno.trajetorias.filter(status=TrajetoriaAcademica.Status.ATIVA).update(
                        status=TrajetoriaAcademica.Status.CONCLUIDA,
                    )
                trajetoria = TrajetoriaAcademica(
                    aluno=aluno,
                    nivel_curso=dados["nivel_curso"],
                    status=dados["status"],
                    ingresso=dados["ingresso"],
                    prazo_qualificacao=dados["prazo_qualificacao"],
                    prazo_defesa=dados["prazo_defesa"],
                    reingressante=dados["reingressante"],
                    isQualificado=dados["isQualificado"],
                    orientador=dados["orientador"],
                    numero_defesa=dados["numero_defesa"],
                    data_defesa=dados["data_defesa"],
                    deposito_versao_final=dados["deposito_versao_final"],
                )
                tipo_coorientador = dados["tipo_coorientador"]
                if tipo_coorientador == TrajetoriaAcademicaForm.TipoCoorientador.CADASTRADO:
                    trajetoria.coorientador = dados["coorientador"]
                elif tipo_coorientador == TrajetoriaAcademicaForm.TipoCoorientador.EXTERNO:
                    trajetoria.coorientador_externo_nome = dados["coorientador_externo_nome"]
                    trajetoria.coorientador_externo_email = dados["coorientador_externo_email"]
                    trajetoria.coorientador_externo_instituicao = dados["coorientador_externo_instituicao"]
                trajetoria.save()
                _registrar_alteracao_trajetoria(
                    trajetoria,
                    AlteracaoAluno.TipoAlteracao.TRAJETORIA,
                    "-",
                    f"Criada trajetória {trajetoria.get_nivel_curso_display()}",
                    dados["comentario"],
                    request.user,
                )
                messages.success(request, "Trajetória acadêmica cadastrada.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível cadastrar a trajetória acadêmica.")

        elif acao == "editar_trajetoria":
            trajetoria = get_object_or_404(TrajetoriaAcademica, pk=request.POST.get("trajetoria_id"), aluno=aluno)
            form = TrajetoriaAcademicaForm(request.POST)
            if form.is_valid():
                dados = form.cleaned_data
                anterior = (
                    f"nivel={trajetoria.get_nivel_curso_display()};"
                    f"status={trajetoria.get_status_display()};"
                    f"ingresso={trajetoria.ingresso};"
                    f"prazo_qualificacao={trajetoria.prazo_qualificacao or '-'};"
                    f"prazo_defesa={trajetoria.prazo_defesa or '-'};"
                    f"reingressante={'Sim' if trajetoria.reingressante else 'Nao'};"
                    f"Orientador={trajetoria.orientador.nome if trajetoria.orientador else '-'};"
                    f"Coorientador={trajetoria.coorientador_display or '-'}"
                )

                trajetoria.nivel_curso = dados["nivel_curso"]
                trajetoria.status = dados["status"]
                trajetoria.ingresso = dados["ingresso"]
                trajetoria.prazo_qualificacao = dados["prazo_qualificacao"]
                trajetoria.prazo_defesa = dados["prazo_defesa"]
                trajetoria.reingressante = dados["reingressante"]
                trajetoria.isQualificado = dados["isQualificado"]
                trajetoria.orientador = dados["orientador"]
                trajetoria.numero_defesa = dados["numero_defesa"]
                trajetoria.data_defesa = dados["data_defesa"]
                trajetoria.deposito_versao_final = dados["deposito_versao_final"]
                trajetoria.coorientador = None
                trajetoria.coorientador_externo_nome = ""
                trajetoria.coorientador_externo_email = ""
                trajetoria.coorientador_externo_instituicao = ""
                tipo_coorientador = dados["tipo_coorientador"]
                if tipo_coorientador == TrajetoriaAcademicaForm.TipoCoorientador.CADASTRADO:
                    trajetoria.coorientador = dados["coorientador"]
                elif tipo_coorientador == TrajetoriaAcademicaForm.TipoCoorientador.EXTERNO:
                    trajetoria.coorientador_externo_nome = dados["coorientador_externo_nome"]
                    trajetoria.coorientador_externo_email = dados["coorientador_externo_email"]
                    trajetoria.coorientador_externo_instituicao = dados["coorientador_externo_instituicao"]
                trajetoria.save()

                if trajetoria.status == TrajetoriaAcademica.Status.CONCLUIDA and trajetoria.usa_deposito_final:
                    aluno.status_aluno = Aluno.StatusAluno.DEFENDEU
                    aluno.save()

                novo = (
                    f"nivel={trajetoria.get_nivel_curso_display()};"
                    f"status={trajetoria.get_status_display()};"
                    f"ingresso={trajetoria.ingresso};"
                    f"prazo_qualificacao={trajetoria.prazo_qualificacao or '-'};"
                    f"prazo_defesa={trajetoria.prazo_defesa or '-'};"
                    f"reingressante={'Sim' if trajetoria.reingressante else 'Nao'};"
                    f"Orientador={trajetoria.orientador.nome if trajetoria.orientador else '-'};"
                    f"Coorientador={trajetoria.coorientador_display or '-'}"
                )
                _registrar_alteracao_trajetoria(
                    trajetoria,
                    AlteracaoAluno.TipoAlteracao.TRAJETORIA,
                    anterior,
                    novo,
                    dados["comentario"],
                    request.user,
                )
                messages.success(request, "Trajetória acadêmica atualizada.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível atualizar a trajetória acadêmica.")

        elif acao == "iniciar_doutorado":
            form = AlunoIniciarDoutoradoForm(request.POST)
            if form.is_valid():
                ingresso = form.cleaned_data["ingresso"].strip()
                prazo_qualificacao = form.cleaned_data["prazo_qualificacao"].strip()
                prazo_defesa = form.cleaned_data["prazo_defesa"].strip()
                if not all(_semestre_valido(valor) for valor in [ingresso, prazo_qualificacao, prazo_defesa]):
                    messages.error(request, "Informe os semestres no formato YYYY.1 ou YYYY.2.")
                else:
                    aluno.trajetorias.filter(status=TrajetoriaAcademica.Status.ATIVA).update(
                        status=TrajetoriaAcademica.Status.CONCLUIDA,
                    )
                    doutorado = TrajetoriaAcademica.objects.create(
                        aluno=aluno,
                        nivel_curso=Aluno.NivelCurso.DOUTORADO,
                        status=TrajetoriaAcademica.Status.ATIVA,
                        ingresso=ingresso,
                        prazo_qualificacao=prazo_qualificacao,
                        prazo_defesa=prazo_defesa,
                        orientador=form.cleaned_data["orientador"],
                    )
                    _registrar_alteracao_trajetoria(
                        doutorado,
                        AlteracaoAluno.TipoAlteracao.TRAJETORIA,
                        "Mestrado ativo",
                        "Doutorado ativo",
                        form.cleaned_data["comentario"],
                        request.user,
                    )
                    messages.success(request, "Doutorado iniciado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            else:
                messages.error(request, "Não foi possível iniciar o doutorado.")

        elif acao == "alterar_trajetoria_campo":
            trajetoria = get_object_or_404(TrajetoriaAcademica, pk=request.POST.get("trajetoria_id"), aluno=aluno)
            campo = request.POST.get("campo", "").strip()
            comentario = request.POST.get("comentario", "").strip()
            if not comentario:
                messages.error(request, "Informe um comentário para registrar a alteração.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)

            tipo = AlteracaoAluno.TipoAlteracao.TRAJETORIA
            anterior = "-"
            novo = "-"

            if campo == "status":
                anterior = trajetoria.get_status_display()
                trajetoria.status = request.POST.get("status", trajetoria.status)
                novo = trajetoria.get_status_display()
            elif campo == "nivel_curso":
                anterior = trajetoria.get_nivel_curso_display()
                trajetoria.nivel_curso = request.POST.get("nivel_curso", trajetoria.nivel_curso)
                novo = trajetoria.get_nivel_curso_display()
            elif campo == "prazo_qualificacao":
                valor = request.POST.get("prazo_qualificacao", "").strip()
                if valor and not _semestre_valido(valor):
                    messages.error(request, "Informe o prazo no formato YYYY.1 ou YYYY.2.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
                tipo = AlteracaoAluno.TipoAlteracao.PRAZO_QUALIFICACAO
                anterior = trajetoria.prazo_qualificacao or "-"
                trajetoria.prazo_qualificacao = valor
                novo = trajetoria.prazo_qualificacao or "-"
            elif campo == "prazo_defesa":
                valor = request.POST.get("prazo_defesa", "").strip()
                if valor and not _semestre_valido(valor):
                    messages.error(request, "Informe o prazo no formato YYYY.1 ou YYYY.2.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
                tipo = AlteracaoAluno.TipoAlteracao.PRAZO_DEFESA
                anterior = trajetoria.prazo_defesa or "-"
                trajetoria.prazo_defesa = valor
                novo = trajetoria.prazo_defesa or "-"
            elif campo == "reingressante":
                tipo = AlteracaoAluno.TipoAlteracao.REINGRESSO
                anterior = "Sim" if trajetoria.reingressante else "Nao"
                trajetoria.reingressante = "reingressante" in request.POST
                novo = "Sim" if trajetoria.reingressante else "Nao"
            elif campo == "isQualificado":
                tipo = AlteracaoAluno.TipoAlteracao.QUALIFICACAO
                anterior = "Sim" if trajetoria.isQualificado else "Nao"
                trajetoria.isQualificado = "isQualificado" in request.POST
                novo = "Sim" if trajetoria.isQualificado else "Nao"
            elif campo == "orientador":
                tipo = AlteracaoAluno.TipoAlteracao.ORIENTADOR
                anterior = trajetoria.orientador.nome if trajetoria.orientador else "-"
                orientador_id = request.POST.get("orientador") or None
                trajetoria.orientador = User.objects.filter(
                    pk=orientador_id,
                    tipo_usuario=User.TipoUsuario.DOCENTE,
                ).first()
                novo = trajetoria.orientador.nome if trajetoria.orientador else "-"
            elif campo == "coorientador":
                tipo = AlteracaoAluno.TipoAlteracao.COORIENTADOR
                anterior = trajetoria.coorientador_display or "-"
                tipo_coorientador = request.POST.get("tipo_coorientador")
                trajetoria.coorientador = None
                trajetoria.coorientador_externo_nome = ""
                trajetoria.coorientador_externo_email = ""
                trajetoria.coorientador_externo_instituicao = ""
                if tipo_coorientador == TrajetoriaAcademicaForm.TipoCoorientador.CADASTRADO:
                    trajetoria.coorientador = User.objects.filter(
                        pk=request.POST.get("coorientador"),
                        tipo_usuario=User.TipoUsuario.DOCENTE,
                    ).first()
                elif tipo_coorientador == TrajetoriaAcademicaForm.TipoCoorientador.EXTERNO:
                    trajetoria.coorientador_externo_nome = request.POST.get("coorientador_externo_nome", "").strip()
                    trajetoria.coorientador_externo_email = request.POST.get("coorientador_externo_email", "").strip()
                    trajetoria.coorientador_externo_instituicao = request.POST.get(
                        "coorientador_externo_instituicao",
                        "",
                    ).strip()
                novo = trajetoria.coorientador_display or "-"
            elif campo == "defesa":
                tipo = AlteracaoAluno.TipoAlteracao.DEFESA
                anterior = f"numero={trajetoria.numero_defesa or '-'};data={trajetoria.data_defesa or '-'}"
                trajetoria.numero_defesa = request.POST.get("numero_defesa", "").strip()
                trajetoria.data_defesa = parse_date(request.POST.get("data_defesa", ""))
                if trajetoria.numero_defesa and trajetoria.data_defesa:
                    trajetoria.status = TrajetoriaAcademica.Status.CONCLUIDA
                    if trajetoria.usa_deposito_final:
                        aluno.status_aluno = Aluno.StatusAluno.DEFENDEU
                        aluno.save()
                novo = f"numero={trajetoria.numero_defesa or '-'};data={trajetoria.data_defesa or '-'}"
            elif campo == "deposito_versao_final":
                tipo = AlteracaoAluno.TipoAlteracao.DEPOSITO_FINAL
                anterior = "Sim" if trajetoria.deposito_versao_final else "Nao"
                trajetoria.deposito_versao_final = "deposito_versao_final" in request.POST
                novo = "Sim" if trajetoria.deposito_versao_final else "Nao"
            else:
                messages.error(request, "Campo de trajetória inválido.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)

            try:
                trajetoria.save()
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("aluno_detalhe", aluno_id=aluno.id)

            _registrar_alteracao_trajetoria(trajetoria, tipo, anterior, novo, comentario, request.user)
            messages.success(request, "Trajetória acadêmica atualizada.")
            return redirect("aluno_detalhe", aluno_id=aluno.id)

        elif acao == "alterar_status":
            form = AlunoStatusForm(request.POST)
            if form.is_valid():
                anterior = aluno.get_status_aluno_display()
                novo = form.cleaned_data["status_aluno"]
                aluno.status_aluno = novo
                aluno.save()
                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo=AlteracaoAluno.TipoAlteracao.STATUS,
                    valor_anterior=anterior,
                    valor_novo=aluno.get_status_aluno_display(),
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user,
                )
                messages.success(request, "Status do aluno atualizado.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível alterar o status do aluno.")

        elif acao == "alterar_dados":
            form = AlunoDadosForm(request.POST, aluno=aluno)
            if form.is_valid():
                anterior = _dados_aluno_label(aluno)
                aluno.nome = form.cleaned_data["nome"].strip()
                aluno.email = form.cleaned_data["email"].strip()
                aluno.matricula = form.cleaned_data["matricula"].strip()
                try:
                    aluno.save()
                except ValidationError as exc:
                    messages.error(request, exc.message_dict if hasattr(exc, "message_dict") else str(exc))
                else:
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
                        valor_anterior=anterior,
                        valor_novo=_dados_aluno_label(aluno),
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Dados do aluno atualizados.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            else:
                messages.error(request, "Não foi possível atualizar os dados do aluno.")

        elif acao == "editar_trajetoria":
            form = TrajetoriaAcademicaForm(request.POST)
            trajetoria = aluno.trajetorias.filter(id=request.POST.get("trajetoria_id")).first()
            if not trajetoria:
                messages.error(request, "Trajetória acadêmica não encontrada.")
            elif form.is_valid():
                anterior = _trajetoria_label(trajetoria)
                trajetoria.nivel_curso = form.cleaned_data["nivel_curso"]
                trajetoria.status = form.cleaned_data["status"]
                trajetoria.ingresso = form.cleaned_data["ingresso"].strip()
                trajetoria.prazo_qualificacao = form.cleaned_data["prazo_qualificacao"].strip()
                trajetoria.prazo_defesa = form.cleaned_data["prazo_defesa"].strip()
                trajetoria.reingressante = form.cleaned_data["reingressante"]
                trajetoria.isQualificado = form.cleaned_data["isQualificado"]
                trajetoria.orientador = form.cleaned_data["orientador"]
                trajetoria.coorientador = None
                trajetoria.coorientador_externo_nome = ""
                trajetoria.coorientador_externo_email = ""
                trajetoria.coorientador_externo_instituicao = ""
                if form.cleaned_data["tipo_coorientador"] == TrajetoriaAcademicaForm.TipoCoorientador.CADASTRADO:
                    trajetoria.coorientador = form.cleaned_data["coorientador"]
                elif form.cleaned_data["tipo_coorientador"] == TrajetoriaAcademicaForm.TipoCoorientador.EXTERNO:
                    trajetoria.coorientador_externo_nome = form.cleaned_data["coorientador_externo_nome"].strip()
                    trajetoria.coorientador_externo_email = form.cleaned_data["coorientador_externo_email"].strip()
                    trajetoria.coorientador_externo_instituicao = form.cleaned_data[
                        "coorientador_externo_instituicao"
                    ].strip()
                trajetoria.numero_defesa = form.cleaned_data["numero_defesa"].strip()
                trajetoria.data_defesa = form.cleaned_data["data_defesa"]
                trajetoria.deposito_versao_final = form.cleaned_data["deposito_versao_final"]
                try:
                    trajetoria.save()
                except ValidationError as exc:
                    messages.error(request, exc.message_dict if hasattr(exc, "message_dict") else str(exc))
                else:
                    if trajetoria.status == TrajetoriaAcademica.Status.ATIVA:
                        aluno.trajetorias.exclude(id=trajetoria.id).filter(
                            status=TrajetoriaAcademica.Status.ATIVA
                        ).update(status=TrajetoriaAcademica.Status.CONCLUIDA)
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
                        valor_anterior=anterior,
                        valor_novo=_trajetoria_label(trajetoria),
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Trajetória acadêmica atualizada.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            else:
                messages.error(request, "Não foi possível atualizar a trajetória acadêmica.")

        elif acao == "nova_trajetoria":
            form = TrajetoriaAcademicaForm(request.POST)
            if form.is_valid():
                trajetoria = TrajetoriaAcademica(
                    aluno=aluno,
                    nivel_curso=form.cleaned_data["nivel_curso"],
                    status=form.cleaned_data["status"],
                    ingresso=form.cleaned_data["ingresso"].strip(),
                    prazo_qualificacao=form.cleaned_data["prazo_qualificacao"].strip(),
                    prazo_defesa=form.cleaned_data["prazo_defesa"].strip(),
                    reingressante=form.cleaned_data["reingressante"],
                    isQualificado=form.cleaned_data["isQualificado"],
                    orientador=form.cleaned_data["orientador"],
                    numero_defesa=form.cleaned_data["numero_defesa"].strip(),
                    data_defesa=form.cleaned_data["data_defesa"],
                    deposito_versao_final=form.cleaned_data["deposito_versao_final"],
                )
                if form.cleaned_data["tipo_coorientador"] == TrajetoriaAcademicaForm.TipoCoorientador.CADASTRADO:
                    trajetoria.coorientador = form.cleaned_data["coorientador"]
                elif form.cleaned_data["tipo_coorientador"] == TrajetoriaAcademicaForm.TipoCoorientador.EXTERNO:
                    trajetoria.coorientador_externo_nome = form.cleaned_data["coorientador_externo_nome"].strip()
                    trajetoria.coorientador_externo_email = form.cleaned_data["coorientador_externo_email"].strip()
                    trajetoria.coorientador_externo_instituicao = form.cleaned_data[
                        "coorientador_externo_instituicao"
                    ].strip()
                try:
                    trajetoria.save()
                except ValidationError as exc:
                    messages.error(request, exc.message_dict if hasattr(exc, "message_dict") else str(exc))
                else:
                    if trajetoria.status == TrajetoriaAcademica.Status.ATIVA:
                        aluno.trajetorias.exclude(id=trajetoria.id).filter(
                            status=TrajetoriaAcademica.Status.ATIVA
                        ).update(status=TrajetoriaAcademica.Status.CONCLUIDA)
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
                        valor_anterior="Sem trajetória",
                        valor_novo=_trajetoria_label(trajetoria),
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Nova trajetória acadêmica cadastrada.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            else:
                messages.error(request, "Não foi possível cadastrar a trajetória acadêmica.")

        elif acao == "alterar_trajetoria_campo":
            trajetoria = aluno.trajetorias.filter(id=request.POST.get("trajetoria_id")).first()
            campo = request.POST.get("campo", "").strip()
            comentario = request.POST.get("comentario", "").strip()
            if not trajetoria:
                messages.error(request, "Trajetória acadêmica não encontrada.")
            elif not comentario:
                messages.error(request, "Informe um comentário para registrar a alteração.")
            else:
                campo_historico, valor_anterior = _trajetoria_campo_historico(trajetoria, campo)
                try:
                    if campo == "status":
                        form = TrajetoriaStatusForm(request.POST)
                        if not form.is_valid():
                            raise ValidationError(form.errors)
                        trajetoria.status = form.cleaned_data["status"]
                    elif campo == "nivel_curso":
                        nivel = request.POST.get("nivel_curso", "").strip()
                        niveis_validos = dict(Aluno.NivelCurso.choices)
                        if nivel not in niveis_validos:
                            raise ValidationError("Nível de curso inválido.")
                        trajetoria.nivel_curso = nivel
                    elif campo == "prazo_qualificacao":
                        valor = request.POST.get("prazo_qualificacao", "").strip()
                        if valor and not _semestre_valido(valor):
                            raise ValidationError("Informe o prazo no formato YYYY.1 ou YYYY.2.")
                        trajetoria.prazo_qualificacao = valor
                    elif campo == "prazo_defesa":
                        valor = request.POST.get("prazo_defesa", "").strip()
                        if valor and not _semestre_valido(valor):
                            raise ValidationError("Informe o prazo no formato YYYY.1 ou YYYY.2.")
                        trajetoria.prazo_defesa = valor
                    elif campo == "reingressante":
                        trajetoria.reingressante = request.POST.get("reingressante") == "on"
                    elif campo == "isQualificado":
                        trajetoria.isQualificado = request.POST.get("isQualificado") == "on"
                    elif campo == "orientador":
                        orientador_id = request.POST.get("orientador", "").strip()
                        trajetoria.orientador = (
                            User.objects.filter(id=orientador_id, tipo_usuario=User.TipoUsuario.DOCENTE).first()
                            if orientador_id
                            else None
                        )
                    elif campo == "coorientador":
                        tipo_coorientador = request.POST.get("tipo_coorientador", "").strip()
                        trajetoria.coorientador = None
                        trajetoria.coorientador_externo_nome = ""
                        trajetoria.coorientador_externo_email = ""
                        trajetoria.coorientador_externo_instituicao = ""
                        if tipo_coorientador == TrajetoriaAcademicaForm.TipoCoorientador.CADASTRADO:
                            coorientador_id = request.POST.get("coorientador", "").strip()
                            coorientador = User.objects.filter(
                                id=coorientador_id,
                                tipo_usuario=User.TipoUsuario.DOCENTE,
                            ).first()
                            if not coorientador:
                                raise ValidationError("Selecione um docente cadastrado.")
                            trajetoria.coorientador = coorientador
                        elif tipo_coorientador == TrajetoriaAcademicaForm.TipoCoorientador.EXTERNO:
                            externo_nome = request.POST.get("coorientador_externo_nome", "").strip()
                            if not externo_nome:
                                raise ValidationError("Informe o nome do coorientador externo.")
                            trajetoria.coorientador_externo_nome = externo_nome
                            trajetoria.coorientador_externo_email = request.POST.get(
                                "coorientador_externo_email",
                                "",
                            ).strip()
                            trajetoria.coorientador_externo_instituicao = request.POST.get(
                                "coorientador_externo_instituicao",
                                "",
                            ).strip()
                        elif tipo_coorientador != TrajetoriaAcademicaForm.TipoCoorientador.NENHUM:
                            raise ValidationError("Tipo de coorientador inválido.")
                    elif campo == "defesa":
                        trajetoria.numero_defesa = request.POST.get("numero_defesa", "").strip()
                        data_defesa = request.POST.get("data_defesa", "").strip()
                        trajetoria.data_defesa = data_defesa or None
                        if trajetoria.numero_defesa or trajetoria.data_defesa:
                            trajetoria.status = TrajetoriaAcademica.Status.CONCLUIDA
                    elif campo == "deposito_versao_final":
                        trajetoria.deposito_versao_final = request.POST.get("deposito_versao_final") == "on"
                    else:
                        raise ValidationError("Campo de trajetória inválido.")

                    trajetoria.save()
                except ValidationError as exc:
                    messages.error(request, exc.message_dict if hasattr(exc, "message_dict") else str(exc))
                else:
                    if campo == "status" and trajetoria.status == TrajetoriaAcademica.Status.ATIVA:
                        aluno.trajetorias.exclude(id=trajetoria.id).filter(
                            status=TrajetoriaAcademica.Status.ATIVA
                        ).update(status=TrajetoriaAcademica.Status.CONCLUIDA)
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
                        valor_anterior=_trajetoria_campo_label(trajetoria, campo_historico, valor_anterior),
                        valor_novo=_trajetoria_campo_label(
                            trajetoria,
                            campo_historico,
                            _trajetoria_campo_historico(trajetoria, campo)[1],
                        ),
                        comentario=comentario,
                        alterado_por=request.user,
                    )
                    messages.success(request, "Informação da trajetória atualizada.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)

        
        elif acao == "novo_estagio_docencia":
            form = NovoEstagioDocenciaForm(request.POST)
            
            if form.is_valid():
                trajetoria_id = form.cleaned_data["trajetoria_id"]
                trajetoria = get_object_or_404(TrajetoriaAcademica, id=trajetoria_id)

                # Cria o estágio no banco pegando TUDO diretamente do formulário (da tela)
                novo_estagio = EstagioDocencia.objects.create(
                    trajetoria=trajetoria,
                    supervisor=form.cleaned_data["supervisor"].strip(),
                    status=form.cleaned_data["status"],
                    inicio=form.cleaned_data.get("inicio"),
                    termino=form.cleaned_data.get("termino")
                )

                estado_novo = _estagio_docencia_label(novo_estagio)
                
                # Auditoria
                _registrar_alteracao_aluno(
                    aluno=aluno, 
                    tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA, # Ou o tipo específico que usarem
                    valor_anterior="Nenhum estágio",
                    valor_novo=estado_novo,
                    comentario=form.cleaned_data["comentario"].strip(),
                    alterado_por=request.user
                )
                
                messages.success(request, "Novo estágio de docência criado com sucesso.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            else:
                messages.error(request, "Erro ao criar estágio. Verifique os campos.")


        
        elif acao == "alterar_estagio_docencia": # Nome ajustado para bater com o HTML
            form = EstagioDocenciaUpdateForm(request.POST)

            if form.is_valid():
                estagio_id = form.cleaned_data["estagio_id"]
                estagio = get_object_or_404(EstagioDocencia, id=estagio_id)

                # Captura o estado antes usando o padrão da casa
                estado_anterior = _estagio_docencia_label(estagio)

                # Atualiza os campos
                estagio.supervisor = form.cleaned_data["supervisor"].strip()
                estagio.status = form.cleaned_data["status"]
                estagio.inicio = form.cleaned_data["inicio"]
                estagio.termino = form.cleaned_data["termino"]
                estagio.save()

                # Captura o estado depois usando o padrão da casa
                estado_novo = _estagio_docencia_label(estagio)

                # Auditoria
                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
                    valor_anterior=estado_anterior,
                    valor_novo=estado_novo,
                    comentario=form.cleaned_data["comentario"].strip(),
                    alterado_por=request.user,
                )

                messages.success(request, "Estágio de docência atualizado com sucesso.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            else:
                messages.error(request, "Não foi possível atualizar o estágio. Verifique os campos.")

        elif acao == "alterar_qualificacao":
            form = AlunoQualificacaoForm(request.POST)
            if form.is_valid() and _trajetoria_required():
                anterior = "Sim" if trajetoria_atual.isQualificado else "Nao"
                trajetoria_atual.isQualificado = form.cleaned_data["isQualificado"]
                trajetoria_atual.save()
                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo=AlteracaoAluno.TipoAlteracao.QUALIFICACAO,
                    valor_anterior=anterior,
                    valor_novo="Sim" if trajetoria_atual.isQualificado else "Nao",
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user,
                )
                messages.success(request, "Qualificação do aluno atualizada.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível atualizar a qualificação.")

        elif acao == "alterar_prazo_qualificacao":
            form = AlunoPrazoForm(request.POST)
            if form.is_valid() and _trajetoria_required():
                semestre = form.cleaned_data["valor_semestre"].strip()
                if not _semestre_valido(semestre):
                    form.add_error("valor_semestre", "Informe no formato YYYY.1 ou YYYY.2.")
                else:
                    anterior = trajetoria_atual.prazo_qualificacao or "-"
                    trajetoria_atual.prazo_qualificacao = semestre
                    trajetoria_atual.save()
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.PRAZO_QUALIFICACAO,
                        valor_anterior=anterior,
                        valor_novo=trajetoria_atual.prazo_qualificacao,
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Prazo de qualificação atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível atualizar o prazo de qualificação.")

        elif acao == "alterar_prazo_defesa":
            form = AlunoPrazoForm(request.POST)
            if form.is_valid() and _trajetoria_required():
                semestre = form.cleaned_data["valor_semestre"].strip()
                if not _semestre_valido(semestre):
                    form.add_error("valor_semestre", "Informe no formato YYYY.1 ou YYYY.2.")
                else:
                    anterior = trajetoria_atual.prazo_defesa or "-"
                    trajetoria_atual.prazo_defesa = semestre
                    trajetoria_atual.save()
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.PRAZO_DEFESA,
                        valor_anterior=anterior,
                        valor_novo=trajetoria_atual.prazo_defesa,
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Prazo de defesa atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível atualizar o prazo de defesa.")

        elif acao == "registrar_defesa":
            form = AlunoDefesaForm(request.POST)
            if form.is_valid() and _trajetoria_required():
                anterior_numero = trajetoria_atual.numero_defesa or "-"
                anterior_data = trajetoria_atual.data_defesa.isoformat() if trajetoria_atual.data_defesa else "-"
                trajetoria_atual.numero_defesa = form.cleaned_data["numero_defesa"]
                trajetoria_atual.data_defesa = form.cleaned_data["data_defesa"]
                trajetoria_atual.status = TrajetoriaAcademica.Status.CONCLUIDA
                trajetoria_atual.save()
                aluno.status_aluno = Aluno.StatusAluno.DEFENDEU
                aluno.save()
                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo=AlteracaoAluno.TipoAlteracao.DEFESA,
                    valor_anterior=f"numero={anterior_numero};data={anterior_data}",
                    valor_novo=f"numero={trajetoria_atual.numero_defesa};data={trajetoria_atual.data_defesa.isoformat()}",
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user,
                )
                messages.success(request, "Defesa registrada com sucesso.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível registrar a defesa.")

        elif acao == "registrar_deposito_final":
            form = AlunoDepositoFinalForm(request.POST)
            if form.is_valid() and _trajetoria_required():
                if aluno.status_aluno != Aluno.StatusAluno.DEFENDEU:
                    form.add_error("deposito_versao_final", "O aluno precisa estar com status Defendeu.")
                else:
                    anterior = "Sim" if trajetoria_atual.deposito_versao_final else "Nao"
                    trajetoria_atual.deposito_versao_final = form.cleaned_data["deposito_versao_final"]
                    trajetoria_atual.save()
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.DEPOSITO_FINAL,
                        valor_anterior=anterior,
                        valor_novo="Sim" if trajetoria_atual.deposito_versao_final else "Nao",
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Registro de depósito da versão final atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível atualizar o depósito da versão final.")

        elif acao == "salvar_publicacao":
            if not can_edit_publicacoes:
                raise PermissionDenied("Você não pode alterar publicações desta trajetória.")
            trajetoria = get_object_or_404(TrajetoriaAcademica, pk=request.POST.get("trajetoria_id"), aluno=aluno)
            publicacao_id = request.POST.get("publicacao_id")
            publicacao = None
            if publicacao_id:
                publicacao = get_object_or_404(PublicacaoTrajetoria, pk=publicacao_id, trajetoria=trajetoria)
            form = PublicacaoTrajetoriaForm(request.POST, instance=publicacao)
            if form.is_valid():
                publicacao = form.save(commit=False)
                publicacao.trajetoria = trajetoria
                if not publicacao.pk:
                    publicacao.criado_por = request.user
                publicacao.save()
                messages.success(request, "Publicação salva.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível salvar a publicação.")

        elif acao == "salvar_disciplina":
            if not can_edit_disciplinas:
                raise PermissionDenied("Apenas coordenação e secretaria podem alterar disciplinas.")
            trajetoria = get_object_or_404(TrajetoriaAcademica, pk=request.POST.get("trajetoria_id"), aluno=aluno)
            disciplina_id = request.POST.get("disciplina_id")
            disciplina = None
            if disciplina_id:
                disciplina = get_object_or_404(DisciplinaTrajetoria, pk=disciplina_id, trajetoria=trajetoria)
            form = DisciplinaTrajetoriaForm(request.POST, instance=disciplina)
            if form.is_valid():
                disciplina = form.save(commit=False)
                disciplina.trajetoria = trajetoria
                disciplina.save()
                messages.success(request, "Disciplina salva.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Não foi possível salvar a disciplina.")

        elif acao == "registrar_horas_complementares":
            if not can_manage_aluno:
                raise PermissionDenied("Apenas coordenação e secretaria podem registrar horas complementares.")
            trajetoria = get_object_or_404(TrajetoriaAcademica, pk=request.POST.get("trajetoria_id"), aluno=aluno)
            form = HorasComplementaresAdministrativoForm(
                request.POST,
                trajetoria=trajetoria,
                usuario=request.user,
            )
            if form.is_valid():
                lancamento = form.save()
                if lancamento.substitui_lancamento_id:
                    valor_anterior = f"Retificado lançamento {lancamento.substitui_lancamento_id}"
                else:
                    valor_anterior = "-"
                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo=AlteracaoAluno.TipoAlteracao.HORAS_COMPLEMENTARES,
                    valor_anterior=valor_anterior,
                    valor_novo=(
                        f"{lancamento.trajetoria.get_nivel_curso_display()} "
                        f"{lancamento.trajetoria.ingresso}: "
                        f"{lancamento.tipo_atividade.nome}: {lancamento.horas_aprovadas}h aprovadas"
                    ),
                    comentario=lancamento.observacoes_secretaria
                    or lancamento.referencia_decisao
                    or lancamento.justificativa_sem_processo
                    or "Lançamento registrado.",
                    alterado_por=request.user,
                )
                messages.success(request, "Lançamento de horas complementares registrado.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)

    processos_aluno = (
        Processo.objects.select_related("setor_atual")
        .filter(usuario_criado_por=aluno)
        .order_by("-data_criacao")
    )
    # Ativa primeiro, depois as demais da mais recente para a mais antiga: a
    # trajetoria em curso e a que se abre ao entrar na tela, e um aluno pode ter
    # varias (mestrado concluido, doutorado em andamento, um trancamento).
    trajetorias = sorted(
        aluno.trajetorias.select_related("orientador", "coorientador").all(),
        key=lambda t: (t.status != TrajetoriaAcademica.Status.ATIVA, -t.criado_em.timestamp()),
    )
    trajetoria_cards = []
    for trajetoria in trajetorias:
        estagio_cards = [
            {
                "obj": estagio,
                "form": EstagioDocenciaUpdateForm(
                    initial={
                        "estagio_id": estagio.id,
                        "supervisor": estagio.supervisor,
                        "status": estagio.status,
                        "inicio": estagio.inicio,
                        "termino": estagio.termino,
                    }
                ),
            }
            for estagio in trajetoria.estagios_docencia.all()
        ]
        trajetoria_cards.append(
            {
                "obj": trajetoria,
                "linhas": _linhas_trajetoria(trajetoria),
                "esta_ativa": trajetoria.status == TrajetoriaAcademica.Status.ATIVA,
                "form": TrajetoriaAcademicaForm(initial=_trajetoria_form_initial(trajetoria)),
                "resumo_horas_complementares": (
                    None
                    if trajetoria.nivel_curso == Aluno.NivelCurso.POSDOUTORADO
                    else LancamentoHorasComplementares.resumo_trajetoria(trajetoria)
                ),
                "horas_form": HorasComplementaresAdministrativoForm(
                    trajetoria=trajetoria,
                    usuario=request.user,
                ),
                "estagio_cards": estagio_cards,
                "novo_estagio_form": NovoEstagioDocenciaForm(
                    initial={"trajetoria_id": trajetoria.id}
                ),
            }
        )
    dados_form = AlunoDadosForm(
        aluno=aluno,
        initial={
            "nome": aluno.nome,
            "email": aluno.email,
            "matricula": aluno.matricula,
            "cpf": aluno.cpf,
            "genero": aluno.genero,
            "sexo_atribuido_nascimento": aluno.sexo_atribuido_nascimento,
            "polo_atuacao": aluno.polo_atuacao,
        },
    )
    nova_trajetoria_form = TrajetoriaAcademicaForm(
        initial={
            "status": TrajetoriaAcademica.Status.ATIVA,
            "tipo_coorientador": TrajetoriaAcademicaForm.TipoCoorientador.NENHUM,
        }
    )
    alteracoes_display = [
        {
            "obj": alteracao,
            "trajetoria": alteracao.valor_novo.split(":", 1)[0] if ":" in alteracao.valor_novo else "Aluno",
            "alteracao": alteracao.get_tipo_display(),
        }
        for alteracao in aluno.alteracoes.select_related("alterado_por").all()
    ]
    return render(
        request,
        "processos/aluno_detalhe.html",
        {
            "aluno": aluno,
            # A mesma tela atende tres leitores com expectativas diferentes: o
            # proprio aluno (que a acessa como "Minha Trajetoria"), o orientador
            # e a coordenacao. Sem saber quem esta lendo, o template tratava
            # todos como coordenacao -- o aluno via o proprio nome como se fosse
            # uma ficha de terceiro, com titulo "Aluno | Coordenacao" na aba e um
            # "Voltar para alunos" que leva a uma listagem proibida para ele.
            "is_self_aluno": is_self_aluno,
            "is_orientador_do_aluno": is_orientador_do_aluno,
            "trajetoria_atual": trajetoria_atual,
            "trajetoria_cards": trajetoria_cards,
            "processos_aluno": processos_aluno,
            "alteracoes_aluno": aluno.alteracoes.select_related("alterado_por").all(),
            "alteracoes_display": alteracoes_display,
            "dados_form": dados_form,
            "nova_trajetoria_form": nova_trajetoria_form,
            "status_form": AlunoStatusForm(initial={"status_aluno": aluno.status_aluno}),
            "qualificacao_form": AlunoQualificacaoForm(
                initial={"isQualificado": trajetoria_atual.isQualificado if trajetoria_atual else False}
            ),
            "prazo_qualificacao_form": AlunoPrazoForm(
                initial={"valor_semestre": trajetoria_atual.prazo_qualificacao if trajetoria_atual else ""}
            ),
            "prazo_defesa_form": AlunoPrazoForm(
                initial={"valor_semestre": trajetoria_atual.prazo_defesa if trajetoria_atual else ""}
            ),
            "defesa_form": AlunoDefesaForm(
                initial={
                    "numero_defesa": trajetoria_atual.numero_defesa if trajetoria_atual else "",
                    "data_defesa": trajetoria_atual.data_defesa if trajetoria_atual else None,
                }
            ),
            "deposito_final_form": AlunoDepositoFinalForm(
                initial={"deposito_versao_final": trajetoria_atual.deposito_versao_final if trajetoria_atual else False}
            ),
            "publicacao_form": PublicacaoTrajetoriaForm(),
            "disciplina_form": DisciplinaTrajetoriaForm(),
            "can_manage_aluno": can_manage_aluno,
            "can_edit_publicacoes": can_edit_publicacoes,
            "can_edit_disciplinas": can_edit_disciplinas,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def caixa_processos_view(request):
    if not _can_view_caixa(request.user):
        raise PermissionDenied("Acesso restrito a docentes e servidores.")

    setores_caixa = _setores_caixa(request.user)
    selected_caixa = request.GET.get("caixa", "").strip()
    status_caixa = request.GET.get("status_caixa", "").strip().upper()
    if status_caixa not in {"AGUARDANDO_CIENCIA", "EM_ANALISE"}:
        status_caixa = "EM_ANALISE"

    opcoes_caixa = [{"value": str(setor.id), "label": setor.nome} for setor in setores_caixa]
    selected_setor_ids = [setor.id for setor in setores_caixa]
    if selected_caixa:
        try:
            selected_id = int(selected_caixa)
        except ValueError:
            selected_id = None
        if selected_id in selected_setor_ids:
            selected_setor_ids = [selected_id]
        else:
            selected_caixa = ""

    processos_caixa = (
        Processo.objects.select_related("usuario_criado_por", "setor_atual")
        .filter(setor_atual_id__in=selected_setor_ids)
        .filter(
            status__in=[
                Processo.StatusProcesso.EM_ANALISE,
                Processo.StatusProcesso.AGUARDANDO_CIENCIA,
            ]
        )
        .filter(status=status_caixa)
        .order_by("-data_criacao")
    )
    return render(
        request,
        "processos/caixa_processos.html",
        {
            "processos": processos_caixa,
            "nomes_setores_caixa": [setor.nome for setor in setores_caixa],
            "nomes_setores_caixa_texto": ", ".join(
                setor.nome for setor in setores_caixa if setor.id in selected_setor_ids
            ),
            "opcoes_caixa": opcoes_caixa,
            "selected_caixa": selected_caixa,
            "status_caixa": status_caixa,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def processo_detalhe_view(request, processo_id):
    processo = get_object_or_404(
        Processo.objects.select_related("usuario_criado_por", "setor_atual")
        .prefetch_related(
            "solicitacoes_banca_anexadas__aluno",
            "solicitacoes_banca_anexadas__trajetoria",
            "solicitacoes_banca_anexadas__trajetoria__disciplinas",
            "solicitacoes_banca_anexadas__trajetoria__estagios_docencia",
            "solicitacoes_banca_anexadas__trajetoria__lancamentos_horas_complementares",
            "solicitacoes_banca_anexadas__trajetoria__publicacoes",
            "solicitacoes_banca_anexadas__docente",
            "solicitacoes_banca_anexadas__finalizado_por",
            "solicitacoes_banca_anexadas__membros",
            "documentos__enviado_por",
            "comentarios__autor",
            "manifestacoes__responsavel",
            "manifestacoes__solicitado_por",
            Prefetch(
                "tramitacoes",
                queryset=TramitacaoProcesso.objects.select_related(
                    "setor_origem",
                    "setor_destino",
                    "encaminhado_por",
                ).order_by("-data_encaminhamento"),
                to_attr="tramitacoes_historico",
            ),
        ),
        id=processo_id,
    )
    if not _can_view_processo_detalhe(request.user, processo):
        raise PermissionDenied("Acesso restrito ao dono do processo ou perfis de gestão.")

    nomes_setores_caixa = _nomes_setores_caixa(request.user)
    can_manage_in_caixa = _can_manage_caixa_actions(request.user, processo)
    tramitacao_para_requerente = (
        processo.tramitacoes.filter(setor_destino__nome="Requerente")
        .select_related("setor_origem")
        .order_by("-data_encaminhamento")
        .first()
    )
    setor_solicitante = tramitacao_para_requerente.setor_origem if tramitacao_para_requerente else None
    can_manage_requerente = processo.setor_atual.nome == "Requerente" and _is_requerente_do_processo(
        request.user, processo
    )
    orientador_responsavel = processo.obter_orientador_responsavel()
    pendente_ciente = processo.manifestacoes.filter(
        tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
        status=ManifestacaoProcesso.StatusManifestacao.PENDENTE,
    ).first()
    can_solicitar_ciente = can_manage_in_caixa and orientador_responsavel is not None and not pendente_ciente
    can_manifestar_ciente = bool(
        pendente_ciente
        and request.user.id == pendente_ciente.responsavel_id
        and request.user.tipo_usuario == User.TipoUsuario.DOCENTE
    )
    can_comment_pleno = _is_docente(request.user) and _is_processo_no_pleno(processo)
    can_add_documento = can_manage_in_caixa or can_manage_requerente
    can_encaminhar_processo = can_manage_in_caixa or (can_manage_requerente and setor_solicitante is not None)
    can_finalizar_processo = can_manage_in_caixa and not processo.esta_finalizado
    can_manage_processo_actions = can_add_documento or can_encaminhar_processo
    aluno_horas_complementares = None
    trajetoria_horas_complementares = None
    if processo.tipo == Processo.TipoProcesso.HORAS_COMPLEMENTARES:
        aluno_horas_complementares = (
            Aluno.objects.prefetch_related("trajetorias").filter(pk=processo.usuario_criado_por_id).first()
        )
        if aluno_horas_complementares:
            trajetoria_horas_complementares = (
                aluno_horas_complementares.trajetoria_ativa()
                or aluno_horas_complementares.trajetorias.order_by("-criado_em").first()
            )
    termo_finalizacao_lower = (processo.termo_finalizacao or "").lower()
    processo_horas_registravel = not processo.esta_finalizado or (
        "deferid" in termo_finalizacao_lower
        and "indeferid" not in termo_finalizacao_lower
        and "arquivad" not in termo_finalizacao_lower
    )
    can_registrar_horas_complementares = bool(
        can_manage_in_caixa and aluno_horas_complementares and trajetoria_horas_complementares and processo_horas_registravel
    )
    open_documento_modal = False
    open_encaminhamento_modal = False
    open_ciente_modal = False
    open_finalizar_modal = False
    open_horas_modal = False
    solicitar_ciente_form = SolicitarCienteOrientadorForm()
    manifestar_ciente_form = ManifestarCienteOrientadorForm()
    finalizar_form = FinalizarProcessoForm()
    horas_complementares_form = LancamentoHorasComplementaresForm(
        aluno=aluno_horas_complementares,
        processo=processo,
        usuario=request.user,
    )

    if request.method == "POST":
        if "registrar_horas_complementares" in request.POST:
            if not can_registrar_horas_complementares:
                raise PermissionDenied("Você não pode registrar horas complementares neste processo.")
            horas_complementares_form = LancamentoHorasComplementaresForm(
                request.POST,
                aluno=aluno_horas_complementares,
                processo=processo,
                usuario=request.user,
            )
            if horas_complementares_form.is_valid():
                lancamento = horas_complementares_form.save()
                if lancamento.substitui_lancamento_id:
                    valor_anterior = f"Retificado lançamento {lancamento.substitui_lancamento_id}"
                else:
                    valor_anterior = "-"
                _registrar_alteracao_aluno(
                    aluno=aluno_horas_complementares,
                    tipo=AlteracaoAluno.TipoAlteracao.HORAS_COMPLEMENTARES,
                    valor_anterior=valor_anterior,
                    valor_novo=f"{lancamento.tipo_atividade.nome}: {lancamento.horas_aprovadas}h aprovadas",
                    comentario=lancamento.observacoes_secretaria or lancamento.referencia_decisao or "Lançamento registrado.",
                    alterado_por=request.user,
                )
                messages.success(request, "Lançamento de horas complementares registrado.")
                return redirect("processo_detalhe", processo_id=processo.id)
            open_horas_modal = True

        elif "cancelar_horas_complementares" in request.POST:
            if not can_registrar_horas_complementares:
                raise PermissionDenied("Você não pode cancelar lançamentos neste processo.")
            lancamento = get_object_or_404(
                LancamentoHorasComplementares,
                pk=request.POST.get("lancamento_id"),
                trajetoria__aluno=aluno_horas_complementares,
                processo_origem=processo,
            )
            justificativa = request.POST.get("justificativa_cancelamento", "").strip()
            try:
                lancamento.cancelar(usuario=request.user, justificativa=justificativa)
            except ValidationError as exc:
                messages.error(request, str(exc))
            else:
                _registrar_alteracao_aluno(
                    aluno=aluno_horas_complementares,
                    tipo=AlteracaoAluno.TipoAlteracao.HORAS_COMPLEMENTARES,
                    valor_anterior=f"{lancamento.tipo_atividade.nome}: {lancamento.horas_aprovadas}h",
                    valor_novo="Lançamento cancelado",
                    comentario=justificativa,
                    alterado_por=request.user,
                )
                messages.success(request, "Lançamento cancelado.")
                return redirect("processo_detalhe", processo_id=processo.id)

        elif "acao_rapida" in request.POST and can_manage_in_caixa:
            acao_rapida = (request.POST.get("acao_rapida") or "").strip()
            if acao_rapida == "deferir":
                processo.deferir()
                messages.success(request, "Processo deferido.")
                send_email_conclusao_aluno.delay(processo.id)
                send_email_conclusao_orientador.delay(processo.id)
                return redirect("processo_detalhe", processo_id=processo.id)
            if acao_rapida == "indeferir":
                processo.indeferir()
                messages.success(request, "Processo indeferido.")
                send_email_conclusao_aluno.delay(processo.id)
                send_email_conclusao_orientador.delay(processo.id)
                return redirect("processo_detalhe", processo_id=processo.id)
            if acao_rapida == "arquivar":
                processo.finalizar(
                    termo_finalizacao="Processo arquivado.",
                    status_final=Processo.StatusProcesso.FINALIZADO,
                )
                messages.success(request, "Processo arquivado.")
                send_email_conclusao_aluno.delay(processo.id)
                send_email_conclusao_orientador.delay(processo.id)
                return redirect("processo_detalhe", processo_id=processo.id)
            if acao_rapida == "solicitar_correcao":
                processo.status = Processo.StatusProcesso.AGUARDANDO_DOCUMENTO
                processo.save(update_fields=["status", "atualizado_em"])
                messages.success(request, "Correção solicitada ao aluno.")
                return redirect("processo_detalhe", processo_id=processo.id)

        elif "adicionar_documento" in request.POST:
            if not can_add_documento:
                raise PermissionDenied("Você não pode adicionar documento neste processo.")
            documento_form = DocumentoCadastroForm(request.POST, request.FILES)
            if can_manage_in_caixa:
                encaminhamento_form = EncaminhamentoForm(current_setor_id=processo.setor_atual_id)
            else:
                encaminhamento_form = EncaminhamentoForm(
                    current_setor_id=processo.setor_atual_id,
                    allowed_setor_ids=[setor_solicitante.id] if setor_solicitante else [],
                )
            if documento_form.is_valid():
                processo.adicionar_documento(
                    titulo=documento_form.cleaned_data["titulo"],
                    arquivo=documento_form.cleaned_data["arquivo"],
                    restricao_tipo=documento_form.cleaned_data["restricao_tipo"],
                    tipo_documento=documento_form.cleaned_data["tipo_documento"] or "",
                    enviado_por=request.user,
                )
                messages.success(request, "Documento adicionado com sucesso.")
                return redirect("processo_detalhe", processo_id=processo.id)
            open_documento_modal = True

        elif "solicitar_ciente_orientador" in request.POST:
            if not can_solicitar_ciente:
                raise PermissionDenied("Você não pode solicitar ciente do orientador neste processo.")
            solicitar_ciente_form = SolicitarCienteOrientadorForm(request.POST)
            if solicitar_ciente_form.is_valid():
                try:
                    manifestacao = processo.solicitar_ciente_orientador(
                        solicitado_por=request.user,
                        mensagem_solicitacao=solicitar_ciente_form.cleaned_data["mensagem_solicitacao"],
                    )
                except ValidationError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, "Solicitação de ciente do orientador registrada.")
                    send_email_solicitacao_ciencia.delay(manifestacao.id)
                    return redirect("processo_detalhe", processo_id=processo.id)
            open_ciente_modal = True

        elif "manifestar_ciente_orientador" in request.POST:
            if not can_manifestar_ciente:
                raise PermissionDenied("Você não pode se manifestar neste ciente.")
            manifestar_ciente_form = ManifestarCienteOrientadorForm(request.POST)
            acao = (request.POST.get("acao_ciente") or "").strip().lower()
            if manifestar_ciente_form.is_valid():
                status_anterior_texto = processo.get_status_display()#salva status
                setor_anterior_id = processo.setor_atual_id if processo.setor_atual else None#salva setor
                try:
                    pendente_ciente.registrar_manifestacao(
                        autor=request.user,
                        aceito=(acao == "ciente"),
                        mensagem=manifestar_ciente_form.cleaned_data["mensagem_manifestacao"],
                    )
                except ValidationError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, "Manifestação registrada com sucesso.")

                    processo.refresh_from_db()
                    status_atual_texto = processo.get_status_display()
                    setor_atual_id = processo.setor_atual_id if processo.setor_atual else None

                    if setor_anterior_id == setor_atual_id and status_anterior_texto != status_atual_texto: #se mudou de status, mas não de setor
                        send_email_status_atualizado.delay(
                            processo.id, 
                            status_anterior_texto, 
                            status_atual_texto
                        )

                    return redirect("processo_detalhe", processo_id=processo.id)
            open_ciente_modal = True

        elif "encaminhar_processo" in request.POST:
            if not can_encaminhar_processo:
                raise PermissionDenied("Você não pode encaminhar este processo.")
            documento_form = DocumentoCadastroForm()
            if can_manage_in_caixa:
                encaminhamento_form = EncaminhamentoForm(
                    request.POST,
                    current_setor_id=processo.setor_atual_id,
                )
            else:
                encaminhamento_form = EncaminhamentoForm(
                    request.POST,
                    current_setor_id=processo.setor_atual_id,
                    allowed_setor_ids=[setor_solicitante.id] if setor_solicitante else [],
                )
            if encaminhamento_form.is_valid():
                setor_destino = (
                    encaminhamento_form.cleaned_data["setor_destino"]
                    if can_manage_in_caixa
                    else setor_solicitante
                )
                status_resultante = (
                    Processo.StatusProcesso.AGUARDANDO_DOCUMENTO
                    if setor_destino and setor_destino.nome == "Requerente"
                    else Processo.StatusProcesso.EM_ANALISE
                )
                prazo_limite = encaminhamento_form.cleaned_data.get("prazo_limite")
                try:
                    despacho_texto = encaminhamento_form.cleaned_data["despacho"]
                    processo.encaminhar(
                        setor_destino=setor_destino,
                        encaminhado_por=request.user,
                        observacao=despacho_texto,
                        status_resultante=status_resultante,
                        prazo_limite=prazo_limite,  
                    )
                except ValidationError as exc:
                    messages.error(request, str(exc))
                else:
                    if setor_destino and _is_setor_pleno_nome(setor_destino.nome):
                        prazo_pleno = encaminhamento_form.cleaned_data.get("prazo_pleno")
                        if prazo_pleno:
                            processo.prazo_limite = prazo_pleno
                            processo.save(update_fields=["prazo_limite", "atualizado_em"])
                    messages.success(request, "Processo encaminhado com sucesso.")
                    if setor_destino and setor_destino.nome == "Requerente":
                        send_email_devolucao_requerente.delay(processo.id, despacho_texto)
                    else:
                        send_email_movimentacao_aluno.delay(processo.id, f"Encaminhado para o setor: {setor_destino.nome}")
                        if setor_destino and _is_setor_pleno_nome(setor_destino.nome):
                            send_email_movimentacao_pleno.delay(processo.id)
                        send_email_mudanca_setor.delay(processo.id)

                    send_email_movimentacao_orientador.delay(processo.id, f"Encaminhado para o setor: {setor_destino.nome}")
                    return redirect("processo_detalhe", processo_id=processo.id)
            open_encaminhamento_modal = True

        elif "finalizar_processo" in request.POST:
            if not can_finalizar_processo:
                raise PermissionDenied("Você não pode finalizar este processo.")
            finalizar_form = FinalizarProcessoForm(request.POST)
            documento_form = DocumentoCadastroForm()
            if can_manage_in_caixa:
                encaminhamento_form = EncaminhamentoForm(current_setor_id=processo.setor_atual_id)
            else:
                encaminhamento_form = EncaminhamentoForm(
                    current_setor_id=processo.setor_atual_id,
                    allowed_setor_ids=[setor_solicitante.id] if setor_solicitante else [],
                )
            if finalizar_form.is_valid():
                try:
                    processo.finalizar(
                        termo_finalizacao=finalizar_form.cleaned_data["termo_finalizacao"],
                        status_final=Processo.StatusProcesso.FINALIZADO,
                    )
                except ValidationError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, "Processo finalizado com sucesso.")
                    send_email_conclusao_aluno.delay(processo.id)
                    send_email_conclusao_orientador.delay(processo.id)
                    return redirect("processo_detalhe", processo_id=processo.id)
            open_finalizar_modal = True

        elif "remover_arquivo_documento" in request.POST:
            documento_form = DocumentoCadastroForm()
            if can_manage_in_caixa:
                encaminhamento_form = EncaminhamentoForm(current_setor_id=processo.setor_atual_id)
            else:
                encaminhamento_form = EncaminhamentoForm(
                    current_setor_id=processo.setor_atual_id,
                    allowed_setor_ids=[setor_solicitante.id] if setor_solicitante else [],
                )
            documento_id = request.POST.get("documento_id")
            motivo_remocao = (request.POST.get("motivo_remocao") or "").strip()
            documento = processo.documentos.filter(id=documento_id).first()
            if not documento:
                messages.error(request, "Documento não encontrado para remoção.")
            else:
                pode_remover = (
                    request.user.id == documento.enviado_por_id or _can_manage_restricted_docs(request.user)
                )
                if not pode_remover:
                    raise PermissionDenied("Você não tem permissão para remover este arquivo.")
                try:
                    documento.remover_arquivo(removido_por=request.user, motivo=motivo_remocao)
                except ValidationError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, "Arquivo removido com sucesso.")
                    return redirect("processo_detalhe", processo_id=processo.id)
# Implementada a transição de estado de "Em Análise" para "Em Debate"
        elif "adicionar_comentario" in request.POST:
            if not can_comment_pleno:
                raise PermissionDenied("Apenas docentes podem comentar processos do Pleno.")
            comentario_form = ComentarioProcessoForm(request.POST)
            if comentario_form.is_valid():
                comentario_intervencao = ComentarioProcesso.objects.create(
                    processo=processo,
                    autor=request.user,
                    anonimo=comentario_form.cleaned_data["anonimo"],
                    texto=comentario_form.cleaned_data["texto"],
                )
                if _is_processo_no_pleno(processo):
                    send_email_processo_comentado_pleno.delay(processo.id, comentario_intervencao.id)
                    # Issue 2.2.2: interrompe aprovação automática e marca como EM_DEBATE
                    if processo.status not in {
                        Processo.StatusProcesso.FINALIZADO,
                        Processo.StatusProcesso.EM_DEBATE,
                    }:
                        processo.status = Processo.StatusProcesso.EM_DEBATE
                        processo.save(update_fields=["status", "atualizado_em"])
                messages.success(request, "Comentário adicionado. Processo marcado como Em Debate.")
                return redirect("processo_detalhe", processo_id=processo.id)

        else:
            documento_form = DocumentoCadastroForm()
            if can_manage_in_caixa:
                encaminhamento_form = EncaminhamentoForm(current_setor_id=processo.setor_atual_id)
            else:
                encaminhamento_form = EncaminhamentoForm(
                    current_setor_id=processo.setor_atual_id,
                    allowed_setor_ids=[setor_solicitante.id] if setor_solicitante else [],
                )
            finalizar_form = FinalizarProcessoForm()

    else:
        documento_form = DocumentoCadastroForm()
        if can_manage_in_caixa:
            encaminhamento_form = EncaminhamentoForm(current_setor_id=processo.setor_atual_id)
        else:
            encaminhamento_form = EncaminhamentoForm(
                current_setor_id=processo.setor_atual_id,
                allowed_setor_ids=[setor_solicitante.id] if setor_solicitante else [],
            )
        finalizar_form = FinalizarProcessoForm()

    if request.method != "POST" or "adicionar_comentario" not in request.POST:
        comentario_form = ComentarioProcessoForm()

    documentos_exibicao = []
    for documento in processo.documentos.all():
        documentos_exibicao.append(
            {
                "obj": documento,
                "can_view_file": documento.pode_visualizar_arquivo(request.user),
                "can_remove_file": (
                    not documento.arquivo_removido
                    and bool(documento.arquivo)
                    and (
                        request.user.id == documento.enviado_por_id
                        or _can_manage_restricted_docs(request.user)
                    )
                ),
            }
        )
    solicitacoes_banca_display = [
        {
            "obj": solicitacao,
            "checklist": _resumo_checklist_banca(solicitacao.trajetoria),
            "publicacoes": solicitacao.trajetoria.publicacoes.all(),
        }
        for solicitacao in processo.solicitacoes_banca_anexadas.all()
    ]
    resumo_horas_complementares = (
        LancamentoHorasComplementares.resumo_trajetoria(trajetoria_horas_complementares)
        if trajetoria_horas_complementares
        else None
    )
    regras_horas_complementares = []
    if aluno_horas_complementares:
        trajetorias_horas = aluno_horas_complementares.trajetorias.order_by("-criado_em")
        for item_trajetoria in trajetorias_horas:
            norma_trajetoria = LancamentoHorasComplementares.norma_para_trajetoria(item_trajetoria)
            if not norma_trajetoria:
                continue
            tipos_trajetoria = horas_complementares_form.fields["tipo_atividade"].queryset.filter(
                norma=norma_trajetoria,
            )
            for tipo in tipos_trajetoria:
                exemplo = LancamentoHorasComplementares(
                    trajetoria=item_trajetoria,
                    processo_origem=processo,
                    tipo_atividade=tipo,
                    norma=tipo.norma,
                    grupo_limite=tipo.grupo_limite,
                    quantidade=1,
                    unidade_quantidade=tipo.unidade_calculo,
                    horas_aprovadas=0,
                    criado_por=request.user,
                )
                regras_horas_complementares.append(
                    {
                        "trajetoria_id": str(item_trajetoria.id),
                        "id": str(tipo.id),
                        "horas_por_unidade": str(tipo.horas_por_unidade),
                        "unidade": tipo.unidade_calculo,
                        "maximo": "" if exemplo.maximo_aprovavel() is None else str(exemplo.maximo_aprovavel()),
                    }
                )
    lancamentos_horas_processo = (
        processo.lancamentos_horas_complementares.select_related(
            "trajetoria",
            "trajetoria__aluno",
            "tipo_atividade",
            "criado_por",
        ).all()
        if trajetoria_horas_complementares
        else []
    )

    return render(
        request,
        "processos/processo_detalhe.html",
        {
            "processo": processo,
            "tramitacoes_historico": processo.tramitacoes_historico,
            "documentos_exibicao": documentos_exibicao,
            "solicitacoes_banca_display": solicitacoes_banca_display,
            "can_manage_in_caixa": can_manage_in_caixa,
            "can_manage_requerente": can_manage_requerente,
            "can_manage_processo_actions": can_manage_processo_actions,
            "can_add_documento": can_add_documento,
            "can_encaminhar_processo": can_encaminhar_processo,
            "can_finalizar_processo": can_finalizar_processo,
            "can_registrar_horas_complementares": can_registrar_horas_complementares,
            "aluno_horas_complementares": aluno_horas_complementares,
            "trajetoria_horas_complementares": trajetoria_horas_complementares,
            "resumo_horas_complementares": resumo_horas_complementares,
            "regras_horas_complementares": regras_horas_complementares,
            "lancamentos_horas_processo": lancamentos_horas_processo,
            "setor_solicitante": setor_solicitante,
            "orientador_responsavel": orientador_responsavel,
            "pendente_ciente": pendente_ciente,
            "can_solicitar_ciente": can_solicitar_ciente,
            "can_manifestar_ciente": can_manifestar_ciente,
            "solicitar_ciente_form": solicitar_ciente_form,
            "manifestar_ciente_form": manifestar_ciente_form,
            "can_comment_pleno": can_comment_pleno,
            "comentario_form": comentario_form,
            "nomes_setores_caixa_texto": ", ".join(nomes_setores_caixa) if nomes_setores_caixa else "-",
            "documento_form": documento_form,
            "encaminhamento_form": encaminhamento_form,
            "finalizar_form": finalizar_form,
            "horas_complementares_form": horas_complementares_form,
            "open_documento_modal": open_documento_modal,
            "open_encaminhamento_modal": open_encaminhamento_modal,
            "open_ciente_modal": open_ciente_modal,
            "open_finalizar_modal": open_finalizar_modal,
            "open_horas_modal": open_horas_modal,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def novo_processo_view(request):
    if not _can_add_processo(request.user):
        raise PermissionDenied("Seu cadastro precisa estar aprovado para abrir processo.")

    if request.method == "POST":
        form = ProcessoAberturaForm(request.POST, request.FILES, user=request.user)
        doc_indices = set()
        for key in request.POST.keys():
            match = re.match(r"^doc_(\d+)_titulo$", key)
            if match:
                doc_indices.add(int(match.group(1)))

        documentos_forms = []
        for idx in sorted(doc_indices):
            titulo = (request.POST.get(f"doc_{idx}_titulo") or "").strip()
            tipo_documento = (request.POST.get(f"doc_{idx}_tipo_documento") or "").strip()
            restricao_tipo = (request.POST.get(f"doc_{idx}_restricao_tipo") or "").strip()
            arquivo = request.FILES.get(f"doc_{idx}_arquivo")

            if not (titulo and tipo_documento and restricao_tipo and arquivo):
                continue

            documento_form = DocumentoCadastroForm(
                {
                    "titulo": titulo,
                    "tipo_documento": tipo_documento,
                    "restricao_tipo": restricao_tipo,
                },
                {"arquivo": arquivo},
            )
            documentos_forms.append(documento_form)

        documentos_validos = True
        for documento_form in documentos_forms:
            if not documento_form.is_valid():
                documentos_validos = False

        if form.is_valid() and documentos_validos:
            setor_secretaria = Setor.objects.filter(nome="Secretaria PPGEC", ativo=True).first()
            if not setor_secretaria:
                messages.error(
                    request,
                    "Setor inicial 'Secretaria PPGEC' não encontrado. Contate o administrador.",
                )
            else:
                processo = form.save(commit=False)
                processo.usuario_criado_por = request.user
                processo.setor_atual = setor_secretaria
                processo.status = Processo.StatusProcesso.EM_ANALISE
                processo.save()

                for documento_form in documentos_forms:
                    processo.adicionar_documento(
                        titulo=documento_form.cleaned_data["titulo"],
                        arquivo=documento_form.cleaned_data["arquivo"],
                        tipo_documento=documento_form.cleaned_data["tipo_documento"],
                        restricao_tipo=documento_form.cleaned_data["restricao_tipo"],
                        enviado_por=request.user,
                    )

                send_email_novo_processo_aluno.delay(processo.id)
                send_email_novo_processo_orientador.delay(processo.id)
                send_email_novo_processo_secretaria.delay(processo.id)

                messages.success(request, f"Processo {processo.numero} aberto com sucesso.")
                return redirect("home")
        elif not documentos_validos:
            for documento_form in documentos_forms:
                for errors in documento_form.errors.values():
                    for error in errors:
                        messages.error(request, f"Documento inválido: {error}")
    else:
        form = ProcessoAberturaForm(user=request.user)

    return render(
        request,
        "processos/novo_processo.html",
        {
            "form": form,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


def _can_use_reservas(user):
    return user.is_authenticated and (
        user.tipo_usuario in {
            User.TipoUsuario.DOCENTE,
            User.TipoUsuario.SERVIDOR,
        }
        or _is_secretaria_member(user)
    )


def _reservas_base_context():
    return {
        "polos": Polo.objects.filter(ativo=True).order_by("nome"),
        "salas": Sala.objects.filter(ativa=True, polo__ativo=True).select_related("polo").order_by("polo__nome", "nome"),
        "docentes": User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE, is_active=True).order_by("nome"),
        "tipos_reserva": ReservaAmbiente.TipoReserva.choices,
        "status_reserva": ReservaAmbiente.StatusReserva.choices,
    }


def _reservas_filtradas(request):
    reservas = ReservaAmbiente.objects.select_related("sala", "sala__polo", "docente", "criado_por", "excluida_por")
    if request.user.tipo_usuario == User.TipoUsuario.DOCENTE and not _is_coordenador(request.user):
        reservas = reservas.filter(docente=request.user)

    filtro_q = request.GET.get("q", "").strip()
    filtro_polo = request.GET.get("polo", "").strip()
    filtro_sala = request.GET.get("sala", "").strip()
    filtro_tipo = request.GET.get("tipo", "").strip()
    filtro_status = request.GET.get("status", "").strip()
    filtro_docente = request.GET.get("docente", "").strip()
    filtro_data_inicio = request.GET.get("data_inicio", "").strip()
    filtro_data_fim = request.GET.get("data_fim", "").strip()

    if filtro_q:
        reservas = reservas.filter(
            Q(titulo__icontains=filtro_q)
            | Q(sala__nome__icontains=filtro_q)
            | Q(sala__polo__nome__icontains=filtro_q)
            | Q(docente__nome__icontains=filtro_q)
            | Q(docente__email__icontains=filtro_q)
        )
    if filtro_polo:
        reservas = reservas.filter(sala__polo_id=filtro_polo)
    if filtro_sala:
        reservas = reservas.filter(sala_id=filtro_sala)
    if filtro_tipo:
        reservas = reservas.filter(tipo=filtro_tipo)
    if filtro_status:
        reservas = reservas.filter(status=filtro_status)
    if filtro_docente and _has_gestao_access(request.user):
        reservas = reservas.filter(docente_id=filtro_docente)

    data_inicio = parse_date(filtro_data_inicio) if filtro_data_inicio else None
    data_fim = parse_date(filtro_data_fim) if filtro_data_fim else None
    if data_inicio:
        reservas = reservas.filter(inicio__date__gte=data_inicio)
    if data_fim:
        reservas = reservas.filter(inicio__date__lte=data_fim)

    return reservas.order_by("inicio"), {
        "q": filtro_q,
        "polo": filtro_polo,
        "sala": filtro_sala,
        "tipo": filtro_tipo,
        "status": filtro_status,
        "docente": filtro_docente,
        "data_inicio": filtro_data_inicio,
        "data_fim": filtro_data_fim,
    }


def _can_excluir_reserva_ambiente(user, reserva):
    return _is_coordenador(user) or reserva.docente_id == user.id


def _reservas_para_exclusao(reserva):
    reservas = ReservaAmbiente.objects.filter(pk=reserva.pk)
    if reserva.grupo_recorrencia:
        reservas = ReservaAmbiente.objects.filter(
            grupo_recorrencia=reserva.grupo_recorrencia,
            inicio__date__gte=timezone.localdate(),
        )
    return reservas.filter(status=ReservaAmbiente.StatusReserva.ATIVA).order_by("inicio")


def _calendario_reservas_context(request):
    salas_queryset = Sala.objects.filter(ativa=True, polo__ativo=True).select_related("polo").order_by("polo__nome", "nome")
    calendario_semana = request.GET.get("semana", "").strip()
    calendario_polo = request.GET.get("cal_polo", "").strip()
    calendario_sala = request.GET.get("cal_sala", "").strip()

    calendario_data_base = parse_date(calendario_semana) if calendario_semana else timezone.localdate()
    if not calendario_data_base:
        calendario_data_base = timezone.localdate()
    calendario_inicio = calendario_data_base - timedelta(days=calendario_data_base.weekday())
    calendario_fim = calendario_inicio + timedelta(days=6)
    calendario_salas = salas_queryset.prefetch_related("disponibilidades")
    if calendario_polo:
        calendario_salas = calendario_salas.filter(polo_id=calendario_polo)
    if calendario_sala:
        calendario_salas = calendario_salas.filter(id=calendario_sala)
    calendario_salas = list(calendario_salas)
    calendario_reservas = (
        ReservaAmbiente.objects.select_related("sala")
        .filter(
            sala__in=calendario_salas,
            inicio__date__gte=calendario_inicio,
            inicio__date__lte=calendario_fim,
            status=ReservaAmbiente.StatusReserva.ATIVA,
        )
        .order_by("inicio")
    )
    reservas_por_sala_dia = {}
    for reserva in calendario_reservas:
        inicio_local = timezone.localtime(reserva.inicio) if timezone.is_aware(reserva.inicio) else reserva.inicio
        fim_local = timezone.localtime(reserva.fim) if timezone.is_aware(reserva.fim) else reserva.fim
        reservas_por_sala_dia.setdefault((reserva.sala_id, inicio_local.date()), []).append(
            {
                "inicio": inicio_local,
                "fim": fim_local,
                "tipo": reserva.get_tipo_display(),
            }
        )

    calendario_dias = [
        {
            "data": calendario_inicio + timedelta(days=indice),
            "label": (calendario_inicio + timedelta(days=indice)).strftime("%d/%m"),
            "weekday": (calendario_inicio + timedelta(days=indice)).weekday(),
        }
        for indice in range(7)
    ]
    calendario_linhas = []
    for sala in calendario_salas:
        celulas = []
        disponibilidades = list(sala.disponibilidades.all())
        for dia in calendario_dias:
            disponibilidades_dia = [item for item in disponibilidades if item.dia_semana == dia["weekday"]]
            celulas.append(
                {
                    "data": dia["data"],
                    "disponibilidades": disponibilidades_dia,
                    "reservas": reservas_por_sala_dia.get((sala.id, dia["data"]), []),
                }
            )
        calendario_linhas.append({"sala": sala, "celulas": celulas})

    return {
        "calendario_dias": calendario_dias,
        "calendario_linhas": calendario_linhas,
        "calendario_inicio": calendario_inicio,
        "calendario_fim": calendario_fim,
        "calendario_semana_anterior": calendario_inicio - timedelta(days=7),
        "calendario_semana_proxima": calendario_inicio + timedelta(days=7),
        "filtros_calendario": {
            "semana": calendario_semana,
            "polo": calendario_polo,
            "sala": calendario_sala,
        },
    }


@login_required
def reservas_ambientes_view(request):
    if not _can_use_reservas(request.user):
        raise PermissionDenied("Acesso restrito a docentes e servidores.")

    polo_servidor = request.user.polo_atuacao if request.user.tipo_usuario == User.TipoUsuario.SERVIDOR else None
    form = ReservaAmbienteForm(request.POST or None, user=request.user)
    if request.method == "POST":
        if form.is_valid():
            docente = request.user if request.user.tipo_usuario == User.TipoUsuario.DOCENTE else form.cleaned_data["docente"]
            try:
                reservas_criadas = ReservaAmbiente.criar_reservas(
                    sala=form.cleaned_data["sala"],
                    docente=docente,
                    criado_por=request.user,
                    tipo=form.cleaned_data["tipo"],
                    titulo=form.cleaned_data["titulo"],
                    inicio=form.cleaned_data["inicio"],
                    fim=form.cleaned_data["fim"],
                    recorrencia=form.cleaned_data["recorrencia"],
                    duracao_recorrencia_meses=form.cleaned_data["duracao_recorrencia_meses"],
                )
            except ValidationError as exc:
                for erro in exc.messages:
                    form.add_error(None, erro)
            else:
                messages.success(request, f"{len(reservas_criadas)} reserva(s) criada(s) com sucesso.")
                return redirect("reservas_ambientes")

    context = _reservas_base_context()
    context.update({"form": form, "polo_servidor": polo_servidor})
    return render(request, "processos/reservas_ambientes.html", context)


@login_required
def disponibilidade_ambientes_view(request):
    if not _can_use_reservas(request.user):
        raise PermissionDenied("Acesso restrito a docentes e servidores.")

    context = _reservas_base_context()
    context.update(_calendario_reservas_context(request))
    return render(request, "processos/disponibilidade_ambientes.html", context)


@login_required
def reservas_ambientes_feitas_view(request):
    if not _can_use_reservas(request.user):
        raise PermissionDenied("Acesso restrito a docentes e servidores.")

    exclusao_form = ReservaAmbienteExclusaoForm()
    if request.method == "POST":
        if request.POST.get("acao") != "excluir_reserva":
            raise PermissionDenied("Ação inválida.")
        reserva = get_object_or_404(ReservaAmbiente, pk=request.POST.get("reserva_id"))
        if not _can_excluir_reserva_ambiente(request.user, reserva):
            raise PermissionDenied("Apenas a coordenação ou o docente da reserva pode exclui-la.")
        exclusao_form = ReservaAmbienteExclusaoForm(request.POST)
        if exclusao_form.is_valid():
            reservas_excluidas = list(_reservas_para_exclusao(reserva))
            for reserva_excluida in reservas_excluidas:
                reserva_excluida.excluir(usuario=request.user, justificativa=exclusao_form.cleaned_data["justificativa"])
            if len(reservas_excluidas) == 1:
                messages.success(request, "Reserva marcada como excluída.")
            else:
                messages.success(request, f"{len(reservas_excluidas)} reservas marcadas como excluidas.")
            return redirect("reservas_ambientes_feitas")
        messages.error(request, "Informe a justificativa para excluir a reserva.")

    reservas, filtros_reservas = _reservas_filtradas(request)
    reservas = list(reservas)
    for reserva in reservas:
        reserva.can_excluir = _can_excluir_reserva_ambiente(request.user, reserva)
    context = _reservas_base_context()
    context.update(
        {
            "reservas": reservas,
            "filtros_reservas": filtros_reservas,
            "exclusao_form": exclusao_form,
        }
    )
    return render(request, "processos/reservas_ambientes_feitas.html", context)


@login_required
def salas_ambientes_view(request):
    if not _has_gestao_access(request.user):
        raise PermissionDenied("Acesso restrito a coordenadores e servidores.")

    # Coordenadores administram todos os polos, mesmo quando possuem um polo de
    # atuação no próprio cadastro. Servidores continuam restritos ao seu polo.
    can_choose_polo = _is_coordenador(request.user)
    polo = None if can_choose_polo else request.user.polo_atuacao
    sala_form = SalaForm(prefix="sala", can_choose_polo=can_choose_polo, include_ativa=False)
    disponibilidade_form = DisponibilidadeSalaLoteForm(prefix="disp")
    sala_edit_form = None
    modal_aberto = ""

    if polo:
        salas_base = Sala.objects.filter(polo=polo)
    elif can_choose_polo:
        salas_base = Sala.objects.all()
    else:
        salas_base = Sala.objects.none()

    if request.method == "POST" and (polo or can_choose_polo):
        acao = request.POST.get("acao")
        if acao == "criar_sala":
            sala_form = SalaForm(request.POST, prefix="sala", can_choose_polo=can_choose_polo, include_ativa=False)
            if sala_form.is_valid():
                sala = sala_form.save(commit=False)
                if polo:
                    sala.polo = polo
                sala.save()
                messages.success(request, "Sala cadastrada com sucesso.")
                return redirect("salas_ambientes")
            modal_aberto = "nova-sala"
        elif acao == "editar_sala":
            sala = get_object_or_404(salas_base, pk=request.POST.get("sala_id"))
            sala_edit_form = SalaForm(request.POST, prefix="sala_edit", instance=sala, can_choose_polo=can_choose_polo)
            if sala_edit_form.is_valid():
                sala_edit = sala_edit_form.save(commit=False)
                if polo:
                    sala_edit.polo = polo
                sala_edit.save()
                messages.success(request, "Sala atualizada com sucesso.")
                return redirect("salas_ambientes")
            modal_aberto = f"editar-sala-{sala.pk}"
        elif acao == "adicionar_disponibilidade":
            sala = get_object_or_404(salas_base, pk=request.POST.get("sala_id"))
            disponibilidade_form = DisponibilidadeSalaLoteForm(request.POST, prefix="disp")
            if disponibilidade_form.is_valid():
                disponibilidades = disponibilidade_form.save(sala)
                if len(disponibilidades) == 1:
                    messages.success(request, "Disponibilidade cadastrada com sucesso.")
                else:
                    messages.success(request, f"{len(disponibilidades)} disponibilidades cadastradas com sucesso.")
                return redirect("salas_ambientes")
            modal_aberto = f"editar-sala-{sala.pk}"
        elif acao == "excluir_disponibilidade":
            disponibilidade = get_object_or_404(
                DisponibilidadeSala.objects.select_related("sala"),
                pk=request.POST.get("disponibilidade_id"),
                sala__in=salas_base,
            )
            disponibilidade.delete()
            messages.success(request, "Horário removido com sucesso.")
            return redirect("salas_ambientes")

    salas = salas_base.select_related("polo").prefetch_related("disponibilidades").order_by("polo__nome", "nome")
    return render(
        request,
        "processos/salas_ambientes.html",
        {
            "polo": polo,
            "salas": salas,
            "sala_form": sala_form,
            "disponibilidade_form": disponibilidade_form,
            "sala_edit_form": sala_edit_form,
            "modal_aberto": modal_aberto,
            "can_choose_polo": can_choose_polo,
        },
    )


def _solicitacao_banca_context(form, request, solicitacao=None):
    trajetorias = form.fields["trajetoria"].queryset
    alunos = form.fields["aluno"].queryset
    checklists_integralizacao = []
    for trajetoria in trajetorias.prefetch_related(
        "disciplinas",
        "estagios_docencia",
        "lancamentos_horas_complementares",
        "publicacoes",
    ):
        resumo = _resumo_checklist_banca(trajetoria)
        if resumo:
            checklists_integralizacao.append(resumo)
    papeis_por_tipo = []
    for tipo, label in SolicitacaoBanca.TipoDefesa.choices:
        papeis = []
        for papel in MembroBanca.papeis_para_tipo(tipo):
            papeis.append(
                {
                    "valor": papel,
                    "label": MembroBanca.Papel(papel).label,
                    "opcional": MembroBanca.papel_opcional(tipo, papel),
                    "exige_instituicao": MembroBanca.exige_instituicao(papel),
                    "exige_cpf": MembroBanca.exige_cpf(tipo, papel),
                    "nome_field": form[f"membro_{papel}_nome"],
                    "instituicao_field": form[f"membro_{papel}_instituicao"],
                    "cpf_field": form[f"membro_{papel}_cpf"],
                }
            )
        papeis_por_tipo.append({"valor": tipo, "label": label, "papeis": papeis})

    return {
        "form": form,
        "solicitacao": solicitacao,
        "alunos_orientados": alunos,
        "trajetorias_orientadas": trajetorias,
        "checklists_integralizacao": checklists_integralizacao,
        "papeis_por_tipo": papeis_por_tipo,
        "is_coordenador": _is_coordenador(request.user),
        "has_gestao_access": _has_gestao_access(request.user),
        "can_view_dashboard": _can_view_dashboard(request.user),
        "can_view_processos": _can_view_processos(request.user),
        "can_view_caixa": _can_view_caixa(request.user),
    }


def _criar_processo_para_solicitacao_banca(solicitacao):
    if solicitacao.processo_id:
        return solicitacao.processo, False

    setor_secretaria = Setor.objects.filter(nome="Secretaria PPGEC", ativo=True).first()
    if not setor_secretaria:
        raise ValidationError("Setor inicial 'Secretaria PPGEC' não encontrado. Contate o administrador.")

    processo = Processo.objects.create(
        usuario_criado_por=solicitacao.docente,
        tipo=solicitacao.tipo_defesa,
        assunto=f"{solicitacao.get_tipo_defesa_display()} - {solicitacao.aluno.nome}",
        descricao=(
            "Processo gerado automaticamente a partir da solicitação de banca "
            f"finalizada em {timezone.localtime(solicitacao.finalizado_em):%d/%m/%Y %H:%M}."
        ),
        setor_atual=setor_secretaria,
        status=Processo.StatusProcesso.EM_ANALISE,
    )
    solicitacao.processo = processo
    solicitacao.save(update_fields=["processo"])
    return processo, True


@login_required
def solicitacoes_assinatura_view(request):
    if not _can_view_assinaturas(request.user) and not _has_gestao_access(request.user):
        raise PermissionDenied("Acesso restrito a solicitações de assinatura.")

    queryset = SolicitacaoAssinatura.objects.select_related(
        "criado_por",
        "docente",
        "setor",
        "assinado_por",
    )
    if not _has_gestao_access(request.user):
        queryset = queryset.filter(
            Q(id__in=_assinaturas_destinadas_queryset(request.user).values("id"))
            | Q(criado_por=request.user)
        )

    status = request.GET.get("status", "").strip().upper()
    if status in {SolicitacaoAssinatura.Status.PENDENTE, SolicitacaoAssinatura.Status.ASSINADO}:
        queryset = queryset.filter(status=status)
    else:
        status = ""

    termo_busca = request.GET.get("q", "").strip()
    if termo_busca:
        queryset = queryset.filter(
            Q(numero_bloco_sei__icontains=termo_busca)
            | Q(numero_documento_sei__icontains=termo_busca)
            | Q(documento_pdf__icontains=termo_busca)
            | Q(observacao__icontains=termo_busca)
            | Q(observacao_assinatura__icontains=termo_busca)
        )

    return render(
        request,
        "processos/solicitacoes_assinatura.html",
        {
            "page_title": "Solicitações de Assinatura",
            "page_description": "Acompanhe assinaturas em documentos do SEI ou PDFs.",
            "solicitacoes": queryset,
            "status_filtro": status,
            "status_choices": SolicitacaoAssinatura.Status.choices,
            "termo_busca": termo_busca,
            "show_status_filters": True,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def pendencias_assinatura_view(request):
    pendencias = _assinaturas_pendentes_queryset(request.user)
    if not pendencias.exists() and not _has_gestao_access(request.user) and not _is_docente(request.user):
        raise PermissionDenied("Acesso restrito a pendências de assinatura.")

    return render(
        request,
        "processos/solicitacoes_assinatura.html",
        {
            "page_title": "Pendências de Assinatura",
            "page_description": "Assinaturas pendentes destinadas a você.",
            "solicitacoes": pendencias,
            "status_filtro": SolicitacaoAssinatura.Status.PENDENTE,
            "status_choices": SolicitacaoAssinatura.Status.choices,
            "show_status_filters": False,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def nova_solicitacao_assinatura_view(request):
    if not _has_gestao_access(request.user):
        raise PermissionDenied("Acesso restrito a secretaria e coordenação.")

    if request.method == "POST":
        form = SolicitacaoAssinaturaForm(request.POST, request.FILES)
        if form.is_valid():
            solicitacao = form.save(commit=False)
            solicitacao.criado_por = request.user
            solicitacao.status = SolicitacaoAssinatura.Status.PENDENTE
            solicitacao.save()
            send_email_solicitacao_assinatura.delay(solicitacao.id)
            messages.success(request, "Solicitação de assinatura enviada.")
            return redirect("solicitacoes_assinatura")
    else:
        form = SolicitacaoAssinaturaForm()

    return render(
        request,
        "processos/nova_solicitacao_assinatura.html",
        {
            "form": form,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def solicitacao_assinatura_detalhe_view(request, solicitacao_id):
    solicitacao = get_object_or_404(
        SolicitacaoAssinatura.objects.select_related("criado_por", "docente", "setor", "assinado_por"),
        pk=solicitacao_id,
    )
    if not _can_view_solicitacao_assinatura(request.user, solicitacao):
        raise PermissionDenied("Você não pode visualizar esta solicitação de assinatura.")

    can_atender = _can_atender_solicitacao_assinatura(request.user, solicitacao)
    if request.method == "POST":
        if not can_atender:
            raise PermissionDenied("Você não pode atender esta solicitação de assinatura.")
        form = AtenderSolicitacaoAssinaturaForm(
            request.POST,
            request.FILES,
            instance=solicitacao,
            solicitacao=solicitacao,
        )
        if form.is_valid():
            solicitacao = form.save(commit=False)
            solicitacao.marcar_assinado(
                usuario=request.user,
                documento_assinado=form.cleaned_data.get("documento_assinado_pdf"),
                observacao=form.cleaned_data.get("observacao_assinatura"),
            )
            messages.success(request, "Solicitação de assinatura concluída.")
            return redirect("solicitacao_assinatura_detalhe", solicitacao_id=solicitacao.id)
    else:
        form = AtenderSolicitacaoAssinaturaForm(instance=solicitacao, solicitacao=solicitacao)

    return render(
        request,
        "processos/solicitacao_assinatura_detalhe.html",
        {
            "solicitacao": solicitacao,
            "form": form,
            "can_atender": can_atender,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def solicitacoes_banca_view(request):
    if request.user.tipo_usuario != User.TipoUsuario.DOCENTE:
        raise PermissionDenied("Acesso restrito a docentes.")

    solicitacoes = (
        SolicitacaoBanca.objects.select_related("aluno", "trajetoria", "processo")
        .filter(docente=request.user)
        .order_by("-atualizado_em")
    )
    return render(
        request,
        "processos/solicitacoes_banca.html",
        {
            "solicitacoes": solicitacoes,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def solicitacao_banca_nova_view(request):
    if request.user.tipo_usuario != User.TipoUsuario.DOCENTE:
        raise PermissionDenied("Acesso restrito a docentes.")

    finalizar = request.POST.get("acao") == "finalizar"
    form = SolicitacaoBancaForm(request.POST or None, docente=request.user, finalizar=finalizar)
    if request.method == "POST" and form.is_valid():
        status = SolicitacaoBanca.Status.FINALIZADA if finalizar else SolicitacaoBanca.Status.RASCUNHO
        processo_criado = None
        try:
            with transaction.atomic():
                solicitacao = form.save(commit=False, docente=request.user, status=status)
                if finalizar:
                    solicitacao.finalizado_por = request.user
                    solicitacao.finalizado_em = timezone.now()
                solicitacao.save()
                form.save_membros(solicitacao)
                if finalizar:
                    processo, criado = _criar_processo_para_solicitacao_banca(solicitacao)
                    processo_criado = processo if criado else None
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
        else:
            if processo_criado:
                send_email_novo_processo_aluno.delay(processo_criado.id)
                send_email_novo_processo_orientador.delay(processo_criado.id)
                send_email_novo_processo_secretaria.delay(processo_criado.id)
                messages.success(
                    request,
                    f"Solicitação de banca finalizada e processo {processo_criado.numero} aberto com sucesso.",
                )
            else:
                messages.success(request, "Solicitação de banca finalizada." if finalizar else "Rascunho salvo.")
            return redirect("solicitacao_banca_detalhe", solicitacao_id=solicitacao.id)

    return render(request, "processos/solicitacao_banca_form.html", _solicitacao_banca_context(form, request))


@login_required
def solicitacao_banca_detalhe_view(request, solicitacao_id):
    if request.user.tipo_usuario != User.TipoUsuario.DOCENTE:
        raise PermissionDenied("Acesso restrito a docentes.")

    solicitacao = get_object_or_404(
        SolicitacaoBanca.objects.select_related("aluno", "trajetoria", "finalizado_por", "processo").prefetch_related("membros"),
        pk=solicitacao_id,
        docente=request.user,
    )
    if not solicitacao.is_rascunho:
        return render(
            request,
            "processos/solicitacao_banca_detalhe.html",
            {
                "solicitacao": solicitacao,
                "is_coordenador": _is_coordenador(request.user),
                "has_gestao_access": _has_gestao_access(request.user),
                "can_view_dashboard": _can_view_dashboard(request.user),
                "can_view_processos": _can_view_processos(request.user),
                "can_view_caixa": _can_view_caixa(request.user),
            },
        )

    finalizar = request.POST.get("acao") == "finalizar"
    form = SolicitacaoBancaForm(
        request.POST or None,
        instance=solicitacao,
        docente=request.user,
        finalizar=finalizar,
    )
    if request.method == "POST" and form.is_valid():
        status = SolicitacaoBanca.Status.FINALIZADA if finalizar else SolicitacaoBanca.Status.RASCUNHO
        processo_criado = None
        try:
            with transaction.atomic():
                solicitacao = form.save(commit=False, docente=request.user, status=status)
                if finalizar:
                    solicitacao.finalizado_por = request.user
                    solicitacao.finalizado_em = timezone.now()
                solicitacao.save()
                form.save_membros(solicitacao)
                if finalizar:
                    processo, criado = _criar_processo_para_solicitacao_banca(solicitacao)
                    processo_criado = processo if criado else None
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
        else:
            if processo_criado:
                send_email_novo_processo_aluno.delay(processo_criado.id)
                send_email_novo_processo_orientador.delay(processo_criado.id)
                send_email_novo_processo_secretaria.delay(processo_criado.id)
                messages.success(
                    request,
                    f"Solicitação de banca finalizada e processo {processo_criado.numero} aberto com sucesso.",
                )
            else:
                messages.success(request, "Solicitação de banca finalizada." if finalizar else "Rascunho salvo.")
            return redirect("solicitacao_banca_detalhe", solicitacao_id=solicitacao.id)

    return render(
        request,
        "processos/solicitacao_banca_form.html",
        _solicitacao_banca_context(form, request, solicitacao=solicitacao),
    )


@login_required
def aluno_documento_vinculo_view(request):
    if request.user.tipo_usuario != User.TipoUsuario.ALUNO:
        raise PermissionDenied("Acesso restrito a alunos.")
    return render(
        request,
        "processos/aluno_documento_todo.html",
        {
            "titulo": "Documento de vínculo",
            "descricao": "A emissão automática do documento de vínculo ainda será construída. Enquanto isso, solicite o documento à secretaria abrindo um processo.",
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
            "show_side_menu": True,
            "side_menu_title": "Menu",
            "side_menu_items": _menu_lateral_home(request.user),
        },
    )


@login_required
def aluno_documento_historico_view(request):
    if request.user.tipo_usuario != User.TipoUsuario.ALUNO:
        raise PermissionDenied("Acesso restrito a alunos.")
    return render(
        request,
        "processos/aluno_documento_todo.html",
        {
            "titulo": "Documento de histórico",
            "descricao": "A emissão do histórico escolar ainda será construída. "
            "Enquanto isso, solicite o documento à secretaria abrindo um processo.",
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
            "show_side_menu": True,
            "side_menu_title": "Menu",
            "side_menu_items": _menu_lateral_home(request.user),
        },
    )



def _nome_do_setor(valor):
    """Nome do setor a partir do id que veio no filtro.

    O marcador precisa dizer "Secretaria PPGEC", nao "3": o numero e detalhe da
    URL, e quem le a tela nao tem como saber a que setor ele corresponde.
    """
    setor = Setor.objects.filter(pk=valor).first()
    return setor.nome if setor else valor


def _nome_do_periodo(valor):
    """Nome do periodo letivo a partir do id que veio no filtro."""
    periodo = PeriodoLetivo.objects.filter(pk=valor).first()
    return periodo.nome if periodo else valor


def _filtros_ativos(request, rotulos):
    """Os filtros em vigor, cada um com o endereco que o remove.

    Existe porque uma lista filtrada nao se anunciava: quem voltasse a tela
    depois via menos resultados do que esperava e nenhuma explicacao do porque.
    Cada marcador diz o que esta valendo e leva ao mesmo endereco sem aquele
    parametro -- os demais filtros seguem de pe, que e o que se espera ao tirar
    um de varios.

    rotulos: {parametro: (titulo, funcao que transforma o valor em texto)}. A
    funcao existe para o marcador mostrar "Trancamento de Matricula" e nao
    "TRANCAMENTO_MATRICULA".

    Titulo vazio serve aos filtros de liga-desliga: "Somente atrasados" e a
    frase inteira, e escrever "Somente: atrasados" seria partir em par o que nao
    e par de rotulo e valor.
    """
    ativos = []
    for parametro, (titulo, formatar) in rotulos.items():
        valor = request.GET.get(parametro, "").strip()
        if not valor:
            continue
        restante = request.GET.copy()
        restante.pop(parametro, None)
        ativos.append(
            {
                "titulo": titulo,
                "valor": formatar(valor) if formatar else valor,
                "url_sem": f"{request.path}?{restante.urlencode()}" if restante else request.path,
            }
        )
    return ativos


@login_required
def menu_meus_processos_view(request):
    if request.user.tipo_usuario == User.TipoUsuario.SERVIDOR:
        raise PermissionDenied("Perfil SERVIDOR não possui meus processos.")

    meus_processos = (
        Processo.objects.select_related("setor_atual")
        .filter(usuario_criado_por=request.user)
        .order_by("-data_criacao")
    )

    filtro_q = request.GET.get("my_q", "").strip()
    filtro_tipo = request.GET.get("my_tipo", "").strip()
    filtro_status = request.GET.get("my_status", "").strip()
    filtro_data_inicio = request.GET.get("my_data_inicio", "").strip()
    filtro_data_fim = request.GET.get("my_data_fim", "").strip()
    filtro_atrasados = request.GET.get("my_atrasados") == "1"

    if filtro_atrasados:
        meus_processos = meus_processos.filter(prazo_limite__lt=timezone.localdate()).exclude(
            status=Processo.StatusProcesso.FINALIZADO
        )
    if filtro_q:
        meus_processos = meus_processos.filter(
            Q(numero__icontains=filtro_q)
            | Q(assunto__icontains=filtro_q)
            | Q(descricao__icontains=filtro_q)
        )
    if filtro_tipo:
        meus_processos = meus_processos.filter(tipo=filtro_tipo)
    if filtro_status:
        meus_processos = meus_processos.filter(status=filtro_status)

    data_inicio = parse_date(filtro_data_inicio) if filtro_data_inicio else None
    data_fim = parse_date(filtro_data_fim) if filtro_data_fim else None
    if data_inicio:
        meus_processos = meus_processos.filter(data_criacao__date__gte=data_inicio)
    if data_fim:
        meus_processos = meus_processos.filter(data_criacao__date__lte=data_fim)

    return render(
        request,
        "processos/menu_meus_processos.html",
        {
            "meus_processos": meus_processos,
            "my_tipos": Processo.TipoProcesso.choices,
            "my_status_list": Processo.StatusProcesso.choices,
            "my_filtro_q": filtro_q,
            "my_filtro_tipo": filtro_tipo,
            "my_filtro_status": filtro_status,
            "my_filtro_data_inicio": filtro_data_inicio,
            "my_filtro_data_fim": filtro_data_fim,
            "my_filtro_atrasados": filtro_atrasados,
            "my_filtros_ativos": _filtros_ativos(
                request,
                {
                    "my_q": ("Busca", None),
                    "my_tipo": ("Tipo", dict(Processo.TipoProcesso.choices).get),
                    "my_status": ("Status", dict(Processo.StatusProcesso.choices).get),
                    "my_data_inicio": ("A partir de", None),
                    "my_data_fim": ("Até", None),
                    "my_atrasados": ("", lambda _: "Somente atrasados"),
                },
            ),
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
            "show_side_menu": True,
            "side_menu_title": "Menu",
            "side_menu_items": _menu_lateral_home(request.user),
        },
    )


@login_required
def menu_processos_orientandos_view(request):
    if request.user.tipo_usuario != User.TipoUsuario.DOCENTE:
        raise PermissionDenied("Acesso restrito a docentes.")

    orientandos = (
        Aluno.objects.filter(
            trajetorias__orientador=request.user,
            trajetorias__status=TrajetoriaAcademica.Status.ATIVA,
        )
        .distinct()
    )
    processos_orientandos = (
        Processo.objects.select_related("usuario_criado_por", "setor_atual")
        .filter(usuario_criado_por__in=orientandos.values("id"))
        .order_by("-data_criacao")
    )
    return render(
        request,
        "processos/menu_processos_orientandos.html",
        {
            "processos_orientandos": processos_orientandos,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
            "show_side_menu": True,
            "side_menu_title": "Menu",
            "side_menu_items": _menu_lateral_home(request.user),
        },
    )


@login_required
def menu_meus_orientandos_view(request):
    if request.user.tipo_usuario != User.TipoUsuario.DOCENTE:
        raise PermissionDenied("Acesso restrito a docentes.")

    trajetorias_docente = TrajetoriaAcademica.objects.select_related("aluno", "orientador", "coorientador").order_by(
        "aluno__nome",
        "-criado_em",
    )
    orientacoes_ativas = trajetorias_docente.filter(
        orientador=request.user,
        status=TrajetoriaAcademica.Status.ATIVA,
    ).exclude(nivel_curso=Aluno.NivelCurso.POSDOUTORADO)
    supervisoes_ativas = trajetorias_docente.filter(
        orientador=request.user,
        status=TrajetoriaAcademica.Status.ATIVA,
        nivel_curso=Aluno.NivelCurso.POSDOUTORADO,
    )
    coorientacoes_ativas = trajetorias_docente.filter(
        coorientador=request.user,
        status=TrajetoriaAcademica.Status.ATIVA,
    )
    vinculos_concluidos = trajetorias_docente.filter(
        Q(orientador=request.user) | Q(coorientador=request.user),
    ).exclude(status=TrajetoriaAcademica.Status.ATIVA)
    return render(
        request,
        "processos/menu_meus_orientandos.html",
        {
            "orientacoes_ativas": orientacoes_ativas,
            "coorientacoes_ativas": coorientacoes_ativas,
            "supervisoes_ativas": supervisoes_ativas,
            "vinculos_concluidos": vinculos_concluidos,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
            "show_side_menu": True,
            "side_menu_title": "Menu",
            "side_menu_items": _menu_lateral_home(request.user),
        },
    )


@login_required
def menu_processos_pleno_view(request):
    if not _is_membro_setor_nome(request.user, Setor.NOME_PLENO):
        raise PermissionDenied("Acesso restrito a membros do Colegiado PPGEC (Pleno).")

    processos_pleno = (
        Processo.objects.select_related("usuario_criado_por", "setor_atual")
        .filter(setor_atual__nome__icontains="Pleno")
        .order_by("-data_criacao")
    )
    return render(
        request,
        "processos/menu_processos_pleno.html",
        {
            "processos_pleno": processos_pleno,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
            "show_side_menu": True,
            "side_menu_title": "Menu",
            "side_menu_items": _menu_lateral_home(request.user),
        },
    )


@login_required
def menu_ciencias_manifestadas_view(request):
    if request.user.tipo_usuario != User.TipoUsuario.DOCENTE:
        raise PermissionDenied("Acesso restrito a docentes.")

    ciencias_pendentes = (
        ManifestacaoProcesso.objects.select_related("processo", "solicitado_por")
        .filter(
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            responsavel=request.user,
            status=ManifestacaoProcesso.StatusManifestacao.PENDENTE,
        )
        .order_by("-data_solicitacao")
    )
    ciencias_manifestadas = (
        ManifestacaoProcesso.objects.select_related("processo", "solicitado_por")
        .filter(
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            responsavel=request.user,
            status__in=[
                ManifestacaoProcesso.StatusManifestacao.CIENTE,
                ManifestacaoProcesso.StatusManifestacao.RECUSADO,
            ],
        )
        .order_by("-data_manifestacao", "-data_solicitacao")
    )
    return render(
        request,
        "processos/menu_ciencias_manifestadas.html",
        {
            "ciencias_pendentes": ciencias_pendentes,
            "ciencias_manifestadas": ciencias_manifestadas,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
            "show_side_menu": True,
            "side_menu_title": "Menu",
            "side_menu_items": _menu_lateral_home(request.user),
        },
    )


# ==========================================================================
# Entrega de arquivos enviados
# ==========================================================================

# Cada campo de arquivo do sistema, com a regra que decide quem pode le-lo.
#
# A entrega de /media/ era feita por django.views.static.serve atras de
# @login_required, o que exige estar logado e nada mais: bastava conhecer o
# caminho para baixar qualquer documento, inclusive os marcados como sigilosos.
# A regra por documento existia (Documento.pode_visualizar_arquivo) e era
# respeitada pelo template -- que esconde o link --, mas nao pelo arquivo.
#
# O registro e explicito de proposito. Um campo de arquivo novo que nao seja
# declarado aqui nao e servido, em vez de ser servido sem regra: o esquecimento
# vira arquivo inacessivel, que se percebe, e nao arquivo exposto, que nao se
# percebe.
def _regras_de_arquivo():
    return (
        (Documento, "arquivo", lambda obj, user: obj.pode_visualizar_arquivo(user)),
        (SolicitacaoAssinatura, "documento_pdf", _can_view_solicitacao_assinatura_do_arquivo),
        (SolicitacaoAssinatura, "documento_assinado_pdf", _can_view_solicitacao_assinatura_do_arquivo),
    )


def _can_view_solicitacao_assinatura_do_arquivo(solicitacao, user):
    """Mesma regra que protege a tela de detalhe da solicitacao.

    Os PDFs de assinatura so aparecem naquela tela; quem nao pode abri-la
    tambem nao deve alcancar os arquivos por outro caminho.
    """
    return _can_view_solicitacao_assinatura(user, solicitacao)


@login_required
def arquivo_enviado_view(request, path):
    """Entrega um arquivo de /media/ depois de aplicar a regra do dono dele.

    Responde 404 -- e nao 403 -- quando o usuario nao tem acesso. Estes
    documentos carregam classificacao de sigilo (informacao pessoal, sigilo
    academico, propriedade intelectual, entre outras), e um 403 confirmaria que
    existe um arquivo naquele caminho. Quem chega aqui sem permissao nao veio
    pela interface: a tela esconde o link de quem nao pode ver.
    """
    for modelo, campo, pode_ver in _regras_de_arquivo():
        dono = modelo.objects.filter(**{campo: path}).first()
        if dono is None:
            continue
        if not pode_ver(dono, request.user):
            raise Http404("Arquivo não encontrado.")
        return _entregar_arquivo(request, path)

    # Nenhum registro reivindica este caminho: arquivo orfao, sobra de um
    # registro apagado ou tentativa de adivinhar caminho.
    raise Http404("Arquivo não encontrado.")


def _entregar_arquivo(request, path):
    """Entrega o arquivo pelo meio do armazenamento em uso.

    Em disco, a propria view transmite o conteudo. No S3, o bucket e privado e
    quem tem a chave e a aplicacao: em vez de baixar o arquivo para reenvia-lo,
    a view assina um endereco de vida curta e redireciona para ele. O trafego
    vai direto do S3 para o navegador, e a aplicacao continua sendo o unico
    lugar onde a permissao e decidida.

    A assinatura so e emitida depois da verificacao, e por isso ela nao afrouxa
    a regra: sem passar por aqui, nao ha endereco valido.
    """
    if settings.USA_S3:
        return redirect(default_storage.url(path))
    return serve(request, path, document_root=settings.MEDIA_ROOT)
