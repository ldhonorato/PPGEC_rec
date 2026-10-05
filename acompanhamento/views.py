from django.shortcuts import render
from django.urls import reverse

from .decoradores import tela_do_modulo
from .telas import TELAS


@tela_do_modulo
def inicio(request):
    """Página inicial do módulo, com a lista das telas disponíveis."""
    telas = [{"titulo": titulo, "endereco": reverse(rota)} for titulo, rota in TELAS]
    return render(request, "acompanhamento/inicio.html", {"telas": telas})

from .servicos import calcular_painel_docentes


@tela_do_modulo
def painel_docentes(request):
    """Exibe o painel de distribuição de docentes do PPGEC."""
    contexto = calcular_painel_docentes()
    return render(request, "acompanhamento/painel_docentes.html", contexto)