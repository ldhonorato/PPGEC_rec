"""Proteção comum a todas as telas do módulo de acompanhamento."""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404

from .chave import modulo_ativo
from .permissoes import tem_acesso_gestao


def tela_do_modulo(view):
    """Aplica, nesta ordem, as três regras de acesso de uma tela do módulo.

    1. Com a chave do módulo desligada, a tela responde como página
       inexistente para qualquer pessoa, com ou sem login.
    2. Visitante sem login é levado à tela de login.
    3. Usuário sem acesso de gestão recebe acesso negado.

    A chave vem primeiro de propósito: com o módulo desligado, ninguém deve
    conseguir nem descobrir que a tela existe.
    """

    @wraps(view)
    def envoltorio(request, *args, **kwargs):
        if not modulo_ativo():
            raise Http404("Página não encontrada.")
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not tem_acesso_gestao(request.user):
            raise PermissionDenied("Acesso restrito à gestão do programa.")
        return view(request, *args, **kwargs)

    return envoltorio
