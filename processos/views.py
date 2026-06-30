import re

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    AlunoComentarioForm,
    AlunoDadosForm,
    AlunoDefesaForm,
    AlunoDepositoFinalForm,
    AlunoCoorientadorForm,
    AlunoOrientadorForm,
    AlunoIniciarDoutoradoForm,
    AlunoPrazoForm,
    AlunoQualificacaoForm,
    AlunoReingressoForm,
    AlunoStatusForm,
    NovoEstagioDocenciaForm,
    EstagioDocenciaUpdateForm,
    TrajetoriaAcademicaForm,
    TrajetoriaStatusForm,
    ManifestarCienteOrientadorForm,
    ComentarioProcessoForm,
    DocumentoCadastroForm,
    EncaminhamentoForm,
    FinalizarProcessoForm,
    ProcessoAberturaForm,
    SolicitarCienteOrientadorForm,
    UserProfileForm
)
from .models import (
    AlteracaoAluno,
    Aluno,
    ComentarioProcesso,
    Docente,
    Documento,
    EstagioDocencia,
    ManifestacaoProcesso,
    Processo,
    Setor,
    TrajetoriaAcademica,
    User,
)

from .tasks import(
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
    send_email_status_atualizado
)

def _is_docente(user):
    return user.is_authenticated and user.tipo_usuario == User.TipoUsuario.DOCENTE


def _is_servidor(user):
    return user.is_authenticated and user.tipo_usuario == User.TipoUsuario.SERVIDOR


def _is_coordenador(user):
    if not user.is_authenticated or user.tipo_usuario != User.TipoUsuario.DOCENTE:
        return False

    try:
        return bool(user.docente.coordenador)
    except Docente.DoesNotExist:
        return False


def _has_gestao_access(user):
    return _is_coordenador(user) or _is_servidor(user)


def _can_view_dashboard(user):
    return _has_gestao_access(user)


def _can_view_processos(user):
    return _is_coordenador(user) or _is_servidor(user)


def _can_view_processo_detalhe(user, processo):
    if not user.is_authenticated:
        return False

    if processo.usuario_criado_por_id == user.id:
        return True

    if _can_view_processos(user):
        return True

    if _is_docente(user):
        if _is_processo_no_pleno(processo):
            return True
        return TrajetoriaAcademica.objects.filter(
            aluno_id=processo.usuario_criado_por_id,
            status=TrajetoriaAcademica.Status.ATIVA,
        ).filter(Q(orientador=user) | Q(coorientador=user)).exists()

    return False


def _is_requerente_do_processo(user, processo):
    return user.is_authenticated and processo.usuario_criado_por_id == user.id


def _can_view_caixa(user):
    return _is_docente(user) or _is_servidor(user)


def _can_manage_restricted_docs(user):
    return _is_servidor(user) or _is_coordenador(user)


def _nomes_setores_caixa(user):
    if _is_servidor(user):
        return ["Secretaria PPGEC"]
    if _is_coordenador(user):
        return ["Coordenação PPG", "Colegiando PPGEC (Pleno)"]
    if _is_docente(user):
        return ["Colegiando PPGEC (Pleno)"]
    return []


def _is_setor_pleno_nome(nome: str) -> bool:
    return "pleno" in (nome or "").lower()


def _semestre_valido(valor: str) -> bool:
    return bool(re.fullmatch(r"\d{4}\.[12]", (valor or "").strip()))


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


def _docente_label(docente) -> str:
    if not docente:
        return "-"
    return f"{docente.nome} ({docente.email})"


def _coorientador_label(trajetoria: TrajetoriaAcademica) -> str:
    if trajetoria.coorientador:
        return f"Cadastrado: {_docente_label(trajetoria.coorientador)}"
    if trajetoria.coorientador_externo_nome:
        partes = [f"Externo: {trajetoria.coorientador_externo_nome}"]
        if trajetoria.coorientador_externo_email:
            partes.append(trajetoria.coorientador_externo_email)
        if trajetoria.coorientador_externo_instituicao:
            partes.append(trajetoria.coorientador_externo_instituicao)
        return " | ".join(partes)
    return "-"


def _prazos_academicos_label(trajetoria: TrajetoriaAcademica) -> str:
    return (
        f"ingresso={trajetoria.ingresso or '-'};"
        f"{trajetoria.qualificacao_label_lower}={trajetoria.prazo_qualificacao or '-'};"
        f"defesa={trajetoria.prazo_defesa or '-'};"
        f"reingressante={'Sim' if trajetoria.reingressante else 'Nao'}"
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
        return "Orientador", _docente_label(trajetoria.orientador)
    if campo == "coorientador":
        return "Coorientador", _coorientador_label(trajetoria)
    if campo == "defesa":
        return "Defesa", _defesa_display(trajetoria)
    if campo == "deposito_versao_final":
        return "Deposito final", _bool_label(trajetoria.deposito_versao_final)
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
        "Deposito final": "Deposito final",
    }
    if campo.lower().startswith("prazo "):
        return "Prazo de qualificacao/projeto"
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
        texto_alteracao = f"Alteracao no {campo} ({valor_novo})"
    elif alteracoes:
        texto_alteracao = "; ".join(
            f"{campo}: {valor_anterior} -> {valor_novo}"
            for campo, valor_anterior, valor_novo in alteracoes
        )
    else:
        texto_alteracao = "Alteracao registrada"

    return {
        "obj": alteracao,
        "trajetoria": trajetoria,
        "alteracao": texto_alteracao,
    }


def _bool_label(valor: bool) -> str:
    return "Sim" if valor else "Nao"


def _trajetoria_ativa(aluno: Aluno) -> TrajetoriaAcademica:
    trajetoria = aluno.trajetorias.filter(status=TrajetoriaAcademica.Status.ATIVA).order_by("-criado_em").first()
    return trajetoria


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
    if _is_servidor(user):
        return processo.setor_atual.nome == "Secretaria PPGEC"
    if _is_coordenador(user):
        return processo.setor_atual.nome == "Coordenação PPG" or _is_processo_no_pleno(processo)
    return False


def _menu_lateral_home(user):
    if user.tipo_usuario == User.TipoUsuario.DOCENTE:
        return [
            {"label": "Meus Processos", "href": "/menu/meus-processos/"},
            {"label": "Processos no Pleno", "href": "/menu/processos-pleno/"},
            {"label": "Processos dos orientandos", "href": "/menu/processos-orientandos/"},
            {"label": "Ciencias", "href": "/menu/ciencias-manifestadas/"},
            {"label": "Meus Orientandos", "href": "/menu/meus-orientandos/"},
        ]
    if user.tipo_usuario == User.TipoUsuario.ALUNO:
        return [
            {"label": "Documento de vínculo (TODO)", "href": "/aluno/documento-vinculo/"},
            {"label": "Documento de histórico", "href": "/aluno/documento-historico/"},
            {"label": "Meus Processos", "href": "/menu/meus-processos/"},
            {"label": "Novo processo", "href": "/processos/novo/"},
        ]
    return []


@login_required
def home_view(request):
    is_coordenador = _is_coordenador(request.user)
    has_gestao_access = _has_gestao_access(request.user)
    can_view_dashboard = _can_view_dashboard(request.user)
    can_view_processos = _can_view_processos(request.user)
    can_view_caixa = _can_view_caixa(request.user)
    meus_processos_base = Processo.objects.filter(usuario_criado_por=request.user)
    meus_processos_requerente = meus_processos_base.filter(setor_atual__nome="Requerente")
    context = {
        "meus_processos_requerente": meus_processos_requerente,
        "is_coordenador": is_coordenador,
        "has_gestao_access": has_gestao_access,
        "can_view_dashboard": can_view_dashboard,
        "can_view_processos": can_view_processos,
        "can_view_caixa": can_view_caixa,
        "can_add_processo": request.user.tipo_usuario != User.TipoUsuario.SERVIDOR,
        "show_side_menu": request.user.tipo_usuario in [User.TipoUsuario.DOCENTE, User.TipoUsuario.ALUNO],
        "side_menu_title": "Menu",
        "side_menu_items": _menu_lateral_home(request.user),
    }

    if request.user.tipo_usuario == User.TipoUsuario.DOCENTE:
        orientandos = Aluno.objects.filter(
            trajetorias__status=TrajetoriaAcademica.Status.ATIVA,
        ).filter(
            Q(trajetorias__orientador=request.user) | Q(trajetorias__coorientador=request.user)
        ).order_by("nome").distinct()
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

    return render(
        request,
        "processos/me.html",
        {
            "profile_form": profile_form,
            "password_form": password_form,
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
        status=TrajetoriaAcademica.Status.ATIVA
    ).select_related("aluno")
    docentes = (
        Docente.objects.prefetch_related(
            Prefetch("trajetorias_orientadas", queryset=trajetorias_ativas, to_attr="trajetorias_orientadas_ativas"),
            Prefetch(
                "trajetorias_coorientadas",
                queryset=trajetorias_ativas,
                to_attr="trajetorias_coorientadas_ativas",
            ),
        )
        .annotate(
            total_orientandos=Count(
                "trajetorias_orientadas",
                filter=Q(trajetorias_orientadas__status=TrajetoriaAcademica.Status.ATIVA),
                distinct=True,
            ),
            total_coorientandos=Count(
                "trajetorias_coorientadas",
                filter=Q(trajetorias_coorientadas__status=TrajetoriaAcademica.Status.ATIVA),
                distinct=True,
            ),
        )
        .order_by("-total_orientandos", "-total_coorientandos", "nome")
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
    return render(
        request,
        "processos/processos_lista.html",
        {
            "processos": queryset,
            "tipos": Processo.TipoProcesso.choices,
            "status_list": Processo.StatusProcesso.choices,
            "setores": Setor.objects.order_by("nome"),
            "filtro_tipo": tipo,
            "filtro_status": status,
            "filtro_setor": setor_id,
            "filtro_q": termo,
            "filtro_atrasados": somente_atrasados,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def alunos_view(request):
    if not _has_gestao_access(request.user):
        raise PermissionDenied("Acesso restrito a coordenadores e servidores.")

    queryset = Aluno.objects.prefetch_related("trajetorias__orientador", "trajetorias__coorientador").order_by("nome")
    nome = request.GET.get("nome", "").strip()
    ingresso_inicio_raw = request.GET.get("ingresso_inicio", "").strip()
    ingresso_fim_raw = request.GET.get("ingresso_fim", "").strip()
    nivel = request.GET.get("nivel", "").strip().upper()
    reingressante = request.GET.get("reingressante", "").strip()
    status = request.GET.get("status", "").strip().upper()

    if nome:
        queryset = queryset.filter(nome__icontains=nome)
    ingresso_inicio = ingresso_inicio_raw if _semestre_valido(ingresso_inicio_raw) else ""
    ingresso_fim = ingresso_fim_raw if _semestre_valido(ingresso_fim_raw) else ""

    status_trajetoria = _status_trajetoria_listagem(status) if status else ""
    queryset = queryset.distinct()
    alunos = list(queryset)
    alunos_filtrados = []
    for aluno in alunos:
        aluno.trajetoria_atual = _trajetoria_referencia_listagem(aluno)
        if not aluno.trajetoria_atual:
            continue
        if nivel and aluno.trajetoria_atual.nivel_curso != nivel:
            continue
        if reingressante == "1" and not aluno.trajetoria_atual.reingressante:
            continue
        if reingressante == "0" and aluno.trajetoria_atual.reingressante:
            continue
        if ingresso_inicio and aluno.trajetoria_atual.ingresso < ingresso_inicio:
            continue
        if ingresso_fim and aluno.trajetoria_atual.ingresso > ingresso_fim:
            continue
        if status_trajetoria and aluno.trajetoria_atual.status != status_trajetoria:
            continue
        aluno.status_listagem = _status_trajetoria_display(aluno.trajetoria_atual)
        alunos_filtrados.append(aluno)

    return render(
        request,
        "processos/alunos_lista.html",
        {
            "alunos": alunos_filtrados,
            "filtro_nome": nome,
            "filtro_ingresso_inicio": ingresso_inicio_raw,
            "filtro_ingresso_fim": ingresso_fim_raw,
            "filtro_nivel": nivel,
            "nivel_list": Aluno.NivelCurso.choices,
            "filtro_reingressante": reingressante,
            "filtro_status": status,
            "status_list": Aluno.StatusAluno.choices,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def aluno_detalhe_view(request, aluno_id):
    if not _has_gestao_access(request.user):
        raise PermissionDenied("Acesso restrito a coordenadores e servidores.")

    aluno = get_object_or_404(
        Aluno.objects.prefetch_related(
            "trajetorias__orientador",
            "trajetorias__coorientador",
            "trajetorias__estagios_docencia",
        ),
        pk=aluno_id,
    )
    trajetoria_atual = _trajetoria_ativa(aluno)

    if request.method == "POST":
        acao = request.POST.get("acao", "").strip()

        if acao == "alterar_status":
            form = AlunoStatusForm(request.POST)
            if form.is_valid():
                anterior = aluno.get_status_aluno_display()
                novo = form.cleaned_data["status_aluno"]
                aluno.status_aluno = novo
                aluno.save()
                _sincronizar_trajetoria_ativa(aluno)
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
            messages.error(request, "Nao foi possivel alterar o status do aluno.")

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
                messages.error(request, "Nao foi possivel atualizar os dados do aluno.")

        elif acao == "editar_trajetoria":
            form = TrajetoriaAcademicaForm(request.POST)
            trajetoria = aluno.trajetorias.filter(id=request.POST.get("trajetoria_id")).first()
            if not trajetoria:
                messages.error(request, "Trajetoria academica nao encontrada.")
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
                    messages.success(request, "Trajetoria academica atualizada.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            else:
                messages.error(request, "Nao foi possivel atualizar a trajetoria academica.")

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
                        valor_anterior="Sem trajetoria",
                        valor_novo=_trajetoria_label(trajetoria),
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Nova trajetoria academica cadastrada.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            else:
                messages.error(request, "Nao foi possivel cadastrar a trajetoria academica.")

        elif acao == "alterar_trajetoria_campo":
            trajetoria = aluno.trajetorias.filter(id=request.POST.get("trajetoria_id")).first()
            campo = request.POST.get("campo", "").strip()
            comentario = request.POST.get("comentario", "").strip()
            if not trajetoria:
                messages.error(request, "Trajetoria academica nao encontrada.")
            elif not comentario:
                messages.error(request, "Informe um comentario para registrar a alteracao.")
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
                            raise ValidationError("Nivel de curso invalido.")
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
                            raise ValidationError("Tipo de coorientador invalido.")
                    elif campo == "defesa":
                        trajetoria.numero_defesa = request.POST.get("numero_defesa", "").strip()
                        data_defesa = request.POST.get("data_defesa", "").strip()
                        trajetoria.data_defesa = data_defesa or None
                        if trajetoria.numero_defesa or trajetoria.data_defesa:
                            trajetoria.status = TrajetoriaAcademica.Status.CONCLUIDA
                    elif campo == "deposito_versao_final":
                        trajetoria.deposito_versao_final = request.POST.get("deposito_versao_final") == "on"
                    else:
                        raise ValidationError("Campo de trajetoria invalido.")

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
                    messages.success(request, "Informacao da trajetoria atualizada.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)

        elif acao == "novo_estagio_docencia":
            # Aqui usamos o novo form que recebe o ID da trajetória
            form = NovoEstagioDocenciaForm(request.POST)
            
            if form.is_valid():
                trajetoria_id = form.cleaned_data["trajetoria_id"]
                trajetoria = get_object_or_404(TrajetoriaAcademica, id=trajetoria_id)
                supervisor_digitado = form.cleaned_data["supervisor"]

                # 1. Máquina de estados (Compara com o orientador da trajetória)
                nome_orientador = trajetoria.orientador.nome if trajetoria.orientador else ""
                
                if supervisor_digitado.strip().lower() == nome_orientador.strip().lower() and nome_orientador:
                    status_inicial = EstagioDocencia.Status.AGUARD_ASSINATURA
                else:
                    status_inicial = EstagioDocencia.Status.AGUARD_CIENCIA

                # 2. Criação oficial do Estágio no banco de dados
                novo_estagio = EstagioDocencia.objects.create(
                    trajetoria=trajetoria,
                    supervisor=supervisor_digitado,
                    status=status_inicial,
                    inicio=form.cleaned_data.get("inicio"),
                    termino=form.cleaned_data.get("termino")
                )

                estado_novo = (
                    f"Status: {novo_estagio.get_status_display()} | Supervisor: {novo_estagio.supervisor} | "
                    f"Início: {novo_estagio.inicio} | Término: {novo_estagio.termino}"
                )
                
                # 3. Auditoria
                _registrar_alteracao_aluno(
                    aluno=aluno, 
                    tipo="Criação - Estágio de Docência",
                    valor_anterior="Nenhum estágio",
                    valor_novo=estado_novo,
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user
                )
                messages.success(request, "Novo estágio de docência criado e fluxo iniciado.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)


        elif acao == "Editar Estágio Docência":
            form = EstagioDocenciaUpdateForm(request.POST)

            if form.is_valid():
                estagio_id = form.cleaned_data["estagio_id"]
                estagio = get_object_or_404(EstagioDocencia, id=estagio_id)

                estado_anterior = (
                    f"Supervisor: {estagio.supervisor} | Status: {estagio.get_status_display()} | "
                    f"Início: {estagio.inicio} | Término: {estagio.termino}"
                )

                estagio.supervisor = form.cleaned_data["supervisor"]
                estagio.status = form.cleaned_data["status"]
                estagio.inicio = form.cleaned_data["inicio"]
                estagio.termino = form.cleaned_data["termino"]
                estagio.save()

                estado_novo = (
                    f"Supervisor: {estagio.supervisor} | Status: {estagio.get_status_display()} | "
                    f"Início: {estagio.inicio} | Término: {estagio.termino}"
                )

                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo="Edição Manual - Estágio de Docência",
                    valor_anterior=estado_anterior,
                    valor_novo=estado_novo,
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user
                )
                messages.success(request, "Estágio de docência atualizado com sucesso.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)

        elif acao == "alterar_qualificacao":
            form = AlunoQualificacaoForm(request.POST)
            if form.is_valid():
                anterior = "Sim" if aluno.isQualificado else "Nao"
                aluno.isQualificado = form.cleaned_data["isQualificado"]
                aluno.save()
                _sincronizar_trajetoria_ativa(aluno)
                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo=AlteracaoAluno.TipoAlteracao.QUALIFICACAO,
                    valor_anterior=anterior,
                    valor_novo="Sim" if aluno.isQualificado else "Nao",
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user,
                )
                messages.success(request, f"{aluno.qualificacao_label} atualizado.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, f"Nao foi possivel atualizar {aluno.qualificacao_label_lower}.")

        elif acao == "alterar_prazo_qualificacao":
            form = AlunoPrazoForm(request.POST)
            if form.is_valid():
                semestre = form.cleaned_data["valor_semestre"].strip()
                if not _semestre_valido(semestre):
                    form.add_error("valor_semestre", "Informe no formato YYYY.1 ou YYYY.2.")
                else:
                    anterior = aluno.prazo_qualificacao or "-"
                    aluno.prazo_qualificacao = semestre
                    aluno.save()
                    _sincronizar_trajetoria_ativa(aluno)
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.PRAZO_QUALIFICACAO,
                        valor_anterior=anterior,
                        valor_novo=aluno.prazo_qualificacao,
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, f"Prazo de {aluno.qualificacao_label_lower} atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, f"Nao foi possivel atualizar o prazo de {aluno.qualificacao_label_lower}.")

        elif acao == "alterar_prazo_defesa":
            form = AlunoPrazoForm(request.POST)
            if form.is_valid():
                semestre = form.cleaned_data["valor_semestre"].strip()
                if not _semestre_valido(semestre):
                    form.add_error("valor_semestre", "Informe no formato YYYY.1 ou YYYY.2.")
                else:
                    anterior = aluno.prazo_defesa or "-"
                    aluno.prazo_defesa = semestre
                    aluno.save()
                    _sincronizar_trajetoria_ativa(aluno)
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.PRAZO_DEFESA,
                        valor_anterior=anterior,
                        valor_novo=aluno.prazo_defesa,
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Prazo de defesa atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel atualizar o prazo de defesa.")

        elif acao == "registrar_reingresso":
            form = AlunoReingressoForm(request.POST)
            if form.is_valid():
                ingresso = form.cleaned_data["ingresso"].strip()
                prazo_qualificacao = form.cleaned_data["prazo_qualificacao"].strip()
                prazo_defesa = form.cleaned_data["prazo_defesa"].strip()
                for field_name, value in {
                    "ingresso": ingresso,
                    "prazo_qualificacao": prazo_qualificacao,
                    "prazo_defesa": prazo_defesa,
                }.items():
                    if not _semestre_valido(value):
                        form.add_error(field_name, "Informe no formato YYYY.1 ou YYYY.2.")

                if not form.errors:
                    anterior = _prazos_academicos_label(aluno)
                    aluno.reingressante = True
                    aluno.ingresso = ingresso
                    aluno.prazo_qualificacao = prazo_qualificacao
                    aluno.prazo_defesa = prazo_defesa
                    aluno.save()
                    _sincronizar_trajetoria_ativa(aluno)
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.REINGRESSO,
                        valor_anterior=anterior,
                        valor_novo=_prazos_academicos_label(aluno),
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Reingresso registrado e prazos redefinidos.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel registrar o reingresso.")

        elif acao == "iniciar_doutorado":
            form = AlunoIniciarDoutoradoForm(request.POST)
            if form.is_valid():
                ingresso = form.cleaned_data["ingresso"].strip()
                prazo_qualificacao = form.cleaned_data["prazo_qualificacao"].strip()
                prazo_defesa = form.cleaned_data["prazo_defesa"].strip()
                for field_name, value in {
                    "ingresso": ingresso,
                    "prazo_qualificacao": prazo_qualificacao,
                    "prazo_defesa": prazo_defesa,
                }.items():
                    if not _semestre_valido(value):
                        form.add_error(field_name, "Informe no formato YYYY.1 ou YYYY.2.")

                if not trajetoria_atual or trajetoria_atual.nivel_curso != Aluno.NivelCurso.MESTRADO:
                    form.add_error("ingresso", "Apenas aluno de mestrado pode iniciar doutorado por esta acao.")

                if not form.errors:
                    trajetoria_mestrado = trajetoria_atual
                    anterior = _trajetoria_label(trajetoria_mestrado)
                    TrajetoriaAcademica.objects.filter(id=trajetoria_mestrado.id).update(
                        status=TrajetoriaAcademica.Status.CONCLUIDA,
                        atualizado_em=timezone.now(),
                    )

                    aluno.status_aluno = Aluno.StatusAluno.ATIVO
                    aluno.save()

                    trajetoria_doutorado = TrajetoriaAcademica.objects.create(
                        aluno=aluno,
                        nivel_curso=Aluno.NivelCurso.DOUTORADO,
                        status=TrajetoriaAcademica.Status.ATIVA,
                        ingresso=ingresso,
                        prazo_qualificacao=prazo_qualificacao,
                        prazo_defesa=prazo_defesa,
                        orientador=form.cleaned_data["orientador"],
                    )
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
                        valor_anterior=anterior,
                        valor_novo=_trajetoria_label(trajetoria_doutorado),
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Mestrado concluido e doutorado iniciado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel iniciar a trajetoria de doutorado.")

        elif acao == "registrar_defesa":
            form = AlunoDefesaForm(request.POST)
            if form.is_valid():
                anterior_numero = aluno.numero_defesa or "-"
                anterior_data = aluno.data_defesa.isoformat() if aluno.data_defesa else "-"
                aluno.numero_defesa = form.cleaned_data["numero_defesa"]
                aluno.data_defesa = form.cleaned_data["data_defesa"]
                aluno.status_aluno = Aluno.StatusAluno.DEFENDEU
                aluno.save()
                _sincronizar_trajetoria_ativa(aluno)
                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo=AlteracaoAluno.TipoAlteracao.DEFESA,
                    valor_anterior=f"numero={anterior_numero};data={anterior_data}",
                    valor_novo=f"numero={aluno.numero_defesa};data={aluno.data_defesa.isoformat()}",
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user,
                )
                messages.success(request, "Defesa registrada com sucesso.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel registrar a defesa.")

        elif acao == "registrar_deposito_final":
            form = AlunoDepositoFinalForm(request.POST)
            if form.is_valid():
                if aluno.status_aluno != Aluno.StatusAluno.DEFENDEU:
                    form.add_error("deposito_versao_final", "O aluno precisa estar com status Defendeu.")
                else:
                    anterior = "Sim" if aluno.deposito_versao_final else "Nao"
                    aluno.deposito_versao_final = form.cleaned_data["deposito_versao_final"]
                    aluno.save()
                    _sincronizar_trajetoria_ativa(aluno)
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.DEPOSITO_FINAL,
                        valor_anterior=anterior,
                        valor_novo="Sim" if aluno.deposito_versao_final else "Nao",
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Registro de deposito da versao final atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel atualizar o deposito da versao final.")

        elif acao == "alterar_orientador":
            form = AlunoOrientadorForm(request.POST)
            if form.is_valid():
                orientador = form.cleaned_data["orientador"]
                anterior = _docente_label(aluno.orientador)
                aluno.orientador = orientador
                try:
                    aluno.save()
                except ValidationError as exc:
                    messages.error(request, exc.message_dict if hasattr(exc, "message_dict") else str(exc))
                else:
                    _sincronizar_trajetoria_ativa(aluno)
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.ORIENTADOR,
                        valor_anterior=anterior,
                        valor_novo=_docente_label(aluno.orientador),
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Orientador atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            else:
                messages.error(request, "Nao foi possivel atualizar o orientador.")

        elif acao == "alterar_coorientador":
            form = AlunoCoorientadorForm(request.POST)
            if form.is_valid():
                anterior = _coorientador_label(aluno)
                tipo_coorientador = form.cleaned_data["tipo_coorientador"]

                aluno.coorientador = None
                aluno.coorientador_externo_nome = ""
                aluno.coorientador_externo_email = ""
                aluno.coorientador_externo_instituicao = ""

                if tipo_coorientador == AlunoCoorientadorForm.TipoCoorientador.CADASTRADO:
                    aluno.coorientador = form.cleaned_data["coorientador"]
                elif tipo_coorientador == AlunoCoorientadorForm.TipoCoorientador.EXTERNO:
                    aluno.coorientador_externo_nome = form.cleaned_data["coorientador_externo_nome"].strip()
                    aluno.coorientador_externo_email = form.cleaned_data["coorientador_externo_email"].strip()
                    aluno.coorientador_externo_instituicao = form.cleaned_data[
                        "coorientador_externo_instituicao"
                    ].strip()

                try:
                    aluno.save()
                except ValidationError as exc:
                    messages.error(request, exc.message_dict if hasattr(exc, "message_dict") else str(exc))
                else:
                    _sincronizar_trajetoria_ativa(aluno)
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.COORIENTADOR,
                        valor_anterior=anterior,
                        valor_novo=_coorientador_label(aluno),
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Coorientador atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            else:
                messages.error(request, "Nao foi possivel atualizar o coorientador.")
                
                
    """
        elif acao == "Novo Estágio Docência":
            form = GatilhoInicialForm(request.POST) # Usando o novo form de Gatilho
            
            if form.is_valid():
                estagio_id = form.cleaned_data["estagio_id"]
                estagio = get_object_or_404(EstagioDocencia, id=estagio_id)
                trajetoria_estagio = estagio.trajetoria
                
                estado_anterior = f"Status: {estagio.get_status_display()} | Supervisor: Nenhum"
                
                supervisor_digitado = form.cleaned_data["supervisor"]
                estagio.supervisor = supervisor_digitado

                # Máquina de estados
                nome_orientador = trajetoria_estagio.orientador.nome if trajetoria_estagio.orientador else ""
                
                if supervisor_digitado.strip().lower() == nome_orientador.strip().lower() and nome_orientador:
                    estagio.status = EstagioDocencia.Status.AGUARD_ASSINATURA
                else:
                    estagio.status = EstagioDocencia.Status.AGUARD_CIENCIA

                estagio.save()
                estado_novo = f"Status: {estagio.get_status_display()} | Supervisor: {estagio.supervisor}"
                
                _registrar_alteracao_aluno(
                    aluno=aluno, 
                    tipo="Gatilho Estágio de Docência",
                    valor_anterior=estado_anterior,
                    valor_novo=estado_novo,
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user
                )
                messages.success(request, "Fluxo de Estágio de Docência iniciado.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)


        elif acao == "Editar Estágio Docência":
            form = EstagioDocenciaForm(request.POST) # Usando o form Supremo

            if form.is_valid():
                estagio_id = form.cleaned_data["estagio_id"]
                estagio = get_object_or_404(EstagioDocencia, id=estagio_id)

                estado_anterior = (
                    f"Supervisor: {estagio.supervisor} | Status: {estagio.get_status_display()} | "
                    f"Início: {estagio.inicio} | Término: {estagio.termino}"
                )

                estagio.supervisor = form.cleaned_data["supervisor"]
                estagio.status = form.cleaned_data["status"]
                estagio.inicio = form.cleaned_data["inicio"]
                estagio.termino = form.cleaned_data["termino"]
                
                estagio.save()

                estado_novo = (
                    f"Supervisor: {estagio.supervisor} | Status: {estagio.get_status_display()} | "
                    f"Início: {estagio.inicio} | Término: {estagio.termino}"
                )

                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo="Edição Manual - Estágio de Docência",
                    valor_anterior=estado_anterior,
                    valor_novo=estado_novo,
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user
                )
                messages.success(request, "Estágio de docência corrigido manualmente.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
     """       
    processos_aluno = (
        Processo.objects.select_related("setor_atual")
        .filter(usuario_criado_por=aluno)
        .order_by("-data_criacao")
    )
    trajetorias_aluno = aluno.trajetorias.select_related("orientador", "coorientador").all()
    alteracoes_aluno = aluno.alteracoes.select_related("alterado_por").all()
    trajetoria_cards = []
    for trajetoria in trajetorias_aluno:
        tipo_coorientador = TrajetoriaAcademicaForm.TipoCoorientador.NENHUM
        if trajetoria.coorientador:
            tipo_coorientador = TrajetoriaAcademicaForm.TipoCoorientador.CADASTRADO
        elif trajetoria.coorientador_externo_nome:
            tipo_coorientador = TrajetoriaAcademicaForm.TipoCoorientador.EXTERNO
        form = TrajetoriaAcademicaForm(
            initial={
                "trajetoria_id": trajetoria.id,
                "nivel_curso": trajetoria.nivel_curso,
                "status": trajetoria.status,
                "ingresso": trajetoria.ingresso,
                "prazo_qualificacao": trajetoria.prazo_qualificacao,
                "prazo_defesa": trajetoria.prazo_defesa,
                "reingressante": trajetoria.reingressante,
                "isQualificado": trajetoria.isQualificado,
                "orientador": trajetoria.orientador,
                "tipo_coorientador": tipo_coorientador,
                "coorientador": trajetoria.coorientador,
                "coorientador_externo_nome": trajetoria.coorientador_externo_nome,
                "coorientador_externo_email": trajetoria.coorientador_externo_email,
                "coorientador_externo_instituicao": trajetoria.coorientador_externo_instituicao,
                "numero_defesa": trajetoria.numero_defesa,
                "data_defesa": trajetoria.data_defesa,
                "deposito_versao_final": trajetoria.deposito_versao_final,
            }
        )
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
                "form": form,
                "estagio_cards": estagio_cards,
                "novo_estagio_form": EstagioDocenciaUpdateForm(
                    initial={"trajetoria_id": trajetoria.id}
                ),
            }
        )
    return render(
        request,
        "processos/aluno_detalhe.html",
        {
            "aluno": aluno,
            "trajetoria_atual": _trajetoria_ativa(aluno),
            "processos_aluno": processos_aluno,
            "trajetorias_aluno": trajetorias_aluno,
            "trajetoria_cards": trajetoria_cards,
            "alteracoes_aluno": alteracoes_aluno,
            "alteracoes_display": [_alteracao_aluno_display(alteracao) for alteracao in alteracoes_aluno],
            "dados_form": AlunoDadosForm(
                aluno=aluno,
                initial={
                    "nome": aluno.nome,
                    "email": aluno.email,
                    "matricula": aluno.matricula,
                },
            ),
            "status_form": AlunoStatusForm(initial={"status_aluno": aluno.status_aluno}),
            "nova_trajetoria_form": TrajetoriaAcademicaForm(
                initial={
                    "status": TrajetoriaAcademica.Status.ATIVA,
                    "tipo_coorientador": TrajetoriaAcademicaForm.TipoCoorientador.NENHUM,
                }
            ),
            "iniciar_doutorado_form": AlunoIniciarDoutoradoForm(
                initial={
                    "ingresso": trajetoria_atual.ingresso if trajetoria_atual else "",
                    "prazo_qualificacao": trajetoria_atual.prazo_qualificacao if trajetoria_atual else "",
                    "prazo_defesa": trajetoria_atual.prazo_defesa if trajetoria_atual else "",
                    "orientador": trajetoria_atual.orientador if trajetoria_atual else None,
                }
            ),
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

    nomes_setores = _nomes_setores_caixa(request.user)
    selected_caixa = request.GET.get("caixa", "").strip().upper()
    status_caixa = request.GET.get("status_caixa", "").strip().upper()
    if status_caixa not in {"AGUARDANDO_CIENCIA", "EM_ANALISE"}:
        status_caixa = "EM_ANALISE"

    opcoes_caixa = []
    if _is_coordenador(request.user):
        opcoes_caixa = [
            {"value": "COORDENACAO", "label": "Coordenação", "setor_nome": "Coordenação PPG"},
            {"value": "PLENO", "label": "Pleno", "setor_nome": "Colegiando PPGEC (Pleno)"},
        ]
        if selected_caixa == "COORDENACAO":
            nomes_setores = ["Coordenação PPG"]
        elif selected_caixa == "PLENO":
            nomes_setores = ["Colegiando PPGEC (Pleno)"]
        else:
            selected_caixa = "COORDENACAO"
            nomes_setores = ["Coordenação PPG"]

    processos_caixa = (
        Processo.objects.select_related("usuario_criado_por", "setor_atual")
        .filter(setor_atual__nome__in=nomes_setores)
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
            "nomes_setores_caixa": nomes_setores,
            "nomes_setores_caixa_texto": ", ".join(nomes_setores),
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
            "documentos__enviado_por",
            "comentarios__autor",
            "manifestacoes__responsavel",
            "manifestacoes__solicitado_por",
            "tramitacoes__setor_origem",
            "tramitacoes__setor_destino",
            "tramitacoes__encaminhado_por",
        ),
        id=processo_id,
    )
    if not _can_view_processo_detalhe(request.user, processo):
        raise PermissionDenied("Acesso restrito ao dono do processo ou perfis de gestao.")

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
    open_documento_modal = False
    open_encaminhamento_modal = False
    open_ciente_modal = False
    open_finalizar_modal = False
    solicitar_ciente_form = SolicitarCienteOrientadorForm()
    manifestar_ciente_form = ManifestarCienteOrientadorForm()
    finalizar_form = FinalizarProcessoForm()

    if request.method == "POST":
        if "acao_rapida" in request.POST and can_manage_in_caixa:
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
                raise PermissionDenied("Voce nao pode adicionar documento neste processo.")
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
                raise PermissionDenied("Voce nao pode solicitar ciente do orientador neste processo.")
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
                    messages.success(request, "Solicitacao de ciente do orientador registrada.")
                    send_email_solicitacao_ciencia.delay(manifestacao.id)
                    return redirect("processo_detalhe", processo_id=processo.id)
            open_ciente_modal = True
        elif "manifestar_ciente_orientador" in request.POST:
            if not can_manifestar_ciente:
                raise PermissionDenied("Voce nao pode se manifestar neste ciente.")
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
                    messages.success(request, "Manifestacao registrada com sucesso.")

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
                raise PermissionDenied("Voce nao pode encaminhar este processo.")
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
                try:
                    despacho_texto = encaminhamento_form.cleaned_data["despacho"]
                    processo.encaminhar(
                        setor_destino=setor_destino,
                        encaminhado_por=request.user,
                        observacao=encaminhamento_form.cleaned_data["despacho"],
                        status_resultante=status_resultante,
                    )
                except ValidationError as exc:
                    messages.error(request, str(exc))
                else:
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
                raise PermissionDenied("Voce nao pode finalizar este processo.")
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
                messages.error(request, "Documento nao encontrado para remocao.")
            else:
                pode_remover = (
                    request.user.id == documento.enviado_por_id or _can_manage_restricted_docs(request.user)
                )
                if not pode_remover:
                    raise PermissionDenied("Voce nao tem permissao para remover este arquivo.")
                try:
                    documento.remover_arquivo(removido_por=request.user, motivo=motivo_remocao)
                except ValidationError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, "Arquivo removido com sucesso.")
                    return redirect("processo_detalhe", processo_id=processo.id)
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

                if _is_processo_no_pleno(processo):#verificação de segurança
                    send_email_processo_comentado_pleno.delay(processo.id, comentario_intervencao.id)

                messages.success(request, "Comentario adicionado com sucesso.")
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

    return render(
        request,
        "processos/processo_detalhe.html",
        {
            "processo": processo,
            "documentos_exibicao": documentos_exibicao,
            "can_manage_in_caixa": can_manage_in_caixa,
            "can_manage_requerente": can_manage_requerente,
            "can_manage_processo_actions": can_manage_processo_actions,
            "can_add_documento": can_add_documento,
            "can_encaminhar_processo": can_encaminhar_processo,
            "can_finalizar_processo": can_finalizar_processo,
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
            "open_documento_modal": open_documento_modal,
            "open_encaminhamento_modal": open_encaminhamento_modal,
            "open_ciente_modal": open_ciente_modal,
            "open_finalizar_modal": open_finalizar_modal,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def novo_processo_view(request):
    if _is_servidor(request.user):
        raise PermissionDenied("Perfil SERVIDOR nao pode abrir processo.")

    if request.method == "POST":
        form = ProcessoAberturaForm(request.POST, request.FILES)
        if form.is_valid():
            setor_secretaria = Setor.objects.filter(nome="Secretaria PPGEC", ativo=True).first()
            if not setor_secretaria:
                messages.error(
                    request,
                    "Setor inicial 'Secretaria PPGEC' nao encontrado. Contate o administrador.",
                )
            else:
                processo = form.save(commit=False)
                processo.usuario_criado_por = request.user
                processo.setor_atual = setor_secretaria
                processo.status = Processo.StatusProcesso.EM_ANALISE
                processo.save()

                doc_indices = set()
                for key in request.POST.keys():
                    match = re.match(r"^doc_(\d+)_titulo$", key)
                    if match:
                        doc_indices.add(int(match.group(1)))

                for idx in sorted(doc_indices):
                    titulo = (request.POST.get(f"doc_{idx}_titulo") or "").strip()
                    tipo_documento = (request.POST.get(f"doc_{idx}_tipo_documento") or "").strip()
                    restricao_tipo = (request.POST.get(f"doc_{idx}_restricao_tipo") or "").strip()
                    arquivo = request.FILES.get(f"doc_{idx}_arquivo")

                    if not (titulo and tipo_documento and restricao_tipo and arquivo):
                        continue

                    processo.adicionar_documento(
                        titulo=titulo,
                        arquivo=arquivo,
                        tipo_documento=tipo_documento,
                        restricao_tipo=restricao_tipo,
                        enviado_por=request.user,
                    )

                # dispara task de forma assincrona
                send_email_novo_processo_aluno.delay(processo.id)
                send_email_novo_processo_orientador.delay(processo.id)
                send_email_novo_processo_secretaria.delay(processo.id)

                messages.success(request, f"Processo {processo.numero} aberto com sucesso.")
                return redirect("home")
    else:
        form = ProcessoAberturaForm()

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


@login_required
def aluno_documento_vinculo_view(request):
    if request.user.tipo_usuario != User.TipoUsuario.ALUNO:
        raise PermissionDenied("Acesso restrito a alunos.")
    return render(
        request,
        "processos/aluno_documento_todo.html",
        {
            "titulo": "Documento de vínculo",
            "descricao": "TODO: disponibilizar emissao do documento de vinculo.",
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
            "descricao": "TODO: disponibilizar emissao do historico do aluno.",
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
def menu_meus_processos_view(request):
    if request.user.tipo_usuario == User.TipoUsuario.SERVIDOR:
        raise PermissionDenied("Perfil SERVIDOR nao possui meus processos.")

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

    orientandos = Aluno.objects.filter(
        trajetorias__status=TrajetoriaAcademica.Status.ATIVA,
    ).filter(
        Q(trajetorias__orientador=request.user) | Q(trajetorias__coorientador=request.user)
    ).distinct()
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

    trajetorias_base = TrajetoriaAcademica.objects.select_related("aluno", "orientador", "coorientador").order_by(
        "aluno__nome", "-criado_em"
    )
    orientacoes_ativas = trajetorias_base.filter(
        status=TrajetoriaAcademica.Status.ATIVA,
        orientador=request.user,
    )
    coorientacoes_ativas = trajetorias_base.filter(
        status=TrajetoriaAcademica.Status.ATIVA,
        coorientador=request.user,
    ).exclude(orientador=request.user)
    vinculos_concluidos = trajetorias_base.filter(
        status=TrajetoriaAcademica.Status.CONCLUIDA,
    ).filter(Q(orientador=request.user) | Q(coorientador=request.user))

    return render(
        request,
        "processos/menu_meus_orientandos.html",
        {
            "orientacoes_ativas": orientacoes_ativas,
            "coorientacoes_ativas": coorientacoes_ativas,
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
    if request.user.tipo_usuario != User.TipoUsuario.DOCENTE:
        raise PermissionDenied("Acesso restrito a docentes.")

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