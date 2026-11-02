from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Avg, Count, F, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from acompanhamento.situacao import (
    IRREGULAR_COM_CHANCE,
    IRREGULAR_SEM_CHANCE,
    calcular_situacoes,
)
from processos.models import Docente, Polo, Processo, TrajetoriaAcademica


def validar_semestre(semestre):
    """Valida o formato AAAA.1/AAAA.2 e retorna (ano, período)."""
    partes = str(semestre).split(".")
    if (
        len(partes) != 2
        or partes[1] not in {"1", "2"}
        or not partes[0].isdigit()
        or len(partes[0]) != 4
        or not 1 <= int(partes[0]) <= 9999
    ):
        raise ValueError(f"Semestre inválido: '{semestre}'. Utilize o formato YYYY.1 ou YYYY.2.")
    return int(partes[0]), partes[1]


def calcular_painel_docentes(somente_permanentes=False):
    """
    Calcula indicadores de distribuição de docentes ativos do programa.
    
    Permite filtrar opcionalmente por somente_permanentes.
    Agrupa externos sob a categoria 'Externo à UPE' e docentes sem polo
    válido ou associados ao Polo Sede / polos inativos como 'Polo não informado'.
    Garante percentuais exatos cuja soma totaliza 100%.
    """
    docentes_ativos = Docente.objects.filter(is_active=True)

    # Contagens gerais dos docentes ativos (independente de polo)
    total_ativos = docentes_ativos.count()
    total_permanentes = docentes_ativos.filter(permanente=True).count()
    total_colaboradores = docentes_ativos.filter(permanente=False).count()
    total_externos = docentes_ativos.filter(externo=True).count()
    total_internos = docentes_ativos.filter(externo=False).count()

    if total_ativos > 0:
        percentual_permanentes = (
            Decimal(total_permanentes) * Decimal("100.0") / Decimal(total_ativos)
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        percentual_colaboradores = Decimal("100.0") - percentual_permanentes
    else:
        percentual_permanentes = Decimal("0.0")
        percentual_colaboradores = Decimal("0.0")

    # Base para a distribuição (aplicando o filtro opcional se solicitado)
    base_distribuicao = docentes_ativos
    if somente_permanentes:
        base_distribuicao = base_distribuicao.filter(permanente=True)

    total_distribuicao = base_distribuicao.count()

    # 1. Externos à UPE (formam linha própria independente do polo)
    qtd_externos = base_distribuicao.filter(externo=True).count()

    # 2. Polos ativos válidos (excluindo qualquer polo genérico nomeado 'Polo Sede')
    polos_validos = (
        Polo.objects.filter(ativo=True)
        .exclude(nome__iexact="Polo Sede")
        .order_by("nome")
    )

    linhas_distribuicao = []
    qtd_em_polos_validos = 0

    for polo in polos_validos:
        # Apenas docentes internos contam nos polos individuais
        qtd = base_distribuicao.filter(
            externo=False,
            polo_atuacao=polo,
        ).count()
        if qtd > 0:
            linhas_distribuicao.append({
                "id": polo.id,
                "nome": polo.nome,
                "total_docentes": qtd,
            })
            qtd_em_polos_validos += qtd

    # 3. Polo não informado: internos que não entraram nos polos válidos
    # (sem polo, ligados ao Polo Sede ou associados a polos inativos)
    qtd_polo_nao_informado = total_distribuicao - qtd_externos - qtd_em_polos_validos

    if qtd_polo_nao_informado > 0 or total_distribuicao == 0:
        linhas_distribuicao.append({
            "id": None,
            "nome": "Polo não informado",
            "total_docentes": qtd_polo_nao_informado,
        })

    if qtd_externos > 0:
        linhas_distribuicao.append({
            "id": None,
            "nome": "Externo à UPE",
            "total_docentes": qtd_externos,
        })

    # Cálculo dos percentuais com ajuste de arredondamento (soma exata de 100%)
    if total_distribuicao > 0:
        soma_percentual = Decimal("0.0")
        for item in linhas_distribuicao:
            percentual = (
                Decimal(item["total_docentes"]) * Decimal("100.0") / Decimal(total_distribuicao)
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            item["percentual"] = float(percentual)
            soma_percentual += percentual

        # Ajuste no último item com quantidade maior que zero caso haja resíduo de arredondamento
        diferenca = Decimal("100.0") - soma_percentual
        if diferenca != Decimal("0.0"):
            for item in reversed(linhas_distribuicao):
                if item["total_docentes"] > 0:
                    item["percentual"] = round(item["percentual"] + float(diferenca), 1)
                    break
    else:
        for item in linhas_distribuicao:
            item["percentual"] = 0.0

    return {
        "total_ativos": total_ativos,
        "total_permanentes": total_permanentes,
        "total_colaboradores": total_colaboradores,
        "percentual_permanentes": percentual_permanentes,
        "percentual_colaboradores": percentual_colaboradores,
        "total_internos": total_internos,
        "total_externos": total_externos,
        "total_distribuicao": total_distribuicao,
        "distribuicao_polos": linhas_distribuicao,
        "somente_permanentes": somente_permanentes,
    }


def calcular_painel_processos(semestre=None, data_inicio=None, data_fim=None):
    """
    Calcula indicadores do painel de processos/solicitações dos discentes.

    Métricas calculadas:
    - Quantidade por tipo de processo e por status;
    - Volume mensal e quantidade por tipo em cada mês do período;
    - Tempo médio de resolução em dias (None caso não haja finalizados);
    - Quantidade de processos em atraso (usando esta_atrasado).

    Um intervalo completo de datas prevalece sobre o semestre informado.
    Sem datas, usa o semestre informado ou o semestre atual.
    """
    hoje = timezone.localdate()
    intervalo_personalizado = data_inicio is not None or data_fim is not None

    if intervalo_personalizado:
        if data_inicio is None or data_fim is None:
            raise ValueError("Informe as datas inicial e final.")
        if data_inicio > data_fim:
            raise ValueError("A data inicial deve ser anterior ou igual à data final.")
        total_meses = (
            (data_fim.year - data_inicio.year) * 12
            + data_fim.month
            - data_inicio.month
            + 1
        )
        if total_meses > 120:
            raise ValueError("O intervalo pode ter no máximo 10 anos.")
    else:
        if semestre is None:
            semestre = f"{hoje.year}.{'1' if hoje.month <= 6 else '2'}"

        ano, periodo = validar_semestre(semestre)
        if periodo == "1":
            data_inicio = date(ano, 1, 1)
            data_fim = date(ano, 6, 30)
        else:
            data_inicio = date(ano, 7, 1)
            data_fim = date(ano, 12, 31)

    queryset = Processo.objects.filter(data_criacao__date__range=(data_inicio, data_fim))

    total_processos = queryset.count()

    # Agrupa em uma consulta só por tipo e mês para os dois gráficos.
    contagens_tipo_mes = queryset.annotate(
        mes=TruncMonth("data_criacao", tzinfo=timezone.get_current_timezone())
    ).values("tipo", "mes").annotate(total=Count("id")).order_by("mes", "tipo")

    contagem_por_tipo = {}
    abertos_por_mes = {}
    ano_mes = (data_inicio.year, data_inicio.month)
    ultimo_ano_mes = (data_fim.year, data_fim.month)
    while ano_mes <= ultimo_ano_mes:
        ano, mes = ano_mes
        abertos_por_mes[f"{ano}-{mes:02d}"] = 0
        ano_mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)

    por_tipo_por_mes = []
    rotulos_tipos = dict(Processo.TipoProcesso.choices)
    for item in contagens_tipo_mes:
        tipo_valor = item["tipo"]
        rotulo_tipo = rotulos_tipos.get(tipo_valor, tipo_valor)
        chave_mes = item["mes"].strftime("%Y-%m")
        contagem_por_tipo[rotulo_tipo] = (
            contagem_por_tipo.get(rotulo_tipo, 0) + item["total"]
        )
        abertos_por_mes[chave_mes] += item["total"]
        por_tipo_por_mes.append(
            {
                "mes": chave_mes,
                "tipo": rotulo_tipo,
                "total": item["total"],
            }
        )
    contagem_por_tipo = dict(
        sorted(contagem_por_tipo.items(), key=lambda item: item[1], reverse=True)
    )

    # Contagem por status
    contagem_por_status = {}
    for item in queryset.values("status").annotate(total=Count("id")).order_by("-total"):
        status_valor = item["status"]
        label = dict(Processo.StatusProcesso.choices).get(status_valor, status_valor)
        contagem_por_status[label] = item["total"]

    # 3. Tempo médio de resolução (None caso não haja finalizados)
    finalizados = queryset.filter(
        Q(finalizado_em__isnull=False) | Q(status=Processo.StatusProcesso.FINALIZADO)
    )
    tempos_dias = []
    for proc in finalizados:
        data_finalizacao = proc.finalizado_em or proc.atualizado_em
        if data_finalizacao and proc.data_criacao:
            diferenca = (data_finalizacao - proc.data_criacao).total_seconds() / 86400
            if diferenca >= 0:
                tempos_dias.append(diferenca)

    if tempos_dias:
        tempo_medio_dias = round(sum(tempos_dias) / len(tempos_dias), 1)
    else:
        tempo_medio_dias = None

    # 4. Quantidade de processos em atraso
    total_atrasados = sum(1 for proc in queryset if proc.esta_atrasado)

    return {
        "semestre": semestre,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "total_processos": total_processos,
        "por_tipo": contagem_por_tipo,
        "por_tipo_por_mes": por_tipo_por_mes,
        "por_status": contagem_por_status,
        "abertos_por_mes": abertos_por_mes,
        "tempo_medio_dias": tempo_medio_dias,
        "total_atrasados": total_atrasados,
    }


# Primeiro semestre com registros no Acadflow. Nas turmas anteriores,
# desligamentos, prorrogações e defesas podem não ter sido lançados.
INICIO_DOS_REGISTROS = (2026, "2")


def _anterior_aos_registros(turma):
    """Diz se a turma ingressou antes do primeiro semestre registrado no Acadflow."""
    try:
        return validar_semestre(turma) < INICIO_DOS_REGISTROS
    except ValueError:
        return False


def calcular_painel_sucesso_turma(
    nivel,
    data_referencia=None,
    ano_inicial=None,
    ano_final=None,
):
    """
    Calcula indicadores de taxa de sucesso e distribuição por turma de ingresso para um nível específico.
    Permite filtrar opcionalmente por intervalo de anos de ingresso (ex: 2024 a 2026).
    """
    if ano_inicial is not None:
        ano_inicial_str = str(ano_inicial).strip()
        if not (len(ano_inicial_str) == 4 and ano_inicial_str.isdigit()):
            raise ValueError("Ano inicial inválido. Utilize o formato YYYY.")
        ano_ini = int(ano_inicial_str)
    else:
        ano_ini = None

    if ano_final is not None:
        ano_final_str = str(ano_final).strip()
        if not (len(ano_final_str) == 4 and ano_final_str.isdigit()):
            raise ValueError("Ano final inválido. Utilize o formato YYYY.")
        ano_fim = int(ano_final_str)
    else:
        ano_fim = None

    if ano_ini is not None and ano_fim is not None and ano_ini > ano_fim:
        raise ValueError("O ano inicial não pode ser maior que o ano final.")

    if data_referencia is None:
        data_referencia = timezone.localdate()

    # Filtra pelo nível e exclui trajetórias removidas e em homologação
    trajetorias_qs = (
        TrajetoriaAcademica.objects.filter(nivel_curso=nivel)
        .exclude(
            status__in=[
                TrajetoriaAcademica.Status.REMOVIDA,
                TrajetoriaAcademica.Status.EM_HOMOLOGACAO,
            ]
        )
        .select_related("aluno")
        .prefetch_related("prorrogacoes")
        .order_by("-ingresso")
    )

    if ano_ini is not None:
        trajetorias_qs = trajetorias_qs.filter(ingresso__gte=f"{ano_ini}.1")

    if ano_fim is not None:
        trajetorias_qs = trajetorias_qs.filter(ingresso__lte=f"{ano_fim}.2")

    lista_trajetorias = list(trajetorias_qs)

    # Coleta trajetórias ativas para verificar situações
    ativas = [t for t in lista_trajetorias if t.status == TrajetoriaAcademica.Status.ATIVA]
    situacoes = calcular_situacoes(ativas, data_referencia)

    turmas_dict = {}

    for traj in lista_trajetorias:
        turma_nome = traj.ingresso or "Não informado"
        if turma_nome not in turmas_dict:
            turmas_dict[turma_nome] = {
                "turma": turma_nome,
                "dados_limitados": _anterior_aos_registros(turma_nome),
                "ingressantes": 0,
                "concluidos": 0,
                "desligados": 0,
                "trancados": 0,
                "ativos": 0,
                "ativos_regulares": 0,
                "ativos_irregulares": 0,
                "com_prorrogacao": 0,
                "taxa_conclusao": 0.0,
                "taxa_desligamento": 0.0,
                "taxa_trancamento": 0.0,
                "taxa_ativos": 0.0,
                "taxa_ativos_regulares": 0.0,
                "taxa_ativos_irregulares": 0.0,
                "percentual_prorrogacao": 0.0,
            }

        dados = turmas_dict[turma_nome]
        dados["ingressantes"] += 1

        if traj.status == TrajetoriaAcademica.Status.CONCLUIDA:
            dados["concluidos"] += 1
        elif traj.status == TrajetoriaAcademica.Status.DESLIGADA:
            dados["desligados"] += 1
        elif traj.status == TrajetoriaAcademica.Status.TRANCADA:
            dados["trancados"] += 1
        elif traj.status == TrajetoriaAcademica.Status.ATIVA:
            dados["ativos"] += 1
            # Verifica se está irregular no marco ou na defesa
            dados_sit = situacoes.get(traj.pk, {})
            eh_irregular = any(
                dados_sit.get(chave)
                and dados_sit[chave].situacao in (IRREGULAR_COM_CHANCE, IRREGULAR_SEM_CHANCE)
                for chave in ("marco", "defesa")
            )
            if eh_irregular:
                dados["ativos_irregulares"] += 1
            else:
                dados["ativos_regulares"] += 1

        # Prorrogação considerada via meses_prorrogados na trajetória
        if traj.meses_prorrogados > 0:
            dados["com_prorrogacao"] += 1

    resultado = []
    for dados in turmas_dict.values():
        total = dados["ingressantes"]
        if total > 0:
            dados["taxa_conclusao"] = round((dados["concluidos"] / total) * 100, 1)
            dados["taxa_desligamento"] = round((dados["desligados"] / total) * 100, 1)
            dados["taxa_trancamento"] = round((dados["trancados"] / total) * 100, 1)
            dados["taxa_ativos"] = round((dados["ativos"] / total) * 100, 1)
            dados["taxa_ativos_regulares"] = round((dados["ativos_regulares"] / total) * 100, 1)
            dados["taxa_ativos_irregulares"] = round((dados["ativos_irregulares"] / total) * 100, 1)
            dados["percentual_prorrogacao"] = round((dados["com_prorrogacao"] / total) * 100, 1)
        resultado.append(dados)

    return resultado
