"""Indicadores e cronograma derivados dos registros do planejamento."""
from collections import Counter
from datetime import timedelta

from django.utils import timezone

from .models import AcoesPlanejamentoEstrategico


STATUS = AcoesPlanejamentoEstrategico.StatusAcoes
TONS_STATUS = {
    STATUS.NAO_INICIADA: "neutro",
    STATUS.EM_ANDAMENTO: "azul",
    STATUS.CONCLUIDA_PARCIALMENTE: "ambar",
    STATUS.CONCLUIDA_TOTALMENTE: "verde",
    STATUS.CANCELADA: "cinza",
}
ENCERRADOS = {STATUS.CONCLUIDA_TOTALMENTE, STATUS.CANCELADA}


def resumir_acoes(acoes, hoje):
    contagens = Counter(acao.status for acao in acoes)
    total = len(acoes)
    ativas = [acao for acao in acoes if acao.status not in ENCERRADOS]
    concluidas = contagens[STATUS.CONCLUIDA_TOTALMENTE]
    base_conclusao = total - contagens[STATUS.CANCELADA]
    return {
        "total": total,
        "concluidas": concluidas,
        "base_conclusao": base_conclusao,
        "taxa_conclusao": concluidas * 100 // base_conclusao if base_conclusao else 0,
        "atrasadas": sum(bool(acao.data_termino_planejado and acao.data_termino_planejado < hoje) for acao in ativas),
        "proximas": sum(bool(acao.data_termino_planejado and hoje <= acao.data_termino_planejado <= hoje + timedelta(days=30)) for acao in ativas),
        "sem_cronograma": sum(not (acao.data_inicio_planejado and acao.data_termino_planejado) for acao in ativas),
        "status": [
            {"nome": nome, "total": contagens[valor], "tom": TONS_STATUS[valor],
             "percentual": round(contagens[valor] * 100 / total, 2) if total else 0}
            for valor, nome in STATUS.choices
        ],
    }


def montar_painel(metas, hoje=None):
    hoje = hoje or timezone.localdate()
    grupos = {}
    acoes = []
    for meta in metas:
        grupo = grupos.setdefault(meta.setor_id, {"setor": meta.setor, "metas": [], "acoes": []})
        grupo["metas"].append(meta)
        meta.acoes_painel = list(meta.acoes_planejamento_estrategico.all())
        meta.resumo = resumir_acoes(meta.acoes_painel, hoje)
        for acao in meta.acoes_painel:
            acao.meta_painel = meta
            acao.tom = TONS_STATUS.get(acao.status, "neutro")
            acao.atrasada = bool(acao.status not in ENCERRADOS and acao.data_termino_planejado and acao.data_termino_planejado < hoje)
            acao.proxima = bool(acao.status not in ENCERRADOS and acao.data_termino_planejado and hoje <= acao.data_termino_planejado <= hoje + timedelta(days=30))
            acao.sem_cronograma = acao.status not in ENCERRADOS and not (acao.data_inicio_planejado and acao.data_termino_planejado)
            acao.dias_atraso = (hoje - acao.data_termino_planejado).days if acao.atrasada else 0
            grupo["acoes"].append(acao)
            acoes.append(acao)
    for grupo in grupos.values():
        grupo["resumo"] = resumir_acoes(grupo["acoes"], hoje)
    return {"hoje": hoje, "resumo": resumir_acoes(acoes, hoje), "grupos": list(grupos.values()), "acoes": acoes, "total_metas": len(metas)}


def montar_cronograma(acoes, hoje):
    linhas = []
    datas = [hoje]
    for acao in acoes:
        intervalos = []
        for tipo in ("planejado", "executado"):
            inicio = getattr(acao, f"data_inicio_{tipo}")
            fim = getattr(acao, f"data_termino_{tipo}")
            em_curso = bool(tipo == "executado" and inicio and not fim and acao.status in {STATUS.EM_ANDAMENTO, STATUS.CONCLUIDA_PARCIALMENTE} and inicio <= hoje)
            if em_curso:
                fim = hoje
            if not inicio or not fim or fim < inicio:
                continue
            intervalos.append({"tipo": tipo, "inicio": inicio, "fim": fim, "em_curso": em_curso})
            datas.extend((inicio, fim))
        linhas.append({"acao": acao, "intervalos": intervalos})
    inicio_eixo = min(datas) - timedelta(days=7)
    fim_eixo = max(datas) + timedelta(days=7)
    duracao = (fim_eixo - inicio_eixo).days + 1
    for linha in linhas:
        for intervalo in linha["intervalos"]:
            intervalo["inicio_percentual"] = round((intervalo["inicio"] - inicio_eixo).days / duracao * 100, 4)
            intervalo["largura_percentual"] = round(((intervalo["fim"] - intervalo["inicio"]).days + 1) / duracao * 100, 4)
    return {
        "linhas_cronograma": linhas,
        "tem_intervalos": any(linha["intervalos"] for linha in linhas),
        "marcos_cronograma": [inicio_eixo + timedelta(days=round((duracao - 1) * indice / 4)) for indice in range(5)],
        "hoje_percentual": round((hoje - inicio_eixo).days / duracao * 100, 4),
    }
