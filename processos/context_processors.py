from .services import processos_atrasados_queryset, processos_atrasados_url
from .models import Docente, User


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


def _is_coordenador(user):
    if not _is_docente(user):
        return False

    try:
        return bool(user.docente.coordenador)
    except Docente.DoesNotExist:
        return False


def _has_gestao_access(user):
    return _is_coordenador(user) or _is_servidor(user)


def _can_view_processos(user):
    return _is_coordenador(user) or _is_servidor(user)


def _menu_lateral_items(user):
    if user.tipo_usuario == User.TipoUsuario.DOCENTE:
        return [
            {"label": "Reservas de ambientes", "href": "/ambientes/reservas/"},
            {"label": "Meus Processos", "href": "/menu/meus-processos/"},
            {"label": "Processos no Pleno", "href": "/menu/processos-pleno/"},
            {"label": "Processos dos orientandos", "href": "/menu/processos-orientandos/"},
            {"label": "Ciencias", "href": "/menu/ciencias-manifestadas/"},
            {"label": "Meus Orientandos", "href": "/menu/meus-orientandos/"},
        ]
    if user.tipo_usuario == User.TipoUsuario.ALUNO:
        return [
            {"label": "Documento de vÃ­nculo (TODO)", "href": "/aluno/documento-vinculo/"},
            {"label": "Documento de histÃ³rico", "href": "/aluno/documento-historico/"},
            {"label": "Meus Processos", "href": "/menu/meus-processos/"},
            {"label": "Novo processo", "href": "/processos/novo/"},
        ]
    if user.tipo_usuario == User.TipoUsuario.SERVIDOR:
        return [
            {"label": "Reservas de ambientes", "href": "/ambientes/reservas/"},
            {"label": "Salas do polo", "href": "/ambientes/salas/"},
        ]
    return []


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
        "can_view_caixa": _is_docente(request.user) or _is_servidor(request.user),
        "nav_has_gestao_access": has_gestao_access,
        "nav_can_view_dashboard": has_gestao_access,
        "nav_can_view_processos": can_view_processos,
        "nav_can_view_caixa": _is_docente(request.user) or _is_servidor(request.user),
        "nav_side_menu_items": _menu_lateral_items(request.user),
    }
