from django.contrib.auth.hashers import make_password
from django.test import TestCase

from acompanhamento.servicos import calcular_painel_docentes
from processos.models import Docente, Polo


class PainelDocentesServicoTests(TestCase):
    def setUp(self):
        self.senha = make_password(None)

        # Polos de teste
        self.polo_recife = Polo.objects.create(nome="Polo Recife", ativo=True)
        self.polo_caruaru = Polo.objects.create(nome="Polo Caruaru", ativo=True)
        self.polo_inativo = Polo.objects.create(nome="Polo Inativo", ativo=False)

        # Docentes ativos permanentes
        Docente.objects.create(
            nome="Docente Permanente Recife",
            email="docente1@ficticio.invalid",
            password=self.senha,
            permanente=True,
            is_active=True,
            polo_atuacao=self.polo_recife,
        )
        Docente.objects.create(
            nome="Docente Permanente Sem Polo",
            email="docente2@ficticio.invalid",
            password=self.senha,
            permanente=True,
            is_active=True,
            polo_atuacao=None,
        )

        # Docente ativo colaborador (externo)
        Docente.objects.create(
            nome="Docente Colaborador Caruaru",
            email="docente3@ficticio.invalid",
            password=self.senha,
            permanente=False,
            externo=True,
            is_active=True,
            polo_atuacao=self.polo_caruaru,
        )

        # Docente inativo (não deve entrar nas contagens do painel)
        Docente.objects.create(
            nome="Docente Inativo",
            email="inativo@ficticio.invalid",
            password=self.senha,
            permanente=True,
            is_active=False,
            polo_atuacao=self.polo_recife,
        )

    def test_calcular_painel_docentes_totais_e_categorias(self):
        # Guarda as quantidades antes de criar novos docentes
        total_iniciais = 38
        perm_iniciais = 29
        colab_iniciais = 9

        dados = calcular_painel_docentes()

        # 38 da migração + 3 ativos criados no setUp = 41
        self.assertEqual(dados["total_ativos"], total_iniciais + 3)
        # 29 da migração + 2 permanentes criados = 31
        self.assertEqual(dados["total_permanentes"], perm_iniciais + 2)
        # 9 da migração + 1 colaborador criado = 10
        self.assertEqual(dados["total_colaboradores"], colab_iniciais + 1)
        
    def test_calcular_painel_docentes_distribuicao_polos(self):
        dados = calcular_painel_docentes()

        # Apenas polos ativos são considerados na listagem
        polos_dict = {p["nome"]: p["total_docentes"] for p in dados["distribuicao_polos"]}

        self.assertIn("Polo Recife", polos_dict)
        self.assertIn("Polo Caruaru", polos_dict)
        self.assertNotIn("Polo Inativo", polos_dict)

        self.assertEqual(polos_dict["Polo Recife"], 1)
        self.assertEqual(polos_dict["Polo Caruaru"], 1)