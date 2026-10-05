from django.db.models import Count, Q
from processos.models import Docente, Polo


def calcular_painel_docentes():
    """Calcula indicadores de distribuição de docentes ativos do programa.

    Considera apenas docentes com is_active=True.
    Totaliza por categoria (permanente e colaborador/externo)
    e agrupa a contagem por polo de atuação.
    """
    docentes_ativos = Docente.objects.filter(is_active=True)

    total_ativos = docentes_ativos.count()
    total_permanentes = docentes_ativos.filter(permanente=True).count()
    # No modelo, quem não é permanente é colaborador (externo)
    total_colaboradores = docentes_ativos.filter(permanente=False).count()

    # Contagem de docentes por polo cadastrado
    distribuicao_polos = (
        Polo.objects.filter(ativo=True)
        .annotate(
            total_docentes=Count(
                "servidores",
                filter=Q(servidores__is_active=True, servidores__docente__isnull=False),
            )
        )
        .values("id", "nome", "total_docentes")
        .order_by("nome")
    )

    # Docentes ativos sem nenhum polo vinculado
    sem_polo = docentes_ativos.filter(polo_atuacao__isnull=True).count()

    return {
        "total_ativos": total_ativos,
        "total_permanentes": total_permanentes,
        "total_colaboradores": total_colaboradores,
        "distribuicao_polos": list(distribuicao_polos),
        "sem_polo": sem_polo,
    }