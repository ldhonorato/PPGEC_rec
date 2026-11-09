from django.utils import timezone

from acompanhamento.models import MarcoCalendario
from acompanhamento.situacao import calcular_situacoes
from processos.models import TrajetoriaAcademica


def calcular_previsao_conclusao(orientador_id, nivel, data_referencia=None):
    """
    Calcula a previsão de conclusão dos alunos ativos de um orientador em determinado nível.
    Retorna resumo por semestre, contagem de prazos vencidos, sem previsão e lista detalhada.
    """
    if data_referencia is None:
        data_referencia = timezone.localdate()

    trajetorias_qs = (
        TrajetoriaAcademica.objects.filter(
            nivel_curso=nivel,
            status=TrajetoriaAcademica.Status.ATIVA,
        )
        .select_related("aluno", "orientador")
        .prefetch_related("prorrogacoes", "trancamentos")
        .order_by("aluno__nome")
    )

    if orientador_id is None:
        trajetorias_qs = trajetorias_qs.filter(orientador__isnull=True)
    else:
        trajetorias_qs = trajetorias_qs.filter(orientador_id=orientador_id)

    ativas = list(trajetorias_qs)
    situacoes = calcular_situacoes(ativas, data_referencia)

    alunos_lista = []
    semestres_contagem = {}
    total_prazo_vencido = 0
    total_sem_previsao = 0

    for traj in ativas:
        aluno_nome = traj.aluno.nome if traj.aluno else "Aluno não informado"
        turma_ingresso = traj.ingresso or "Não informada"

        dados_defesa = situacoes.get(traj.pk, {}).get("defesa") if traj.data_ingresso else None
        data_limite = dados_defesa.data_limite if dados_defesa else None

        if not data_limite:
            total_sem_previsao += 1
            alunos_lista.append(
                {
                    "nome": aluno_nome,
                    "turma": turma_ingresso,
                    "data_limite": None,
                    "semestre": "",
                }
            )
            continue

        if data_limite < data_referencia:
            total_prazo_vencido += 1
            semestre_previsao = ""
        else:
            semestre_previsao = MarcoCalendario.semestre_da_data(data_limite) or ""
            if semestre_previsao:
                semestres_contagem[semestre_previsao] = (
                    semestres_contagem.get(semestre_previsao, 0) + 1
                )

        alunos_lista.append(
            {
                "nome": aluno_nome,
                "turma": turma_ingresso,
                "data_limite": data_limite,
                "semestre": semestre_previsao,
            }
        )

    por_semestre = [
        {"semestre": sem, "alunos": qtd}
        for sem, qtd in sorted(semestres_contagem.items())
    ]

    return {
        "por_semestre": por_semestre,
        "prazo_vencido": total_prazo_vencido,
        "sem_previsao": total_sem_previsao,
        "alunos": alunos_lista,
    }
