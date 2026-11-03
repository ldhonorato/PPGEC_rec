"""Telas do módulo listadas na página inicial.

Cada tela nova do módulo entra nesta lista, e não no menu lateral do sistema.
O menu tem um único item do módulo, que leva à página inicial. Assim nenhuma
tela nova mexe no código que monta o menu em todas as páginas.

Cada item tem título, frase da tela, nome da rota e ícone de includes/icons.html.
"""

TELAS = [
    (
        "Painel de Sucesso por Turma",
        "Concluídos, desligados e ativos de cada turma.",
        "acompanhamento:painel_sucesso_turma",
        "i-dashboard",
    ),
    (
        "Painel de Processos",
        "Abertos, atrasados e tempo médio no período.",
        "acompanhamento:painel_processos",
        "i-todos-processos",
    ),
    (
        "Painel de Docentes",
        "Distribuição por polo, permanentes e colaboradores.",
        "acompanhamento:painel_docentes",
        "i-setores",
    ),
    (
        "Carga de Orientação",
        "Alunos por orientador.",
        "coordenacao_dashboard",
        "i-orientandos",
    ),
    (
        "Calendário de Marcos",
        "Datas de projeto e qualificação por semestre.",
        "acompanhamento:calendario_marcos",
        "i-periodos",
    ),
]
