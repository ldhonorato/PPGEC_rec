import re
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from .forms import (
    AlunoCadastroForm,
    AlunoComentarioForm,
    AlunoDadosForm,
    AlunoDefesaForm,
    AlunoDepositoFinalForm,
    AlunoIniciarDoutoradoForm,
    AlunoPrazoForm,
    AlunoQualificacaoForm,
    AlunoStatusForm,
    EstagioDocenciaUpdateForm,
    NovoEstagioDocenciaForm,
    ManifestarCienteOrientadorForm,
    ComentarioProcessoForm,
    DisciplinaTrajetoriaForm,
    DisponibilidadeSalaLoteForm,
    DocumentoCadastroForm,
    EncaminhamentoForm,
    FinalizarProcessoForm,
    ProcessoAberturaForm,
    PublicacaoTrajetoriaForm,
    ReservaAmbienteExclusaoForm,
    ReservaAmbienteForm,
    SalaForm,
    SolicitacaoBancaForm,
    SolicitarCienteOrientadorForm,
    SetorComissaoForm,
    TrajetoriaAcademicaForm,
    TrajetoriaStatusForm,
    UserProfileForm,
)
from .models import (
    AlteracaoAluno,
    Aluno,
    DisciplinaTrajetoria,
    DisponibilidadeSala,
    ComentarioProcesso,
    Docente,
    Documento,
    EstagioDocencia,
    ManifestacaoProcesso,
    MembroBanca,
    Polo,
    Processo,
    PublicacaoTrajetoria,
    ReservaAmbiente,
    Sala,
    Setor,
    SetorMembro,
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
        if _is_processo_no_pleno(processo) and _is_membro_setor_nome(user, "Colegiando PPGEC (Pleno)"):
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


def _can_manage_restricted_docs(user):
    return _is_servidor(user) or _is_coordenador(user)


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
            {"label": "Ciencias manifestadas", "href": "/menu/ciencias-manifestadas/"},
            {"label": "Meus Orientandos", "href": "/menu/meus-orientandos/"},
        ]
        if _is_membro_setor_nome(user, "Colegiando PPGEC (Pleno)"):
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


from django.http import JsonResponse
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
    if not (can_edit_setores or _is_servidor(request.user)):
        raise PermissionDenied("Acesso restrito a coordenadores e servidores.")

    setor_editado = None
    setor_id = request.GET.get("editar") if can_edit_setores else None
    if request.method == "POST":
        if not can_edit_setores:
            raise PermissionDenied("Apenas coordenadores podem alterar setores e comissoes.")
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
            messages.success(request, "Participacao encerrada.")
            return redirect("setores_comissoes")

        form = SetorComissaoForm(request.POST, instance=setor_editado)
        if not setor_editado:
            raise PermissionDenied("Use a pagina Criar Comissao para cadastrar novas comissoes.")
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

            messages.success(request, "Setor/comissao salvo com sucesso.")
            return redirect("setores_comissoes")
        messages.error(request, "Nao foi possivel salvar o setor/comissao.")
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

            messages.success(request, "Comissao criada com sucesso.")
            return redirect("setores_comissoes")
        messages.error(request, "Nao foi possivel criar a comissao.")
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
    docentes = (
        Docente.objects.prefetch_related(
            Prefetch(
                "trajetorias_orientadas",
                queryset=trajetorias_ativas,
                to_attr="trajetorias_orientadas_ativas",
            ),
            Prefetch(
                "trajetorias_coorientadas",
                queryset=trajetorias_ativas,
                to_attr="trajetorias_coorientadas_ativas",
            ),
        )
        .annotate(
            total_orientandos=Count(
                "trajetorias_orientadas__aluno",
                filter=Q(trajetorias_orientadas__status=TrajetoriaAcademica.Status.ATIVA),
                distinct=True,
            ),
            total_coorientandos=Count(
                "trajetorias_coorientadas__aluno",
                filter=Q(trajetorias_coorientadas__status=TrajetoriaAcademica.Status.ATIVA),
                distinct=True,
            ),
        )
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


@login_required
def validar_cadastros_alunos_view(request):
    if not _is_servidor(request.user):
        raise PermissionDenied("Acesso restrito a secretaria.")

    if request.method == "POST":
        aluno = get_object_or_404(
            Aluno,
            pk=request.POST.get("aluno_id"),
            status_aluno=Aluno.StatusAluno.EM_AVALIACAO,
        )
        acao = request.POST.get("acao", "").strip()
        trajetorias_em_homologacao = aluno.trajetorias.filter(
            status=TrajetoriaAcademica.Status.EM_HOMOLOGACAO
        )
        if acao == "aprovar":
            aluno.trajetorias.filter(status=TrajetoriaAcademica.Status.ATIVA).update(
                status=TrajetoriaAcademica.Status.CONCLUIDA,
            )
            trajetorias_em_homologacao.update(status=TrajetoriaAcademica.Status.ATIVA)
            aluno.status_aluno = Aluno.StatusAluno.ATIVO
            aluno.save()
            _registrar_alteracao_aluno(
                aluno=aluno,
                tipo=AlteracaoAluno.TipoAlteracao.STATUS,
                valor_anterior="Em avaliacao",
                valor_novo=aluno.get_status_aluno_display(),
                comentario="Cadastro aprovado pela secretaria.",
                alterado_por=request.user,
            )
            messages.success(request, f"Cadastro de {aluno.nome} aprovado.")
        elif acao == "reprovar":
            trajetorias_em_homologacao.update(status=TrajetoriaAcademica.Status.REMOVIDA)
            aluno.status_aluno = Aluno.StatusAluno.DESLIGADO
            aluno.save()
            _registrar_alteracao_aluno(
                aluno=aluno,
                tipo=AlteracaoAluno.TipoAlteracao.STATUS,
                valor_anterior="Em avaliacao",
                valor_novo=aluno.get_status_aluno_display(),
                comentario="Cadastro reprovado pela secretaria.",
                alterado_por=request.user,
            )
            messages.success(request, f"Cadastro de {aluno.nome} reprovado.")
        else:
            messages.error(request, "Acao invalida para validacao de cadastro.")
        return redirect("validar_cadastros_alunos")

    alunos_pendentes = []
    queryset = (
        Aluno.objects.filter(status_aluno=Aluno.StatusAluno.EM_AVALIACAO)
        .prefetch_related("trajetorias__orientador", "trajetorias__coorientador")
        .order_by("date_joined", "nome")
    )
    for aluno in queryset:
        trajetoria_atual = aluno.trajetoria_ativa()
        if not trajetoria_atual:
            trajetoria_atual = aluno.trajetorias.order_by("-criado_em").first()
        aluno.trajetoria_atual = trajetoria_atual
        alunos_pendentes.append(aluno)

    return render(
        request,
        "processos/validar_cadastros_alunos.html",
        {
            "alunos_pendentes": alunos_pendentes,
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

    queryset = Aluno.objects.prefetch_related("trajetorias__orientador").order_by("nome")
    nome = request.GET.get("nome", "").strip()
    nivel = request.GET.get("nivel", "").strip().upper()
    ingresso_inicio_raw = request.GET.get("ingresso_inicio", "").strip()
    ingresso_fim_raw = request.GET.get("ingresso_fim", "").strip()
    status = request.GET.get("status", "").strip().upper()

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

    alunos = list(queryset.distinct())
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
            "filtro_nome": nome,
            "filtro_nivel": nivel,
            "filtro_ingresso_inicio": ingresso_inicio_raw,
            "filtro_ingresso_fim": ingresso_fim_raw,
            "filtro_status": status,
            "status_list": Aluno.StatusAluno.choices,
            "nivel_list": Aluno.NivelCurso.choices,
            "is_coordenador": _is_coordenador(request.user),
            "has_gestao_access": _has_gestao_access(request.user),
            "can_view_dashboard": _can_view_dashboard(request.user),
            "can_view_processos": _can_view_processos(request.user),
            "can_view_caixa": _can_view_caixa(request.user),
        },
    )


@login_required
def aluno_detalhe_view(request, aluno_id):
    can_manage_aluno = _has_gestao_access(request.user)
    is_self_aluno = request.user.tipo_usuario == User.TipoUsuario.ALUNO and request.user.id == aluno_id
    if not (can_manage_aluno or is_self_aluno):
        raise PermissionDenied("Acesso restrito ao aluno, coordenadores e servidores.")

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
        if not can_manage_aluno and acao not in {"salvar_publicacao"}:
            raise PermissionDenied("Apenas publicacoes podem ser alteradas pelo aluno.")

        if acao == "alterar_dados":
            form = AlunoDadosForm(request.POST, aluno=aluno)
            if form.is_valid():
                anterior = f"nome={aluno.nome};email={aluno.email};matricula={aluno.matricula or '-'}"
                aluno.nome = form.cleaned_data["nome"]
                aluno.email = form.cleaned_data["email"]
                aluno.matricula = form.cleaned_data["matricula"]
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
            messages.error(request, "Nao foi possivel atualizar os dados do aluno.")

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
                    f"Criada trajetoria {trajetoria.get_nivel_curso_display()}",
                    dados["comentario"],
                    request.user,
                )
                messages.success(request, "Trajetoria academica cadastrada.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel cadastrar a trajetoria academica.")

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
                messages.success(request, "Trajetoria academica atualizada.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel atualizar a trajetoria academica.")

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
                messages.error(request, "Nao foi possivel iniciar o doutorado.")

        elif acao == "alterar_trajetoria_campo":
            trajetoria = get_object_or_404(TrajetoriaAcademica, pk=request.POST.get("trajetoria_id"), aluno=aluno)
            campo = request.POST.get("campo", "").strip()
            comentario = request.POST.get("comentario", "").strip()
            if not comentario:
                messages.error(request, "Informe um comentario para registrar a alteracao.")
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
                messages.error(request, "Campo de trajetoria invalido.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)

            try:
                trajetoria.save()
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("aluno_detalhe", aluno_id=aluno.id)

            _registrar_alteracao_trajetoria(trajetoria, tipo, anterior, novo, comentario, request.user)
            messages.success(request, "Trajetoria academica atualizada.")
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
                messages.success(request, "Qualificacao do aluno atualizada.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel atualizar a qualificacao.")

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
                    messages.success(request, "Prazo de qualificacao atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel atualizar o prazo de qualificacao.")

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
            messages.error(request, "Nao foi possivel atualizar o prazo de defesa.")

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
            messages.error(request, "Nao foi possivel registrar a defesa.")

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
                    messages.success(request, "Registro de deposito da versao final atualizado.")
                    return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel atualizar o deposito da versao final.")

        elif acao == "salvar_publicacao":
            if not can_edit_publicacoes:
                raise PermissionDenied("Voce nao pode alterar publicacoes desta trajetoria.")
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
                messages.success(request, "Publicacao salva.")
                return redirect("aluno_detalhe", aluno_id=aluno.id)
            messages.error(request, "Nao foi possivel salvar a publicacao.")

        elif acao == "salvar_disciplina":
            if not can_edit_disciplinas:
                raise PermissionDenied("Apenas coordenacao e secretaria podem alterar disciplinas.")
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
            messages.error(request, "Nao foi possivel salvar a disciplina.")

    processos_aluno = (
        Processo.objects.select_related("setor_atual")
        .filter(usuario_criado_por=aluno)
        .order_by("-data_criacao")
    )
    trajetorias = aluno.trajetorias.select_related("orientador", "coorientador").order_by("-criado_em")
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
                "form": TrajetoriaAcademicaForm(initial=_trajetoria_form_initial(trajetoria)),
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
                messages.success(request, "Comentario adicionado. Processo marcado como Em Debate.")
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
            "tramitacoes_historico": processo.tramitacoes_historico,
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
                    "Setor inicial 'Secretaria PPGEC' nao encontrado. Contate o administrador.",
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
                        messages.error(request, f"Documento invalido: {error}")
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
    return user.is_authenticated and user.tipo_usuario in {
        User.TipoUsuario.DOCENTE,
        User.TipoUsuario.SERVIDOR,
    }


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
            raise PermissionDenied("Acao invalida.")
        reserva = get_object_or_404(ReservaAmbiente, pk=request.POST.get("reserva_id"))
        if not _can_excluir_reserva_ambiente(request.user, reserva):
            raise PermissionDenied("Apenas a coordenacao ou o docente da reserva pode exclui-la.")
        exclusao_form = ReservaAmbienteExclusaoForm(request.POST)
        if exclusao_form.is_valid():
            reservas_excluidas = list(_reservas_para_exclusao(reserva))
            for reserva_excluida in reservas_excluidas:
                reserva_excluida.excluir(usuario=request.user, justificativa=exclusao_form.cleaned_data["justificativa"])
            if len(reservas_excluidas) == 1:
                messages.success(request, "Reserva marcada como excluida.")
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

    polo = request.user.polo_atuacao
    can_choose_polo = _is_coordenador(request.user) and not polo
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
            messages.success(request, "Horario removido com sucesso.")
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
        raise ValidationError("Setor inicial 'Secretaria PPGEC' nao encontrado. Contate o administrador.")

    processo = Processo.objects.create(
        usuario_criado_por=solicitacao.docente,
        tipo=solicitacao.tipo_defesa,
        assunto=f"{solicitacao.get_tipo_defesa_display()} - {solicitacao.aluno.nome}",
        descricao=(
            "Processo gerado automaticamente a partir da solicitacao de banca "
            f"finalizada em {timezone.localtime(solicitacao.finalizado_em):%d/%m/%Y %H:%M}."
        ),
        setor_atual=setor_secretaria,
        status=Processo.StatusProcesso.EM_ANALISE,
    )
    solicitacao.processo = processo
    solicitacao.save(update_fields=["processo"])
    return processo, True


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
                    f"Solicitacao de banca finalizada e processo {processo_criado.numero} aberto com sucesso.",
                )
            else:
                messages.success(request, "Solicitacao de banca finalizada." if finalizar else "Rascunho salvo.")
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
                    f"Solicitacao de banca finalizada e processo {processo_criado.numero} aberto com sucesso.",
                )
            else:
                messages.success(request, "Solicitacao de banca finalizada." if finalizar else "Rascunho salvo.")
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
    if not _is_membro_setor_nome(request.user, "Colegiando PPGEC (Pleno)"):
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