"""Telas do módulo listadas na página inicial.

Cada tela nova do módulo entra nesta lista, e não no menu lateral do sistema.
O menu tem um único item do módulo, que leva à página inicial. Assim nenhuma
tela nova mexe no código que monta o menu em todas as páginas.

Cada item é um par (título, nome da rota).
"""

TELAS = (
    ("Painel de Docentes", "acompanhamento:painel_docentes"),
)