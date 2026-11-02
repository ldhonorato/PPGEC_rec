from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format

from .chave import aviso_ativo
from .decoradores import tela_do_modulo
from .forms import MarcoCalendarioForm
from .lista_alunos import cartao_alunos_irregulares
from .models import ExecucaoAviso, MarcoCalendario
from .servicos import (
    calcular_painel_docentes,
    calcular_painel_processos,
    calcular_painel_sucesso_turma,
    validar_semestre,
)
from .telas import TELAS


@tela_do_modulo
def inicio(request):
    """Página inicial do módulo, com a lista das telas disponíveis."""
    telas = [
        {"titulo": titulo, "descricao": descricao, "endereco": reverse(rota), "icone": icone}
        for titulo, descricao, rota, icone in TELAS
    ]
    contexto = {
        "telas": telas,
        "cartao_irregulares": cartao_alunos_irregulares(),
        "aviso_ativo": aviso_ativo(),
        "ultima_execucao_aviso": ExecucaoAviso.objects.first() if aviso_ativo() else None,
    }
    return render(request, "acompanhamento/inicio.html", contexto)


@tela_do_modulo
def painel_docentes(request):
    """Exibe o painel de distribuição de docentes do PPGEC."""
    somente_permanentes = request.GET.get("somente_permanentes") in ["1", "true", "True"]
    contexto = calcular_painel_docentes(somente_permanentes=somente_permanentes)
    contexto["somente_permanentes"] = somente_permanentes
    return render(request, "acompanhamento/painel_docentes.html", contexto)


@tela_do_modulo
def calendario_marcos(request):
    """Lista os marcos do calendário e permite cadastrar/editar."""
    marcos = MarcoCalendario.objects.all().order_by("data_limite")

    marco_id = request.GET.get("editar")
    marco_edicao = None
    if marco_id:
        marco_edicao = get_object_or_404(MarcoCalendario, pk=marco_id)

    if request.method == "POST":
        form = MarcoCalendarioForm(request.POST, instance=marco_edicao)
        if form.is_valid():
            marco = form.save(commit=False)
            marco.alterado_por = request.user
            marco.save()
            return redirect("acompanhamento:calendario_marcos")
    else:
        form = MarcoCalendarioForm(instance=marco_edicao)

    contexto = {
        "marcos": marcos,
        "form": form,
        "marco_edicao": marco_edicao,
    }
    return render(request, "acompanhamento/calendario_marcos.html", contexto)


def _semestres_recentes(quantidade=6):
    """Semestre atual e os anteriores, do mais recente para o mais antigo."""
    hoje = timezone.localdate()
    ano, periodo = hoje.year, 1 if hoje.month <= 6 else 2
    semestres = []
    for _ in range(quantidade):
        semestres.append(f"{ano}.{periodo}")
        ano, periodo = (ano, 1) if periodo == 2 else (ano - 1, 2)
    return semestres


def _barras(contagens):
    """Converte {rótulo: total} em linhas com a largura relativa ao maior total."""
    maior = max(contagens.values(), default=0)
    return [
        {
            "rotulo": rotulo,
            "total": total,
            "largura": round(total * 100 / maior, 1) if maior else 0,
        }
        for rotulo, total in contagens.items()
    ]


# Barras do painel de sucesso por turma: rótulo, chave da contagem e chave do
# percentual sobre os ingressantes, como vêm de calcular_painel_sucesso_turma.
INDICADORES_DA_TURMA = (
    ("Concluídos", "concluidos", "taxa_conclusao"),
    ("Desligados", "desligados", "taxa_desligamento"),
    ("Trancados", "trancados", "taxa_trancamento"),
    ("Ativos", "ativos", "taxa_ativos"),
    ("Ativos regulares", "ativos_regulares", "taxa_ativos_regulares"),
    ("Ativos irregulares", "ativos_irregulares", "taxa_ativos_irregulares"),
    ("Com prorrogação", "com_prorrogacao", "percentual_prorrogacao"),
)


def _barras_da_turma(turma):
    """Barras da turma com a largura igual ao percentual sobre os ingressantes.

    O _barras() usa o maior total como 100%, o que deixaria cheia a barra de
    concluídos mesmo com 45% da turma.
    """
    return [
        {
            "rotulo": rotulo,
            "quantidade": turma[contagem],
            "total": f"{turma[contagem]} ({number_format(turma[percentual], decimal_pos=1)}%)",
            "largura": turma[percentual],
        }
        for rotulo, contagem, percentual in INDICADORES_DA_TURMA
    ]


@tela_do_modulo
def painel_processos(request):
    """Exibe os indicadores no semestre ou intervalo de datas escolhido."""
    semestre_solicitado = request.GET.get("semestre") or None
    data_inicio_solicitada = request.GET.get("data_inicio") or None
    data_fim_solicitada = request.GET.get("data_fim") or None
    filtro_por_datas = data_inicio_solicitada is not None or data_fim_solicitada is not None
    semestre_valido = None
    if semestre_solicitado is not None:
        try:
            validar_semestre(semestre_solicitado)
        except ValueError:
            pass
        else:
            semestre_valido = semestre_solicitado

    intervalo_aplicado = False
    if filtro_por_datas:
        try:
            if data_inicio_solicitada is None or data_fim_solicitada is None:
                raise ValueError("Informe as datas inicial e final.")
            try:
                data_inicio = date.fromisoformat(data_inicio_solicitada)
                data_fim = date.fromisoformat(data_fim_solicitada)
            except ValueError as erro:
                raise ValueError("Informe datas válidas no formato DD/MM/AAAA.") from erro
            contexto = calcular_painel_processos(
                semestre=semestre_valido,
                data_inicio=data_inicio,
                data_fim=data_fim,
            )
            data_inicio_filtro = data_inicio
            data_fim_filtro = data_fim
            intervalo_aplicado = True
        except ValueError as erro:
            messages.error(request, str(erro))
            contexto = calcular_painel_processos(semestre=semestre_valido)
            data_inicio_filtro = None
            data_fim_filtro = None
    else:
        try:
            contexto = calcular_painel_processos(semestre=semestre_solicitado)
        except ValueError:
            messages.error(request, "Semestre inválido. Use o formato AAAA.1 ou AAAA.2.")
            contexto = calcular_painel_processos()
        data_inicio_filtro = None
        data_fim_filtro = None

    meses = {
        f"{chave[5:]}/{chave[:4]}": total
        for chave, total in contexto["abertos_por_mes"].items()
    }
    semestres = _semestres_recentes()
    if intervalo_aplicado and semestre_valido is not None:
        semestre_selecionado = semestre_valido
    else:
        semestre_selecionado = contexto["semestre"]
    if semestre_selecionado is None:
        semestre_selecionado = _semestres_recentes(quantidade=1)[0]
    if semestre_selecionado not in semestres:
        semestres.append(semestre_selecionado)

    contagens_tipo_mes = {
        f"{item['mes'][5:]}/{item['mes'][:4]} — {item['tipo']}": item["total"]
        for item in contexto["por_tipo_por_mes"]
    }
    contexto.update(
        {
            "semestres": semestres,
            "semestre_selecionado": semestre_selecionado,
            "data_inicio_filtro": data_inicio_filtro,
            "data_fim_filtro": data_fim_filtro,
            "barras_tipo": _barras(contexto["por_tipo"]),
            "barras_status": _barras(contexto["por_status"]),
            "barras_mes": _barras(meses),
            "barras_tipo_mes": _barras(contagens_tipo_mes),
        }
    )
    return render(request, "acompanhamento/painel_processos.html", contexto)


@tela_do_modulo
def painel_sucesso_turma(request):
    """Exibe os indicadores do painel de sucesso por turma."""
    nivel_selecionado = request.GET.get("nivel")
    nivel_padrao = "MESTRADO"

    if nivel_selecionado and nivel_selecionado not in ["MESTRADO", "DOUTORADO"]:
        messages.error(
            request,
            "Nível inválido. Escolha entre Mestrado ou Doutorado."
        )
        nivel_selecionado = nivel_padrao
    elif not nivel_selecionado:
        nivel_selecionado = nivel_padrao

    ano_inicial = request.GET.get("ano_inicial", "").strip() or None
    ano_final = request.GET.get("ano_final", "").strip() or None

    try:
        turmas = calcular_painel_sucesso_turma(
            nivel=nivel_selecionado,
            ano_inicial=ano_inicial,
            ano_final=ano_final,
        )
    except ValueError as e:
        messages.error(request, str(e))
        turmas = calcular_painel_sucesso_turma(nivel=nivel_selecionado)

    for turma in turmas:
        turma["barras"] = _barras_da_turma(turma)

    turmas_grafico = list(reversed(turmas))

    alerta_dados_limitados = any(t.get("dados_limitados") for t in turmas)

    contexto = {
        "nivel_selecionado": nivel_selecionado,
        "ano_inicial": request.GET.get("ano_inicial", ""),
        "ano_final": request.GET.get("ano_final", ""),
        "turmas": turmas,
        "turmas_grafico": turmas_grafico,
        "alerta_dados_limitados": alerta_dados_limitados,
    }
    return render(request, "acompanhamento/painel_sucesso_turma.html", contexto)
