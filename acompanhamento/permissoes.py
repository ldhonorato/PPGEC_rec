"""Regra de acesso às telas do módulo de acompanhamento.

O sistema tem a regra de acesso de gestão escrita duas vezes, como funções
privadas, em processos/views.py e em processos/context_processors.py. O módulo
não cria uma terceira cópia: delega para a versão de context_processors, que é
a mesma usada para montar o menu. A versão de views.py não é importada porque
o arquivo é grande e carrega praticamente o sistema inteiro.

Um teste do módulo compara as duas versões do sistema para todos os perfis.
Se uma delas mudar e a outra não, o teste quebra na sincronização com o
repositório original.
"""

from processos.context_processors import _has_gestao_access


def tem_acesso_gestao(usuario) -> bool:
    """Diz se o usuário pode usar as telas do módulo.

    Tem acesso quem o sistema considera com acesso de gestão: docente
    coordenador, servidor, bolsista ou voluntário, e membro ativo dos setores
    Secretaria PPGEC ou Coordenação PPG.
    """
    return _has_gestao_access(usuario)
