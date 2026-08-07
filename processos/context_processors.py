from django.db.models import Q

from .services import processos_atrasados_queryset, processos_atrasados_url
from .models import Aluno, Docente, Setor, SetorMembro, SolicitacaoAssinatura, User


def processos_atrasados(request):
    if not request.user.is_authenticated:
        return {}

    return {
        "processos_atrasados_count": processos_atrasados_queryset(request.user).count(),
        "processos_atrasados_url": processos_atrasados_url(request.user),
    }


def _is_docente(user):
    return user.is_authenticated and user.tipo_usuario == User.TipoUsuario.DOCENTE


def _is_servidor(user):
    return user.is_authenticated and user.tipo_usuario == User.TipoUsuario.SERVIDOR


def _is_secretaria_member(user):
    return user.is_authenticated and SetorMembro.objects.filter(
        usuario=user,
        data_saida__isnull=True,
        setor__ativo=True,
        setor__nome="Secretaria PPGEC",
    ).exists()


def _is_coordenador(user):
    if not _is_docente(user):
        return False

    try:
        return bool(user.docente.coordenador)
    except Docente.DoesNotExist:
        return False


def _has_gestao_access(user):
    return _is_coordenador(user) or _is_servidor(user) or _is_secretaria_member(user)


def _can_view_processos(user):
    return _has_gestao_access(user)


def _can_add_processo(user):
    if not user.is_authenticated or _is_servidor(user):
        return False
    if user.tipo_usuario == User.TipoUsuario.ALUNO:
        return not Aluno.objects.filter(
            pk=user.pk,
            status_aluno=Aluno.StatusAluno.EM_AVALIACAO,
        ).exists()
    return True


def _can_view_caixa(user):
    return (
        _is_servidor(user)
        or SetorMembro.objects.filter(usuario=user, data_saida__isnull=True, setor__ativo=True).exists()
    )


def _has_setor_membership(user):
    return SetorMembro.objects.filter(usuario=user, data_saida__isnull=True, setor__ativo=True).exists()


def _is_membro_setor_nome(user, nome):
    return SetorMembro.objects.filter(
        usuario=user,
        data_saida__isnull=True,
        setor__ativo=True,
        setor__nome=nome,
    ).exists()


def _has_assinaturas_access(user):
    if _has_gestao_access(user):
        return True
    if _is_docente(user):
        return True
    setores_ids = SetorMembro.objects.filter(
        usuario=user,
        data_saida__isnull=True,
        setor__ativo=True,
    ).values_list("setor_id", flat=True)
    return SolicitacaoAssinatura.objects.filter(
        Q(docente=user) | Q(setor_id__in=setores_ids) | Q(criado_por=user)
    ).exists()


# Descricao curta exibida abaixo do rotulo no menu lateral. Fica num mapa por
# rotulo em vez de argumento em cada _menu_item para manter as chamadas legiveis
# e o texto todo num lugar so -- e o tipo de conteudo que se revisa em conjunto.
DESCRICOES_MENU = {
    "Início": "Visão geral do seu perfil",
    # Processos
    "Meus Processos": "Processos que você abriu",
    "Novo Processo": "Abrir um novo requerimento",
    "Caixa de Processos": "Aguardando análise do seu setor",
    "Processos": "Buscar em todos os processos",
    "Processos no Pleno": "Em debate no colegiado",
    "Processos dos Orientandos": "Acompanhar seus orientandos",
    # Aluno
    "Matrícula": "Suas solicitações do período",
    "Histórico Escolar": "Emissão em construção",
    "Documento de Vínculo": "Comprovante de vínculo",
    "Minha Trajetória": "Créditos, prazos e integralização",
    # Ambientes
    "Reserva de Ambiente": "Salas e horários do programa",
    "Nova reserva de ambiente": "Reservar sala para uma data",
    "Disponibilidade semanal": "Grade de ocupação das salas",
    "Reservas feitas": "Suas reservas registradas",
    "Cadastro de Salas": "Ambientes disponíveis para reserva",
    # Docente
    "Ofertas de Disciplinas": "Turmas que você leciona",
    "Ciências": "Ciências solicitadas a você",
    "Meus Orientandos": "Alunos sob sua orientação",
    "Solicitação de Banca": "Qualificação e defesa",
    # Gestao
    "Dashboard": "Indicadores do programa",
    "Matrículas": "Períodos, turmas e solicitações",
    "Períodos letivos": "Calendário e prazos do semestre",
    "Solicitações": "Pedidos de matrícula dos alunos",
    "Disciplinas": "Catálogo e ementários",
    "Ofertas de disciplinas": "Turmas abertas no período",
    "Alunos": "Cadastro e trajetória dos alunos",
    "Validar Cadastros": "Cadastros aguardando aprovação",
    "Cadastro": "Inclusão e validação de pessoas",
    "Declarações de vínculo": "Comprovantes do semestre",
    "Cadastro de ingressantes": "Importar novos alunos por planilha",
    "Setores e Comissões": "Estrutura de tramitação",
    "Criar Comissão": "Montar uma nova comissão",
    # Assinaturas
    "Assinaturas": "Solicitações de assinatura",
    "Nova solicitação": "Pedir assinatura de um documento",
    "Pendências de assinatura": "Documentos aguardando você",
    "Solicitações feitas": "Pedidos que você enviou",
}


def _menu_item(label, href, url_names, icon, children=None):
    return {
        "label": label,
        "descricao": DESCRICOES_MENU.get(label, ""),
        "href": href,
        "url_names": url_names,
        "icon": icon,
        "children": children or [],
    }


def _menu_lateral_sections(user):
    sections = []

    # Telas de detalhe (um processo, um aluno) nao tem entrada propria no menu: sao
    # destinos de uma listagem. Sem declarar o url_name na listagem que leva ate elas,
    # abrir um processo apagava a barra inteira -- nenhum item ficava marcado e o
    # usuario perdia a referencia de onde estava.
    #
    # Cada detalhe pertence a UM item por perfil, senao dois acendem ao mesmo tempo.
    # Quem tem gestao chega no processo/aluno pelas listagens da Coordenacao; quem nao
    # tem, chega pelas telas pessoais. O criterio abaixo divide por esse acesso.
    tem_gestao = _has_gestao_access(user)

    # Inicio abre a secao em todos os perfis. Antes so se chegava a tela inicial
    # clicando na logo da barra superior, o que nao e um destino obvio.
    principal_items = [_menu_item("Início", "/", ["home"], "inicio")]
    if user.tipo_usuario != User.TipoUsuario.SERVIDOR:
        principal_items.append(
            _menu_item(
                "Meus Processos",
                "/menu/meus-processos/",
                ["menu_meus_processos"] if tem_gestao else ["menu_meus_processos", "processo_detalhe"],
                "meus-processos",
            )
        )
        if _can_add_processo(user):
            principal_items.append(_menu_item("Novo Processo", "/processos/novo/", ["novo_processo"], "novo-processo"))
    if user.tipo_usuario == User.TipoUsuario.ALUNO:
        principal_items.append(
            _menu_item(
                "Matrícula",
                "/matriculas/",
                ["matriculas_minhas", "matricula_solicitar", "matricula_solicitar_periodo", "matricula_minha_solicitacao"],
                "matricula",
            )
        )
        principal_items.append(
            _menu_item("Documento de Vínculo", "/aluno/documento-vinculo/", ["aluno_documento_vinculo"], "documento-vinculo")
        )
        # Em construcao, e ainda assim no menu: so se chegava a ela por endereco
        # direto, o que na pratica e nao existir. A descricao diz o estado antes
        # do toque -- ela aparece tanto na barra lateral quanto na folha --, e a
        # tela repete com todas as letras e oferece o caminho que hoje resolve.
        principal_items.append(
            _menu_item(
                "Histórico Escolar",
                "/aluno/documento-historico/",
                ["aluno_documento_historico"],
                "disciplinas",
            )
        )
        principal_items.append(
            _menu_item("Minha Trajetória", f"/coordenacao/alunos/{user.id}/", ["aluno_detalhe"], "trajetoria")
        )
    if user.tipo_usuario in {User.TipoUsuario.DOCENTE, User.TipoUsuario.SERVIDOR} or _is_secretaria_member(user):
        principal_items.append(
            _menu_item(
                "Reserva de Ambiente",
                "/ambientes/reservas/",
                ["reservas_ambientes", "disponibilidade_ambientes", "reservas_ambientes_feitas"],
                "ambiente",
                children=[
                    _menu_item("Nova reserva de ambiente", "/ambientes/reservas/", ["reservas_ambientes"], "nova-reserva"),
                    _menu_item(
                        "Disponibilidade semanal",
                        "/ambientes/disponibilidade/",
                        ["disponibilidade_ambientes"],
                        "disponibilidade",
                    ),
                    _menu_item(
                        "Reservas feitas",
                        "/ambientes/reservas/feitas/",
                        ["reservas_ambientes_feitas"],
                        "reservas-feitas",
                    ),
                ],
            )
        )
    has_setor_membership = _has_setor_membership(user)
    if has_setor_membership:
        principal_items.append(
            _menu_item(
                "Caixa de Processos",
                "/coordenacao/caixa-processos/",
                ["coordenacao_caixa_processos"],
                "caixa",
            )
        )
    if _has_assinaturas_access(user) and not _has_gestao_access(user):
        principal_items.append(
            _menu_item(
                "Assinaturas",
                "/assinaturas/pendentes/",
                # solicitacoes_assinatura (/assinaturas/) tambem responde para quem
                # so tem acesso de assinatura, e nao acendia item nenhum.
                ["pendencias_assinatura", "solicitacoes_assinatura", "solicitacao_assinatura_detalhe"],
                "assinaturas",
            )
        )
    if principal_items:
        sections.append({"label": "Principal", "items": principal_items})

    if user.tipo_usuario == User.TipoUsuario.DOCENTE:
        docente_items = [
            _menu_item("Ofertas de Disciplinas", "/gestao/matriculas/ofertas/", ["matriculas_ofertas"], "ofertas"),
            _menu_item("Ciências", "/menu/ciencias-manifestadas/", ["menu_ciencias_manifestadas"], "ciencias"),
            _menu_item(
                "Meus Orientandos",
                "/menu/meus-orientandos/",
                # O orientador passou a poder abrir a trajetoria do orientando; sem
                # aluno_detalhe aqui, essa tela nao acendia nada para o docente.
                ["menu_meus_orientandos"] if tem_gestao else ["menu_meus_orientandos", "aluno_detalhe"],
                "orientandos",
            ),
            _menu_item(
                "Solicitação de Banca",
                "/bancas/",
                ["solicitacoes_banca", "solicitacao_banca_nova", "solicitacao_banca_detalhe"],
                "banca",
            ),
            _menu_item(
                "Processos dos Orientandos",
                "/menu/processos-orientandos/",
                ["menu_processos_orientandos"],
                "processos-orientandos",
            ),
        ]
        if _is_membro_setor_nome(user, Setor.NOME_PLENO):
            docente_items.insert(
                0,
                _menu_item("Processos no Pleno", "/menu/processos-pleno/", ["menu_processos_pleno"], "pleno"),
            )
        sections.append({"label": "Docente", "items": docente_items})

    if _has_gestao_access(user):
        coordenacao_items = []
        if not has_setor_membership:
            coordenacao_items.append(
                _menu_item(
                    "Caixa de Processos",
                    "/coordenacao/caixa-processos/",
                    ["coordenacao_caixa_processos"],
                    "caixa",
                )
            )
        coordenacao_items.extend([
            # A listagem geral nao tinha entrada no menu: so se chegava nela pelo
            # badge de atrasados da barra superior (ja filtrado) ou por um card da
            # home. E a unica tela com busca por numero, assunto e descricao.
            _menu_item(
                "Processos",
                "/coordenacao/processos/",
                ["coordenacao_processos", "processo_detalhe"],
                "todos-processos",
            ),
            _menu_item("Dashboard", "/coordenacao/dashboard/", ["coordenacao_dashboard"], "dashboard"),
            _menu_item(
                "Matrículas",
                "/gestao/matriculas/periodos/",
                [
                    "matriculas_periodos",
                    "matriculas_solicitacoes",
                    "matriculas_disciplinas",
                    "matriculas_ofertas",
                    "matricula_oferta_alunos",
                    "matricula_oferta_exportar",
                    "matricula_oferta_planejamento_presencial",
                ],
                "matriculas",
                children=[
                    _menu_item("Períodos letivos", "/gestao/matriculas/periodos/", ["matriculas_periodos"], "periodos"),
                    _menu_item(
                        "Solicitações",
                        "/gestao/matriculas/solicitacoes/",
                        ["matriculas_solicitacoes"],
                        "solicitacoes",
                    ),
                    _menu_item("Disciplinas", "/gestao/matriculas/disciplinas/", ["matriculas_disciplinas"], "disciplinas"),
                    _menu_item(
                        "Ofertas de disciplinas",
                        "/gestao/matriculas/ofertas/",
                        [
                            "matriculas_ofertas",
                            "matricula_oferta_alunos",
                            "matricula_oferta_exportar",
                            "matricula_oferta_planejamento_presencial",
                        ],
                        "ofertas",
                    ),
                ],
            ),
            _menu_item(
                "Cadastro",
                "/coordenacao/alunos/",
                ["coordenacao_alunos", "aluno_detalhe", "validar_cadastros_alunos", "importar_ingressantes"],
                "alunos",
                children=[
                    _menu_item("Alunos", "/coordenacao/alunos/", ["coordenacao_alunos", "aluno_detalhe"], "alunos"),
                    _menu_item(
                        "Cadastro de ingressantes",
                        "/gestao/cadastro/ingressantes/",
                        ["importar_ingressantes"],
                        "novo-processo",
                    ),
                    _menu_item(
                        "Declarações de vínculo",
                        "/gestao/declaracoes-vinculo/",
                        ["declaracoes_vinculo"],
                        "documento-vinculo",
                    ),
                    _menu_item(
                        "Validar Cadastros",
                        "/coordenacao/alunos/cadastros/",
                        ["validar_cadastros_alunos"],
                        "validar",
                    ),
                ],
            ),
            _menu_item("Setores e Comissões", "/coordenacao/setores/", ["setores_comissoes"], "setores"),
        ])
        coordenacao_items.append(
            _menu_item(
                "Assinaturas",
                "/assinaturas/",
                [
                    "nova_solicitacao_assinatura",
                    "pendencias_assinatura",
                    "solicitacoes_assinatura",
                    "solicitacao_assinatura_detalhe",
                ],
                "assinaturas",
                children=[
                    _menu_item(
                        "Nova solicitação",
                        "/assinaturas/nova/",
                        ["nova_solicitacao_assinatura"],
                        "nova-solicitacao",
                    ),
                    _menu_item(
                        "Pendências de assinatura",
                        "/assinaturas/pendentes/",
                        ["pendencias_assinatura"],
                        "pendencias",
                    ),
                    _menu_item(
                        "Solicitações feitas",
                        "/assinaturas/",
                        ["solicitacoes_assinatura"],
                        "solicitacoes-feitas",
                    ),
                ],
            )
        )
        if _is_coordenador(user):
            coordenacao_items.append(
                _menu_item("Criar Comissão", "/coordenacao/setores/criar/", ["criar_comissao"], "criar-comissao")
            )
        if _has_gestao_access(user):
            # "Reservas de Salas" existia aqui apontando para /ambientes/reservas/feitas/,
            # o mesmo destino de "Reservas feitas" (submenu Reserva de Ambiente). O item era
            # montado aqui e escondido por um {% if %} fixo no base.html; removido na origem.
            coordenacao_items.append(
                _menu_item("Cadastro de Salas", "/ambientes/salas/", ["salas_ambientes"], "salas")
            )
        sections.append({"label": "Coordenação", "items": coordenacao_items})

    return sections


def _menu_lateral_items(user):
    sections = _menu_lateral_sections(user)
    if not sections:
        return []
    items = []
    for section in sections:
        items.extend(section["items"])
    return items


# Os tres destinos que a barra flutuante oferece em tela estreita, por perfil.
#
# Nao e o menu resumido: e o que a pessoa abre todo dia. O aluno vive entre os
# processos que abriu e a matricula do periodo; o docente, entre os processos e
# os orientandos; o servidor, entre a caixa que recebe os processos do setor e a
# listagem de alunos. O resto continua na gaveta, que a propria barra abre.
#
# Sao rotulos, e nao rotas, de proposito: o item vem inteiro do menu lateral --
# endereco, icone e a lista de url_names que decide quando ele acende. Assim a
# barra nao pode discordar do menu sobre onde um destino fica ou quando ele esta
# ativo, que e a divergencia que uma segunda lista de rotas produziria.
BARRA_FLUTUANTE = {
    User.TipoUsuario.ALUNO: ("Início", "Meus Processos", "Matrícula"),
    User.TipoUsuario.DOCENTE: ("Início", "Meus Processos", "Meus Orientandos"),
    User.TipoUsuario.SERVIDOR: ("Início", "Caixa de Processos", "Alunos"),
}

# O circulo de destaque e uma acao, nao um destino: abre um processo. Quem nao
# pode abrir -- o servidor, por regra do proprio sistema -- fica sem ele, e a
# barra centraliza.
ROTULO_ACAO_FLUTUANTE = "Novo Processo"


def _barra_flutuante(user, items):
    """Os destinos do menu que a barra mostra, na ordem em que os mostra.

    Percorre tambem os filhos dos grupos: "Alunos" e "Disciplinas" nao ficam no
    primeiro nivel do menu do servidor, e procurar so na superficie devolvia uma
    barra mais curta sem nenhum sinal de que faltava alguem.
    """
    por_rotulo = {}
    pendentes = list(items)
    while pendentes:
        item = pendentes.pop(0)
        por_rotulo.setdefault(item["label"], item)
        pendentes.extend(item["children"])

    rotulos = BARRA_FLUTUANTE.get(user.tipo_usuario, ())
    return [por_rotulo[rotulo] for rotulo in rotulos if rotulo in por_rotulo]


def navegacao_lateral(request):
    if not request.user.is_authenticated:
        return {}

    has_gestao_access = _has_gestao_access(request.user)
    can_view_processos = _can_view_processos(request.user)
    solicitar_cpf_aluno = False
    if request.user.tipo_usuario == User.TipoUsuario.ALUNO:
        try:
            solicitar_cpf_aluno = not bool(request.user.aluno.cpf)
        except Aluno.DoesNotExist:
            pass
    # Um levantamento so: a barra flutuante escolhe entre os mesmos itens que a
    # gaveta mostra, e montar o menu duas vezes repetiria as consultas de setor.
    itens_do_menu = _menu_lateral_items(request.user)
    return {
        "is_coordenador": _is_coordenador(request.user),
        "has_gestao_access": has_gestao_access,
        "can_view_dashboard": has_gestao_access,
        "can_view_processos": can_view_processos,
        "can_view_caixa": _can_view_caixa(request.user),
        "nav_has_gestao_access": has_gestao_access,
        "nav_can_view_dashboard": has_gestao_access,
        "nav_can_view_processos": can_view_processos,
        "nav_can_view_caixa": _can_view_caixa(request.user),
        # A casca da aplicacao entra por padrao; as telas de acesso desligam
        # com extra_context, porque tem layout proprio de janela inteira.
        "mostra_moldura": True,
        "nav_menu_sections": _menu_lateral_sections(request.user),
        "nav_side_menu_items": itens_do_menu,
        "barra_flutuante": _barra_flutuante(request.user, itens_do_menu),
        "barra_flutuante_acao": next(
            (item for item in itens_do_menu if item["label"] == ROTULO_ACAO_FLUTUANTE),
            None,
        ),
        "solicitar_cpf_aluno": solicitar_cpf_aluno,
    }
