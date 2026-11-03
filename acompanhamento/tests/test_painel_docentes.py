from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings
from django.urls import reverse

from processos.models import Docente, Polo
from acompanhamento.servicos import calcular_painel_docentes
from acompanhamento.tests.test_linha_de_base import criar_perfis

User = get_user_model()


class ServicoPainelDocentesTests(TestCase):
    def setUp(self):
        self.senha = make_password("senha123")
        self.polo_ativo = Polo.objects.create(nome="Polo Recife", ativo=True)
        self.polo_inativo = Polo.objects.create(nome="Polo Inativo", ativo=False)
        self.polo_sede, _ = Polo.objects.get_or_create(nome="Polo Sede", defaults={"ativo": True})

    def _criar_docente(self, nome, email, is_active=True, permanente=True, externo=False, polo=None):
        return Docente.objects.create(
            nome=nome,
            email=email,
            password=self.senha,
            is_active=is_active,
            permanente=permanente,
            externo=externo,
            polo_atuacao=polo,
        )

    def test_percentuais_de_permanentes_e_colaboradores_somam_cem(self):
        Docente.objects.update(is_active=False)

        for indice in range(3):
            self._criar_docente(
                f"Docente permanente {indice}",
                f"permanente{indice}@teste.com",
                permanente=True,
            )
        self._criar_docente(
            "Docente colaborador",
            "colaborador@teste.com",
            permanente=False,
        )

        resultado = calcular_painel_docentes()

        self.assertEqual(resultado["percentual_permanentes"], Decimal("75.0"))
        self.assertEqual(resultado["percentual_colaboradores"], Decimal("25.0"))

    def test_percentuais_sao_zero_sem_docentes_ativos(self):
        Docente.objects.update(is_active=False)

        resultado = calcular_painel_docentes()

        self.assertEqual(resultado["total_ativos"], 0)
        self.assertEqual(resultado["percentual_permanentes"], Decimal("0.0"))
        self.assertEqual(resultado["percentual_colaboradores"], Decimal("0.0"))

    def test_distribuicao_percentuais_somam_cem_por_cento(self):
        antes = calcular_painel_docentes()

        self._criar_docente("Doc P1", "p1@teste.com", permanente=True, externo=False, polo=self.polo_ativo)
        self._criar_docente("Doc P2", "p2@teste.com", permanente=True, externo=False, polo=self.polo_ativo)
        self._criar_docente("Doc Ext", "ext@teste.com", permanente=False, externo=True, polo=None)
        self._criar_docente("Doc Sem Polo", "sp@teste.com", permanente=True, externo=False, polo=None)

        depois = calcular_painel_docentes()
        self.assertEqual(depois["total_ativos"], antes["total_ativos"] + 4)

        if depois["distribuicao_polos"]:
            soma = sum(item["percentual"] for item in depois["distribuicao_polos"])
            self.assertAlmostEqual(soma, 100.0, places=1)

    def test_externos_formam_linha_propria(self):
        antes_externos = calcular_painel_docentes()["total_externos"]
        self._criar_docente("Doc Ext Com Polo", "extp@teste.com", permanente=True, externo=True, polo=self.polo_ativo)

        depois = calcular_painel_docentes()
        self.assertEqual(depois["total_externos"], antes_externos + 1)

        linha_externo = next((item for item in depois["distribuicao_polos"] if item["nome"] == "Externo à UPE"), None)
        self.assertIsNotNone(linha_externo)
        self.assertGreaterEqual(linha_externo["total_docentes"], 1)

    def test_polo_nao_informado_inclui_sem_polo_polo_sede_e_inativos(self):
        self._criar_docente("Doc SP", "sp_nao@teste.com", permanente=True, externo=False, polo=None)
        self._criar_docente("Doc Sede", "sede@teste.com", permanente=True, externo=False, polo=self.polo_sede)
        self._criar_docente("Doc Inat", "inat@teste.com", permanente=True, externo=False, polo=self.polo_inativo)

        resultado = calcular_painel_docentes()
        linha_nao_informado = next(
            (item for item in resultado["distribuicao_polos"] if item["nome"] == "Polo não informado"),
            None,
        )
        self.assertIsNotNone(linha_nao_informado)
        self.assertGreaterEqual(linha_nao_informado["total_docentes"], 3)

    def test_filtro_somente_permanentes(self):
        self._criar_docente("Doc Perm", "perm_f@teste.com", permanente=True, externo=False, polo=self.polo_ativo)
        self._criar_docente("Doc Colab", "colab_f@teste.com", permanente=False, externo=False, polo=self.polo_ativo)

        todos = calcular_painel_docentes(somente_permanentes=False)
        so_perm = calcular_painel_docentes(somente_permanentes=True)

        self.assertGreater(todos["total_distribuicao"], so_perm["total_distribuicao"])
        self.assertEqual(so_perm["total_distribuicao"], so_perm["total_permanentes"])

    def test_mudanca_de_categoria_refletida_na_proxima_consulta(self):
        doc = self._criar_docente("Doc Mutavel", "mut@teste.com", permanente=False, externo=False, polo=self.polo_ativo)

        res1 = calcular_painel_docentes()
        total_colab_1 = res1["total_colaboradores"]

        doc.permanente = True
        doc.save()

        res2 = calcular_painel_docentes()
        self.assertEqual(res2["total_colaboradores"], total_colab_1 - 1)
        self.assertEqual(res2["total_permanentes"], res1["total_permanentes"] + 1)

@override_settings(SECURE_SSL_REDIRECT=False)
class VisualizacaoPainelDocentesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfis = criar_perfis()
        cls.url = reverse("acompanhamento:painel_docentes")

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_servidor_com_chave_ligada_acessa_com_sucesso_200(self):
        self.client.force_login(self.perfis["servidor"])
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_aluno_recebe_403(self):
        self.client.force_login(self.perfis["aluno"])
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 403)

    @override_settings(ACOMPANHAMENTO_ATIVO=False)
    def test_chave_desligada_retorna_404(self):
        self.client.force_login(self.perfis["servidor"])
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 404)
