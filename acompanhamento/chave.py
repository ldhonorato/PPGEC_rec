"""Chave geral do módulo de acompanhamento."""

from django.conf import settings


def modulo_ativo() -> bool:
    """Diz se o módulo está ligado.

    Lê a configuração a cada chamada, e não na carga do módulo, para que os
    testes possam ligar e desligar a chave com override_settings.
    """
    return bool(getattr(settings, "ACOMPANHAMENTO_ATIVO", False))

def aviso_ativo() -> bool:
    """Diz se o aviso diário está ligado.

    Só vale com o módulo também ligado: as duas chaves precisam estar ativas.
    """
    return modulo_ativo() and bool(
        getattr(settings, "ACOMPANHAMENTO_AVISO_ATIVO", False)
    )