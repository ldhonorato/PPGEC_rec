"""Linha de base do comportamento do sistema antes do módulo de acompanhamento.

Estes testes registram como o Acadflow responde hoje, sem nenhuma alteração
do módulo: o código de resposta das páginas principais para cada perfil de
usuário e o número de consultas ao banco feitas para montar o menu lateral.

Eles existem para provar que o módulo não muda nada no sistema existente.
Qualquer alteração do módulo que mude um destes números quebra o teste. Se o
número mudar por uma alteração do próprio repositório original, trazida na
sincronização, a linha de base deve ser atualizada no mesmo pull request da
sincronização, com o motivo no corpo do commit.
"""

from django.contrib.auth.hashers import make_password
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from processos import context_processors
from processos.models import Aluno, Docente, Setor, SetorMembro, User

PAGINAS = (
    "/",
    "/coordenacao/alunos/",
    "/coordenacao/dashboard/",
    "/coordenacao/processos/",
    "/coordenacao/caixa-processos/",
)

# Código de resposta esperado para cada perfil, na ordem de PAGINAS.
CODIGOS_ESPERADOS = {
    "visitante": (302, 302, 302, 302, 302),
    "aluno": (200, 403, 403, 403, 403),
    "docente": (200, 403, 403, 403, 403),
    "docente_coordenador": (200, 200, 200, 200, 200),
    "servidor": (200, 200, 200, 200, 200),
    "bolsista": (200, 200, 200, 200, 200),
    "membro_secretaria": (200, 200, 200, 200, 200),
    "membro_coordenacao": (200, 200, 200, 200, 200),
}

# Consultas ao banco feitas por navegacao_lateral para cada perfil autenticado.
# Os números altos de aluno e docente vêm do próprio menu do sistema, que é
# montado duas vezes por página. Não é papel do módulo corrigir isso; o papel
# do módulo é não aumentar nenhum destes números.
CONSULTAS_MENU_ESPERADAS = {
    "aluno": 31,
    "docente": 27,
    "docente_coordenador": 7,
    "servidor": 2,
    "bolsista": 2,
    "membro_secretaria": 19,
    "membro_coordenacao": 31,
}


def criar_perfis():
    """Cria um usuário fictício para cada perfil e devolve um dicionário por nome.

    Os modelos de aluno e de docente validam o cadastro inteiro ao salvar, e a
    senha é obrigatória. Os perfis recebem uma senha inutilizável, porque os
    testes entram com force_login e ninguém deve conseguir entrar com senha.
    """
    sem_senha = make_password(None)
    secretaria = Setor.objects.get(nome=Setor.NOME_SECRETARIA)
    coordenacao = Setor.objects.get(nome=Setor.NOME_COORDENACAO)

    perfis = {
        "aluno": Aluno.objects.create(
            nome="Aluno Base", email="aluno.base@ficticio.invalid", password=sem_senha
        ),
        "docente": Docente.objects.create(
            nome="Docente Base", email="docente.base@ficticio.invalid", password=sem_senha
        ),
        "docente_coordenador": Docente.objects.create(
            nome="Coordenador Base",
            email="coordenador.base@ficticio.invalid",
            password=sem_senha,
            coordenador=True,
        ),
        "servidor": User.objects.create(
            nome="Servidor Base",
            email="servidor.base@ficticio.invalid",
            password=sem_senha,
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        ),
        "bolsista": User.objects.create(
            nome="Bolsista Base",
            email="bolsista.base@ficticio.invalid",
            password=sem_senha,
            tipo_usuario=User.TipoUsuario.BOLSISTA_VOLUNTARIO,
        ),
        "membro_secretaria": Docente.objects.create(
            nome="Membro Secretaria Base",
            email="membro.secretaria.base@ficticio.invalid",
            password=sem_senha,
        ),
        "membro_coordenacao": Docente.objects.create(
            nome="Membro Coordenação Base",
            email="membro.coordenacao.base@ficticio.invalid",
            password=sem_senha,
        ),
    }
    SetorMembro.objects.create(setor=secretaria, usuario=perfis["membro_secretaria"])
    SetorMembro.objects.create(setor=coordenacao, usuario=perfis["membro_coordenacao"])
    return perfis


@override_settings(SECURE_SSL_REDIRECT=False)
class LinhaDeBaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfis = criar_perfis()

    def test_codigos_de_resposta_por_perfil(self):
        obtidos = {}
        for nome in CODIGOS_ESPERADOS:
            if nome == "visitante":
                self.client.logout()
            else:
                self.client.force_login(self.perfis[nome])
            obtidos[nome] = tuple(self.client.get(pagina).status_code for pagina in PAGINAS)
        self.assertEqual(obtidos, CODIGOS_ESPERADOS)

    def test_consultas_do_menu_por_perfil(self):
        fabrica = RequestFactory()
        obtidas = {}
        for nome in CONSULTAS_MENU_ESPERADAS:
            requisicao = fabrica.get("/")
            requisicao.user = self.perfis[nome]
            with CaptureQueriesContext(connection) as consultas:
                context_processors.navegacao_lateral(requisicao)
            obtidas[nome] = len(consultas)
        self.assertEqual(obtidas, CONSULTAS_MENU_ESPERADAS)
