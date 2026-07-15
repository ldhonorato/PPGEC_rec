from django.db.models import Q

from .services import processos_atrasados_queryset, processos_atrasados_url
from .models import Aluno, Docente, SetorMembro, SolicitacaoAssinatura, User


def processos_atrasados(request):
    if not request.user.is_authenticated:
        return {}

    return {
        "processos_atrasados_count": processos_atrasados_queryset(request.user).count(),
        "processos_atrasados_url": processos_atrasados_url(request.user),
    }


def _is_docente(user):
    return user.is_authenticated and user.tipo_usuario == User.TipoUsuario.DOCENTE


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
    if not _is_docente(user):
        return False

    try:
        return bool(user.docente.coordenador)
    except Docente.DoesNotExist:
        return False


def _has_gestao_access(user):
    return _is_coordenador(user) or _is_servidor(user) or _is_secretaria_member(user)


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


def _can_view_caixa(user):
    return (
        _is_servidor(user)
        or SetorMembro.objects.filter(usuario=user, data_saida__isnull=True, setor__ativo=True).exists()
    )


def _has_setor_membership(user):
    return SetorMembro.objects.filter(usuario=user, data_saida__isnull=True, setor__ativo=True).exists()


def _is_membro_setor_nome(user, nome):
    return SetorMembro.objects.filter(
        usuario=user,
        data_saida__isnull=True,
        setor__ativo=True,
        setor__nome=nome,
    ).exists()


def _has_assinaturas_access(user):
    if _has_gestao_access(user):
        return True
    if _is_docente(user):
        return True
    setores_ids = SetorMembro.objects.filter(
        usuario=user,
        data_saida__isnull=True,
        setor__ativo=True,
    ).values_list("setor_id", flat=True)
    return SolicitacaoAssinatura.objects.filter(
        Q(docente=user) | Q(setor_id__in=setores_ids) | Q(criado_por=user)
    ).exists()


def _menu_item(label, href, url_names, icon, children=None):
    return {
        "label": label,
        "href": href,
        "url_names": url_names,
        "icon": icon,
        "children": children or [],
    }


def _menu_lateral_sections(user):
    sections = []

    principal_items = []
    if user.tipo_usuario != User.TipoUsuario.SERVIDOR:
        principal_items.append(_menu_item("Meus Processos", "/menu/meus-processos/", ["menu_meus_processos"], "M"))
        if _can_add_processo(user):
            principal_items.append(_menu_item("Novo Processo", "/processos/novo/", ["novo_processo"], "N"))
    if user.tipo_usuario == User.TipoUsuario.ALUNO:
        principal_items.append(
            _menu_item(
                "Matrícula",
                "/matriculas/",
                ["matriculas_minhas", "matricula_solicitar", "matricula_solicitar_periodo", "matricula_minha_solicitacao"],
                "M",
            )
        )
        principal_items.append(
            _menu_item("Documento de Vínculo", "/aluno/documento-vinculo/", ["aluno_documento_vinculo"], "D")
        )
        principal_items.append(
            _menu_item("Minha Trajetória", f"/coordenacao/alunos/{user.id}/", ["aluno_detalhe"], "T")
        )
    if user.tipo_usuario in {User.TipoUsuario.DOCENTE, User.TipoUsuario.SERVIDOR} or _is_secretaria_member(user):
        principal_items.append(
            _menu_item(
                "Reserva de Ambiente",
                "/ambientes/reservas/",
                ["reservas_ambientes", "disponibilidade_ambientes", "reservas_ambientes_feitas"],
                "R",
                children=[
                    _menu_item("Nova reserva de ambiente", "/ambientes/reservas/", ["reservas_ambientes"], "N"),
                    _menu_item(
                        "Disponibilidade semanal",
                        "/ambientes/disponibilidade/",
                        ["disponibilidade_ambientes"],
                        "D",
                    ),
                    _menu_item(
                        "Reservas feitas",
                        "/ambientes/reservas/feitas/",
                        ["reservas_ambientes_feitas"],
                        "F",
                    ),
                ],
            )
        )
    has_setor_membership = _has_setor_membership(user)
    if has_setor_membership:
        principal_items.append(
            _menu_item(
                "Caixa de Processos",
                "/coordenacao/caixa-processos/",
                ["coordenacao_caixa_processos"],
                "C",
            )
        )
    if _has_assinaturas_access(user) and not _has_gestao_access(user):
        principal_items.append(
            _menu_item(
                "Assinaturas",
                "/assinaturas/pendentes/",
                ["pendencias_assinatura", "solicitacao_assinatura_detalhe"],
                "A",
            )
        )
    if principal_items:
        sections.append({"label": "Principal", "items": principal_items})

    if user.tipo_usuario == User.TipoUsuario.DOCENTE:
        docente_items = [
            _menu_item("Ofertas de Disciplinas", "/gestao/matriculas/ofertas/", ["matriculas_ofertas"], "O"),
            _menu_item("Ciências", "/menu/ciencias-manifestadas/", ["menu_ciencias_manifestadas"], "C"),
            _menu_item("Meus Orientandos", "/menu/meus-orientandos/", ["menu_meus_orientandos"], "O"),
            _menu_item(
                "Solicitação de Banca",
                "/bancas/",
                ["solicitacoes_banca", "solicitacao_banca_nova", "solicitacao_banca_detalhe"],
                "B",
            ),
            _menu_item(
                "Processos dos Orientandos",
                "/menu/processos-orientandos/",
                ["menu_processos_orientandos"],
                "P",
            ),
        ]
        if _is_membro_setor_nome(user, "Colegiando PPGEC (Pleno)"):
            docente_items.insert(
                0,
                _menu_item("Processos no Pleno", "/menu/processos-pleno/", ["menu_processos_pleno"], "P"),
            )
        sections.append({"label": "Docente", "items": docente_items})

    if _has_gestao_access(user):
        coordenacao_items = []
        if not has_setor_membership:
            coordenacao_items.append(
                _menu_item(
                    "Caixa de Processos",
                    "/coordenacao/caixa-processos/",
                    ["coordenacao_caixa_processos"],
                    "C",
                )
            )
        coordenacao_items.extend([
            _menu_item("Dashboard", "/coordenacao/dashboard/", ["coordenacao_dashboard"], "D"),
            _menu_item(
                "Matrículas",
                "/gestao/matriculas/periodos/",
                [
                    "matriculas_periodos",
                    "matriculas_solicitacoes",
                    "matriculas_disciplinas",
                    "matriculas_ofertas",
                    "matricula_oferta_alunos",
                    "matricula_oferta_exportar",
                ],
                "M",
                children=[
                    _menu_item("Períodos letivos", "/gestao/matriculas/periodos/", ["matriculas_periodos"], "P"),
                    _menu_item(
                        "Solicitações",
                        "/gestao/matriculas/solicitacoes/",
                        ["matriculas_solicitacoes"],
                        "S",
                    ),
                    _menu_item("Disciplinas", "/gestao/matriculas/disciplinas/", ["matriculas_disciplinas"], "D"),
                    _menu_item(
                        "Ofertas de disciplinas",
                        "/gestao/matriculas/ofertas/",
                        ["matriculas_ofertas", "matricula_oferta_alunos", "matricula_oferta_exportar"],
                        "O",
                    ),
                ],
            ),
            _menu_item("Alunos", "/coordenacao/alunos/", ["coordenacao_alunos", "aluno_detalhe"], "A"),
            *(
                [
                    _menu_item(
                        "Validar Cadastros",
                        "/coordenacao/alunos/cadastros/",
                        ["validar_cadastros_alunos"],
                        "V",
                    )
                ]
                if _is_servidor(user) or _is_secretaria_member(user)
                else []
            ),
            _menu_item("Setores e Comissões", "/coordenacao/setores/", ["setores_comissoes"], "S"),
        ])
        coordenacao_items.append(
            _menu_item(
                "Assinaturas",
                "/assinaturas/",
                [
                    "nova_solicitacao_assinatura",
                    "pendencias_assinatura",
                    "solicitacoes_assinatura",
                    "solicitacao_assinatura_detalhe",
                ],
                "A",
                children=[
                    _menu_item(
                        "Nova solicitacao",
                        "/assinaturas/nova/",
                        ["nova_solicitacao_assinatura"],
                        "N",
                    ),
                    _menu_item(
                        "Pendencias de assinatura",
                        "/assinaturas/pendentes/",
                        ["pendencias_assinatura"],
                        "P",
                    ),
                    _menu_item(
                        "Solicitacoes feitas",
                        "/assinaturas/",
                        ["solicitacoes_assinatura"],
                        "S",
                    ),
                ],
            )
        )
        if _is_coordenador(user):
            coordenacao_items.append(
                _menu_item("Criar Comissão", "/coordenacao/setores/criar/", ["criar_comissao"], "C")
            )
        if _has_gestao_access(user):
            coordenacao_items.extend(
                [
                    _menu_item("Cadastro de Salas", "/ambientes/salas/", ["salas_ambientes"], "S"),
                    _menu_item("Reservas de Salas", "/ambientes/reservas/feitas/", ["reservas_ambientes_feitas"], "R"),
                ]
            )
        sections.append({"label": "Coordenação", "items": coordenacao_items})

    return sections


def _menu_lateral_items(user):
    sections = _menu_lateral_sections(user)
    if not sections:
        return []
    items = []
    for section in sections:
        items.extend(section["items"])
    return items


def navegacao_lateral(request):
    if not request.user.is_authenticated:
        return {}

    has_gestao_access = _has_gestao_access(request.user)
    can_view_processos = _can_view_processos(request.user)
    return {
        "is_coordenador": _is_coordenador(request.user),
        "has_gestao_access": has_gestao_access,
        "can_view_dashboard": has_gestao_access,
        "can_view_processos": can_view_processos,
        "can_view_caixa": _can_view_caixa(request.user),
        "nav_has_gestao_access": has_gestao_access,
        "nav_can_view_dashboard": has_gestao_access,
        "nav_can_view_processos": can_view_processos,
        "nav_can_view_caixa": _can_view_caixa(request.user),
        "nav_menu_sections": _menu_lateral_sections(request.user),
        "nav_side_menu_items": _menu_lateral_items(request.user),
    }
