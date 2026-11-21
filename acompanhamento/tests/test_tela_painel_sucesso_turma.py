from datetime import date

from django.test import TestCase, override_settings
from django.urls import reverse

from acompanhamento.tests.test_fundacao import PERFIS_COM_GESTAO
from acompanhamento.tests.test_linha_de_base import criar_perfis
from processos.models import Aluno, TrajetoriaAcademica


@override_settings(ACOMPANHAMENTO_ATIVO=True, SECURE_SSL_REDIRECT=False)
class TelaPainelSucessoTurmaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfis = criar_perfis()
        cls.url = reverse("acompanhamento:painel_sucesso_turma")

    def _trajetoria(self, email, status, ingresso, data_ingresso, **campos):
        aluno = Aluno.objects.create_user(email=email, nome=email.split("@")[0])
        return TrajetoriaAcademica.objects.create(
            aluno=aluno,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=status,
            ingresso=ingresso,
            data_ingresso=data_ingresso,
            **campos,
        )

    def _turma(self, response, nome):
        return next(turma for turma in response.context["turmas"] if turma["turma"] == nome)

    def _turmas_dos_anos(self):
        for ano in (2097, 2098, 2099):
            self._trajetoria(
                f"ano{ano}.painel@teste.invalid",
                TrajetoriaAcademica.Status.ATIVA,
                f"{ano}.1",
                date(ano, 3, 1),
            )

    @override_settings(ACOMPANHAMENTO_ATIVO=False)
    def test_chave_desligada_retorna_404(self):
        """Garante que a rota responde 404 quando o módulo está inativo."""
        self.client.force_login(self.perfis["membro_secretaria"])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_acesso_por_perfil(self):
        """Só os perfis da gestão entram; os outros recebem 403."""
        for nome, usuario in self.perfis.items():
            with self.subTest(perfil=nome):
                self.client.force_login(usuario)
                esperado = 200 if nome in PERFIS_COM_GESTAO else 403
                self.assertEqual(self.client.get(self.url).status_code, esperado)

    def test_nivel_escolhido_aparece_selecionado(self):
        """O nível escolhido no filtro continua selecionado na tela."""
        self.client.force_login(self.perfis["membro_secretaria"])
        response = self.client.get(self.url, {"nivel": "DOUTORADO"})
        self.assertEqual(response.context["nivel_selecionado"], "DOUTORADO")
        self.assertContains(response, '<option value="DOUTORADO" selected>', html=False)

    def test_nivel_invalido_volta_ao_mestrado_com_mensagem(self):
        """Nível inválido na URL volta ao padrão, Mestrado, e avisa."""
        self.client.force_login(self.perfis["membro_secretaria"])
        response = self.client.get(self.url, {"nivel": "INVALIDO"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["nivel_selecionado"], "MESTRADO")
        self.assertContains(response, "Nível inválido. Escolha entre Mestrado ou Doutorado.")

    def test_barras_usam_o_percentual_sobre_os_ingressantes(self):
        """Turma com 4 ingressantes: 1 concluído, 1 desligado e 2 trancados."""
        self._trajetoria(
            "concluido.painel@teste.invalid",
            TrajetoriaAcademica.Status.CONCLUIDA,
            "2099.1",
            date(2099, 3, 1),
            isQualificado=True,
            numero_defesa="DEF-PAINEL-1",
            data_defesa=date(2100, 3, 1),
        )
        self._trajetoria(
            "desligado.painel@teste.invalid",
            TrajetoriaAcademica.Status.DESLIGADA,
            "2099.1",
            date(2099, 3, 1),
        )
        for i in range(2):
            self._trajetoria(
                f"trancado{i}.painel@teste.invalid",
                TrajetoriaAcademica.Status.TRANCADA,
                "2099.1",
                date(2099, 3, 1),
            )

        self.client.force_login(self.perfis["membro_secretaria"])
        response = self.client.get(self.url, {"nivel": "MESTRADO"})

        turma = self._turma(response, "2099.1")
        self.assertEqual(turma["ingressantes"], 4)
        barras = {barra["rotulo"]: barra for barra in turma["barras"]}
        self.assertEqual(barras["Concluídos"]["total"], "1 (25,0%)")
        self.assertEqual(barras["Concluídos"]["largura"], 25.0)
        self.assertEqual(barras["Trancados"]["total"], "2 (50,0%)")
        self.assertEqual(barras["Trancados"]["largura"], 50.0)
        self.assertEqual(barras["Ativos"]["largura"], 0.0)
        self.assertContains(response, "Turma 2099.1")
        self.assertContains(response, "4 ingressantes")
        self.assertContains(response, 'style="height: 50.0%"', html=False)

    def test_aviso_so_nas_turmas_anteriores_a_2026_2(self):
        """O aviso de dados incompletos aparece até 2026.1, e não a partir de 2026.2."""
        self._trajetoria(
            "antiga.painel@teste.invalid",
            TrajetoriaAcademica.Status.DESLIGADA,
            "2026.1",
            date(2026, 3, 1),
        )
        self._trajetoria(
            "nova.painel@teste.invalid",
            TrajetoriaAcademica.Status.DESLIGADA,
            "2026.2",
            date(2026, 8, 1),
        )

        self.client.force_login(self.perfis["membro_secretaria"])
        response = self.client.get(self.url, {"nivel": "MESTRADO"})

        self.assertTrue(self._turma(response, "2026.1")["dados_limitados"])
        self.assertFalse(self._turma(response, "2026.2")["dados_limitados"])
        self.assertContains(
            response,
            "Esta consulta inclui turmas anteriores a 2026.2 (marcadas com *).",
        )

    def test_grafico_tem_sete_barras_por_turma(self):
        """Turma 2099.1 com 4 ingressantes e 1 concluído; a 2097.1 vem antes."""
        self._trajetoria(
            "concluido.grafico@teste.invalid",
            TrajetoriaAcademica.Status.CONCLUIDA,
            "2099.1",
            date(2099, 3, 1),
            isQualificado=True,
            numero_defesa="DEF-GRAFICO-1",
            data_defesa=date(2100, 3, 1),
        )
        for i in range(3):
            self._trajetoria(
                f"ativo{i}.grafico@teste.invalid",
                TrajetoriaAcademica.Status.ATIVA,
                "2099.1",
                date(2099, 3, 1),
            )
        self._trajetoria(
            "antigo.grafico@teste.invalid",
            TrajetoriaAcademica.Status.ATIVA,
            "2097.1",
            date(2097, 3, 1),
        )

        self.client.force_login(self.perfis["membro_secretaria"])
        response = self.client.get(self.url, {"nivel": "MESTRADO"})

        grafico = response.context["turmas_grafico"]
        ordem = [turma["turma"] for turma in grafico]
        self.assertLess(ordem.index("2097.1"), ordem.index("2099.1"))
        barras = grafico[ordem.index("2099.1")]["barras"]
        self.assertEqual(
            [barra["rotulo"] for barra in barras],
            [
                "Concluídos",
                "Desligados",
                "Trancados",
                "Ativos",
                "Ativos regulares",
                "Ativos irregulares",
                "Com prorrogação",
            ],
        )
        self.assertEqual(barras[0]["quantidade"], 1)
        self.assertEqual(barras[0]["largura"], 25.0)
        self.assertContains(response, 'style="height: 25.0%"', html=False)
        self.assertContains(response, "Concluídos: 1 (25,0%)")
        self.assertContains(response, "data-grafico-rolagem")
        self.assertContains(response, "<span>100%</span>", html=True)

    def test_tela_listada_na_pagina_inicial_do_modulo(self):
        """Garante que a nova tela está registrada e visível na página inicial do módulo."""
        self.client.force_login(self.perfis["membro_secretaria"])
        response = self.client.get(reverse("acompanhamento:acompanhamento_inicio"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.url)

    def test_filtro_de_anos_traz_so_o_intervalo_e_mantem_os_campos(self):
        self._turmas_dos_anos()
        self.client.force_login(self.perfis["membro_secretaria"])

        response = self.client.get(
            self.url, {"nivel": "MESTRADO", "ano_inicial": "2098", "ano_final": "2099"}
        )

        ordem = [turma["turma"] for turma in response.context["turmas_grafico"]]
        self.assertEqual(ordem, ["2098.1", "2099.1"])
        self.assertContains(response, 'value="2098"', html=False)
        self.assertContains(response, 'value="2099"', html=False)

    def test_anos_vazios_trazem_todas_as_turmas_sem_mensagem(self):
        self._turmas_dos_anos()
        self.client.force_login(self.perfis["membro_secretaria"])

        response = self.client.get(
            self.url, {"nivel": "MESTRADO", "ano_inicial": "", "ano_final": ""}
        )

        ordem = [turma["turma"] for turma in response.context["turmas_grafico"]]
        self.assertTrue({"2097.1", "2098.1", "2099.1"} <= set(ordem))
        self.assertNotContains(response, "inválido")

    def test_ano_invalido_mostra_a_mensagem_e_traz_todas_as_turmas(self):
        self._turmas_dos_anos()
        self.client.force_login(self.perfis["membro_secretaria"])

        response = self.client.get(self.url, {"nivel": "MESTRADO", "ano_inicial": "25"})

        self.assertContains(response, "Ano inicial inválido. Utilize o formato YYYY.")
        ordem = [turma["turma"] for turma in response.context["turmas_grafico"]]
        self.assertIn("2097.1", ordem)
        self.assertContains(response, 'value="25"', html=False)

    def test_ano_inicial_maior_que_o_final_mostra_a_mensagem(self):
        self._turmas_dos_anos()
        self.client.force_login(self.perfis["membro_secretaria"])

        response = self.client.get(
            self.url, {"nivel": "MESTRADO", "ano_inicial": "2099", "ano_final": "2097"}
        )

        self.assertContains(response, "O ano inicial não pode ser maior que o ano final.")
        ordem = [turma["turma"] for turma in response.context["turmas_grafico"]]
        self.assertIn("2098.1", ordem)
        