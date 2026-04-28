import re

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    AlunoComentarioForm,
    AlunoDefesaForm,
    AlunoDepositoFinalForm,
    AlunoPrazoForm,
    AlunoQualificacaoForm,
    AlunoStatusForm,
    ManifestarCienteOrientadorForm,
    ComentarioProcessoForm,
    DocumentoCadastroForm,
    EncaminhamentoForm,
    FinalizarProcessoForm,
    ProcessoAberturaForm,
    SolicitarCienteOrientadorForm,
    UserProfileForm,
)
from .models import (
    AlteracaoAluno,
    Aluno,
    ComentarioProcesso,
    Docente,
    Documento,
    ManifestacaoProcesso,
    Processo,
    Setor,
    User,
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
        return Aluno.objects.filter(pk=processo.usuario_criado_por_id, orientador=user).exists()

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
            {"label": "Ciencias manifestadas", "href": "/menu/ciencias-manifestadas/"},
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
        orientandos = Aluno.objects.filter(orientador=request.user).order_by("nome")
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

    docentes = (
        Docente.objects.prefetch_related("orientandos")
        .annotate(total_orientandos=Count("orientandos"))
        .order_by("-total_orientandos", "nome")
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

    queryset = Aluno.objects.select_related("orientador").order_by("nome")
    nome = request.GET.get("nome", "").strip()
    ingresso_inicio_raw = request.GET.get("ingresso_inicio", "").strip()
    ingresso_fim_raw = request.GET.get("ingresso_fim", "").strip()
    status = request.GET.get("status", "").strip().upper()

    if nome:
        queryset = queryset.filter(nome__icontains=nome)

    ingresso_inicio = ingresso_inicio_raw if _semestre_valido(ingresso_inicio_raw) else ""
    ingresso_fim = ingresso_fim_raw if _semestre_valido(ingresso_fim_raw) else ""

    if ingresso_inicio:
        queryset = queryset.filter(ingresso__gte=ingresso_inicio)
    if ingresso_fim:
        queryset = queryset.filter(ingresso__lte=ingresso_fim)

    if status:
        queryset = queryset.filter(status_aluno=status)

    return render(
        request,
        "processos/alunos_lista.html",
        {
            "alunos": queryset,
            "filtro_nome": nome,
            "filtro_ingresso_inicio": ingresso_inicio_raw,
            "filtro_ingresso_fim": ingresso_fim_raw,
            "filtro_status": status,
            "status_list": (
                Aluno.StatusAluno.choices
            ),
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

    aluno = get_object_or_404(Aluno.objects.select_related("orientador"), pk=aluno_id)

    if request.method == "POST":
        acao = request.POST.get("acao", "").strip()

        if acao == "alterar_status":
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
            messages.error(request, "Nao foi possivel alterar o status do aluno.")

        elif acao == "alterar_qualificacao":
            form = AlunoQualificacaoForm(request.POST)
            if form.is_valid():
                anterior = "Sim" if aluno.isQualificado else "Nao"
                aluno.isQualificado = form.cleaned_data["isQualificado"]
                aluno.save()
                _registrar_alteracao_aluno(
                    aluno=aluno,
                    tipo=AlteracaoAluno.TipoAlteracao.QUALIFICACAO,
                    valor_anterior=anterior,
                    valor_novo="Sim" if aluno.isQualificado else "Nao",
                    comentario=form.cleaned_data["comentario"],
                    alterado_por=request.user,
                )
                messages.success(request, "Qualificacao do aluno atualizada.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel atualizar a qualificacao.")

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
                    _registrar_alteracao_aluno(
                        aluno=aluno,
                        tipo=AlteracaoAluno.TipoAlteracao.PRAZO_QUALIFICACAO,
                        valor_anterior=anterior,
                        valor_novo=aluno.prazo_qualificacao,
                        comentario=form.cleaned_data["comentario"],
                        alterado_por=request.user,
                    )
                    messages.success(request, "Prazo de qualificacao atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel atualizar o prazo de qualificacao.")

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

        elif acao == "registrar_defesa":
            form = AlunoDefesaForm(request.POST)
            if form.is_valid():
                anterior_numero = aluno.numero_defesa or "-"
                anterior_data = aluno.data_defesa.isoformat() if aluno.data_defesa else "-"
                aluno.numero_defesa = form.cleaned_data["numero_defesa"]
                aluno.data_defesa = form.cleaned_data["data_defesa"]
                aluno.status_aluno = Aluno.StatusAluno.DEFENDEU
                aluno.save()
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

    processos_aluno = (
        Processo.objects.select_related("setor_atual")
        .filter(usuario_criado_por=aluno)
        .order_by("-data_criacao")
    )
    return render(
        request,
        "processos/aluno_detalhe.html",
        {
            "aluno": aluno,
            "processos_aluno": processos_aluno,
            "alteracoes_aluno": aluno.alteracoes.select_related("alterado_por").all(),
            "status_form": AlunoStatusForm(initial={"status_aluno": aluno.status_aluno}),
            "qualificacao_form": AlunoQualificacaoForm(initial={"isQualificado": aluno.isQualificado}),
            "prazo_qualificacao_form": AlunoPrazoForm(initial={"valor_semestre": aluno.prazo_qualificacao}),
            "prazo_defesa_form": AlunoPrazoForm(initial={"valor_semestre": aluno.prazo_defesa}),
            "defesa_form": AlunoDefesaForm(
                initial={
                    "numero_defesa": aluno.numero_defesa,
                    "data_defesa": aluno.data_defesa,
                }
            ),
            "deposito_final_form": AlunoDepositoFinalForm(
                initial={"deposito_versao_final": aluno.deposito_versao_final}
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
                return redirect("processo_detalhe", processo_id=processo.id)
            if acao_rapida == "indeferir":
                processo.indeferir()
                messages.success(request, "Processo indeferido.")
                return redirect("processo_detalhe", processo_id=processo.id)
            if acao_rapida == "arquivar":
                processo.finalizar(
                    termo_finalizacao="Processo arquivado.",
                    status_final=Processo.StatusProcesso.FINALIZADO,
                )
                messages.success(request, "Processo arquivado.")
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
                    processo.solicitar_ciente_orientador(
                        solicitado_por=request.user,
                        mensagem_solicitacao=solicitar_ciente_form.cleaned_data["mensagem_solicitacao"],
                    )
                except ValidationError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, "Solicitacao de ciente do orientador registrada.")
                    return redirect("processo_detalhe", processo_id=processo.id)
            open_ciente_modal = True
        elif "manifestar_ciente_orientador" in request.POST:
            if not can_manifestar_ciente:
                raise PermissionDenied("Voce nao pode se manifestar neste ciente.")
            manifestar_ciente_form = ManifestarCienteOrientadorForm(request.POST)
            acao = (request.POST.get("acao_ciente") or "").strip().lower()
            if manifestar_ciente_form.is_valid():
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
                ComentarioProcesso.objects.create(
                    processo=processo,
                    autor=request.user,
                    anonimo=comentario_form.cleaned_data["anonimo"],
                    texto=comentario_form.cleaned_data["texto"],
                )
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

    orientandos = Aluno.objects.filter(orientador=request.user)
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

    orientandos = Aluno.objects.filter(orientador=request.user).order_by("nome")
    return render(
        request,
        "processos/menu_meus_orientandos.html",
        {
            "orientandos": orientandos,
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
