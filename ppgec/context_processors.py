"""Contexto disponivel em todas as telas, incluindo as nao autenticadas."""

from django.conf import settings


def rodape_institucional(request):
    """Versao e aviso de direitos, exibidos no rodape de qualquer tela.

    Diferente dos context processors de processos/, este nao depende de
    usuario autenticado -- as telas de login, cadastro e recuperacao de senha
    tambem mostram o rodape.
    """
    return {
        "app_versao": settings.APP_VERSION,
        "app_organizacao": settings.APP_ORGANIZACAO,
        "app_copyright": settings.APP_COPYRIGHT,
    }
