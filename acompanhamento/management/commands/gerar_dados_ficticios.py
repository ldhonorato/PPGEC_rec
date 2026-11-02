"""Comando que popula o banco com dados fictícios para desenvolvimento.

Usuários criados aqui têm e-mail @ficticio.invalid e polos começam com
"Fictício". A remoção usa essas marcas, então nada real é apagado.
"""

import random
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from acompanhamento.models import MarcoCalendario
from acompanhamento.situacao import somar_meses
from processos.models import (
    Aluno,
    ApresentacaoQualificacao,
    DisciplinaTrajetoria,
    Docente,
    Polo,
    Processo,
    ProrrogacaoTrajetoria,
    Setor,
    TrajetoriaAcademica,
    TrancamentoTrajetoria,
    User,
)

DOMINIO = "@ficticio.invalid"
PREFIXO_POLO = "Fictício"
NOMES_POLOS = ["Fictício Recife", "Fictício Caruaru"]

PRENOMES = [
    "Ana", "Bruno", "Carla", "Diego", "Elisa", "Fábio", "Gabriela", "Heitor",
    "Isabela", "João", "Larissa", "Marcos", "Natália", "Otávio", "Paula",
    "Rafael", "Sofia", "Tiago", "Vitória", "Wagner",
]
SOBRENOMES = [
    "Almeida", "Barros", "Cavalcanti", "Duarte", "Esteves", "Ferraz",
    "Gusmão", "Holanda", "Lins", "Moura", "Nogueira", "Pessoa", "Queiroz",
    "Rêgo", "Siqueira", "Tavares",
]

STATUS_ABERTOS = [
    Processo.StatusProcesso.EM_ANALISE,
    Processo.StatusProcesso.AGUARDANDO_DOCUMENTO,
    Processo.StatusProcesso.AGUARDANDO_CIENCIA,
    Processo.StatusProcesso.EM_DEBATE,
]

# Quantos dias para trás os processos podem ter sido abertos (um pouco
# mais de seis meses, que é o mínimo pedido).
JANELA_DIAS = 200


# Alunos das turmas antigas: (nível, meses desde o ingresso, desfecho,
# meses de prorrogação). Concluído precisa de pelo menos 14 meses no
# mestrado e 26 no doutorado, senão a data da defesa é recusada.
TURMAS_DO_PAINEL = [
    ("MESTRADO", 6, "ativo", 0),
    ("MESTRADO", 20, "ativo", 3),
    ("MESTRADO", 30, "concluido", 3),
    ("MESTRADO", 42, "concluido", 0),
    ("MESTRADO", 42, "desligado", 0),
    ("MESTRADO", 54, "concluido", 6),
    ("MESTRADO", 54, "trancado", 0),
    ("MESTRADO", 66, "concluido", 0),
    ("MESTRADO", 66, "desligado", 0),
    ("DOUTORADO", 6, "ativo", 0),
    ("DOUTORADO", 18, "desligado", 0),
    ("DOUTORADO", 30, "trancado", 0),
    ("DOUTORADO", 40, "concluido", 6),
    ("DOUTORADO", 40, "ativo", 6),
    ("DOUTORADO", 52, "concluido", 0),
    ("DOUTORADO", 62, "concluido", 0),
    ("DOUTORADO", 62, "desligado", 0),
    ("DOUTORADO", 74, "concluido", 12),
]

STATUS_DO_DESFECHO = {
    "ativo": TrajetoriaAcademica.Status.ATIVA,
    "concluido": TrajetoriaAcademica.Status.ATIVA,  # nova_trajetoria conclui
    "desligado": TrajetoriaAcademica.Status.DESLIGADA,
    "trancado": TrajetoriaAcademica.Status.TRANCADA,
}


class Command(BaseCommand):
    help = "Remove os dados fictícios anteriores e cria um conjunto novo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--semente", type=int, default=42,
            help="Mesma semente gera sempre os mesmos dados (padrão: 42).",
        )
        parser.add_argument(
            "--data-referencia", type=date.fromisoformat, default=None,
            help="Data usada como 'hoje', no formato AAAA-MM-DD (padrão: hoje).",
        )
        parser.add_argument(
            "--apenas-remover", action="store_true",
            help="Só remove os dados fictícios, sem criar novos.",
        )

    def handle(self, *args, **opcoes):
        # Só roda em desenvolvimento. Checado antes de mexer no banco.
        if not settings.DEBUG:
            raise CommandError(
                "Recusado: o modo de desenvolvimento está desligado (DEBUG=False)."
            )

        self.rng = random.Random(opcoes["semente"])
        self.referencia = opcoes["data_referencia"] or timezone.localdate()
        self.nomes_usados = set()

        # Se der erro no meio, desfaz tudo.
        with transaction.atomic():
            removidos = self.remover()
            if opcoes["apenas_remover"]:
                resumo = {}
            else:
                resumo = self.criar()

        self.imprimir_resumo(removidos, resumo)

    def remover(self):
        # Os processos vão primeiro porque o Processo tem PROTECT no usuário.
        processos = Processo.objects.filter(
            Q(usuario_criado_por__email__endswith=DOMINIO)
            | Q(aluno_interessado__email__endswith=DOMINIO)
        )
        total_processos = processos.count()
        processos.delete()

        # Trajetórias antes dos usuários, porque apresentação, prorrogação
        # e trancamento têm PROTECT em quem registrou.
        trajetorias = TrajetoriaAcademica.objects.filter(aluno__email__endswith=DOMINIO)
        total_trajetorias = trajetorias.count()
        trajetorias.delete()

        # Datas do calendário criadas aqui ficam marcadas com a secretaria
        # fictícia. Saem antes dela, senão perdem a marca (SET_NULL).
        MarcoCalendario.objects.filter(alterado_por__email__endswith=DOMINIO).delete()

        usuarios = User.objects.filter(email__endswith=DOMINIO)
        total_usuarios = usuarios.count()
        usuarios.delete()

        polos = Polo.objects.filter(nome__startswith=PREFIXO_POLO)
        total_polos = polos.count()
        polos.delete()

        return {
            "Processos": total_processos,
            "Trajetórias": total_trajetorias,
            "Usuários": total_usuarios,
            "Polos": total_polos,
        }

    def criar(self):
        resumo = {}

        for nome in NOMES_POLOS:
            Polo.objects.create(nome=nome, descricao="Polo de dados fictícios")
        resumo["Polos fictícios"] = len(NOMES_POLOS)

        resumo.update(self.criar_docentes())

        alunos = self.criar_alunos(quantidade=8)
        resumo["Alunos"] = len(alunos)

        resumo.update(self.criar_processos(alunos))
        resumo["Alunos com trajetória"] = self.criar_alunos_com_trajetoria()
        return resumo

    def criar_docentes(self):
        # Um docente para cada combinação (permanente/colaborador e
        # interno/externo) em cada polo ativo. O None é o grupo sem polo.
        contagem = {}
        polos = list(Polo.objects.filter(ativo=True)) + [None]

        for polo in polos:
            for permanente in (True, False):
                for externo in (False, True):
                    docente = Docente(
                        nome=self.nome_aleatorio(),
                        permanente=permanente,
                        externo=externo,
                        polo_atuacao=polo,
                    )
                    docente.email = self.email_para(docente.nome)
                    docente.set_unusable_password()
                    docente.save()

                    categoria = "permanentes" if permanente else "colaboradores"
                    vinculo = "externos" if externo else "internos"
                    chave = f"Docentes {categoria} {vinculo}"
                    contagem[chave] = contagem.get(chave, 0) + 1

        contagem["Docentes sem polo (já incluídos acima)"] = 4
        return contagem

    def criar_alunos(self, quantidade):
        alunos = []
        for _ in range(quantidade):
            aluno = Aluno(nome=self.nome_aleatorio())
            aluno.email = self.email_para(aluno.nome)
            aluno.set_unusable_password()
            aluno.save()
            alunos.append(aluno)
        return alunos

    def criar_processos(self, alunos):
        # Um processo aberto e um finalizado de cada tipo.
        setor = Setor.objects.filter(nome=Setor.NOME_SECRETARIA).first()
        if setor is None:
            raise CommandError(
                f"Setor '{Setor.NOME_SECRETARIA}' não encontrado. Rode o migrate antes."
            )

        pedidos = []
        for tipo in Processo.TipoProcesso:
            pedidos.append((tipo, False))
            pedidos.append((tipo, True))

        # Datas igualmente espaçadas na janela (assim cobre os seis meses
        # com certeza) e depois embaralhadas.
        dias_atras = [
            round(i * JANELA_DIAS / (len(pedidos) - 1)) for i in range(len(pedidos))
        ]
        self.rng.shuffle(dias_atras)

        abertos = 0
        finalizados = 0
        for (tipo, finalizado), dias in zip(pedidos, dias_atras):
            aluno = self.rng.choice(alunos)
            criado_em = self.referencia - timedelta(days=dias)
            prazo = criado_em + timedelta(days=Processo.prazo_dias_para_tipo(tipo))

            processo = Processo(
                usuario_criado_por=aluno,
                aluno_interessado=aluno,
                tipo=tipo,
                assunto=f"{tipo.label} (fictício)",
                descricao="Processo gerado pelo comando de dados fictícios.",
                setor_atual=setor,
                prazo_limite=prazo,
            )
            if finalizado:
                processo.status = Processo.StatusProcesso.FINALIZADO
                fim = min(prazo, self.referencia)
                processo.finalizado_em = self.para_datetime(fim)
                processo.termo_finalizacao = "Finalizado nos dados fictícios."
                finalizados += 1
            else:
                processo.status = self.rng.choice(STATUS_ABERTOS)
                abertos += 1
            processo.save()

            # data_criacao é auto_now_add, então o save ignora a data que a
            # gente quer. O update() grava direto no banco.
            Processo.objects.filter(pk=processo.pk).update(
                data_criacao=self.para_datetime(criado_em)
            )

        return {"Processos abertos": abertos, "Processos finalizados": finalizados}

    def criar_alunos_com_trajetoria(self):
        # Um aluno em cada situação de prazo, contando a partir da data de
        # referência. Os números são meses desde o ingresso e dias até a
        # data limite do projeto (negativo = já passou).
        self.secretaria = User(nome="Secretaria Fictícia", email="secretaria" + DOMINIO)
        self.secretaria.set_unusable_password()
        self.secretaria.save()

        # Mestrado
        self.nova_trajetoria("MESTRADO", ingresso=12, limite_projeto=60)  # tudo em dia
        self.nova_trajetoria("MESTRADO", ingresso=14, limite_projeto=-30)  # projeto atrasado, com chance
        self.nova_trajetoria("MESTRADO", ingresso=18, limite_projeto=-120)  # projeto atrasado, sem chance
        self.nova_trajetoria("MESTRADO", ingresso=None)  # sem datas cadastradas
        self.nova_trajetoria("MESTRADO", ingresso=30, limite_projeto=-300)  # projeto e defesa vencidos
        self.nova_trajetoria("MESTRADO", ingresso=26, defendeu=True)  # já defendeu

        # Projeto aprovado antes da data limite
        trajetoria = self.nova_trajetoria("MESTRADO", ingresso=14, limite_projeto=-30)
        self.apresentar(trajetoria, dias_atras=40, conceito="A")

        # Caso 7: primeira apresentação depois da data limite, com conceito C.
        # Ela já era a reapresentação, então não sobra chance.
        trajetoria = self.nova_trajetoria("MESTRADO", ingresso=15, limite_projeto=-60)
        self.apresentar(trajetoria, dias_atras=20, conceito="C")

        # Defesa venceria, mas a prorrogação e o trancamento estendem o prazo
        trajetoria = self.nova_trajetoria("MESTRADO", ingresso=25, limite_projeto=-30)
        ProrrogacaoTrajetoria.objects.create(
            trajetoria=trajetoria, meses=3, registrado_por=self.secretaria
        )
        inicio = somar_meses(self.referencia, -6)
        TrancamentoTrajetoria.objects.create(
            trajetoria=trajetoria,
            data_inicio=inicio,
            data_fim=inicio + timedelta(days=29),
            registrado_por=self.secretaria,
        )

        # Trancado e desligado: estariam atrasados, mas não entram na lista
        self.nova_trajetoria(
            "MESTRADO", ingresso=18, limite_projeto=-120,
            status=TrajetoriaAcademica.Status.TRANCADA,
        )
        self.nova_trajetoria(
            "MESTRADO", ingresso=18, limite_projeto=-120,
            status=TrajetoriaAcademica.Status.DESLIGADA,
        )

        # Doutorado: qualificados, um em dia e um com a defesa vencida
        trajetoria = self.nova_trajetoria("DOUTORADO", ingresso=30)
        self.apresentar(trajetoria, dias_atras=60, conceito="A")
        trajetoria = self.nova_trajetoria("DOUTORADO", ingresso=50)
        self.apresentar(trajetoria, dias_atras=400, conceito="A")

        # Doutorado com a qualificação atrasada, ainda com chance. A data
        # limite vem do calendário, no quinto semestre (dois anos após o
        # ingresso).
        limite = self.referencia - timedelta(days=30)
        self.data_no_calendario(limite)
        self.nova_trajetoria("DOUTORADO", data_ingresso=somar_meses(limite, -24))

        # Turmas de anos diferentes para o painel de sucesso por turma. Vêm
        # depois dos 14 de cima, sem mudar a ordem deles.
        for nivel, meses, desfecho, prorrogacao in TURMAS_DO_PAINEL:
            trajetoria = self.nova_trajetoria(
                nivel,
                ingresso=meses,
                defendeu=desfecho == "concluido",
                status=STATUS_DO_DESFECHO[desfecho],
            )
            self.prorrogar(trajetoria, prorrogacao)

        return TrajetoriaAcademica.objects.filter(aluno__email__endswith=DOMINIO).count()

    def prorrogar(self, trajetoria, meses):
        # Cada concessão tem no máximo 3 meses no mestrado e 6 no doutorado.
        por_vez = 3 if trajetoria.nivel_curso == "MESTRADO" else 6
        while meses > 0:
            parte = min(meses, por_vez)
            ProrrogacaoTrajetoria.objects.create(
                trajetoria=trajetoria, meses=parte, registrado_por=self.secretaria
            )
            meses -= parte

    def nova_trajetoria(
        self,
        nivel,
        ingresso=None,
        limite_projeto=None,
        defendeu=False,
        status=TrajetoriaAcademica.Status.ATIVA,
        data_ingresso=None,
    ):
        aluno = Aluno(nome=self.nome_aleatorio())
        aluno.email = self.email_para(aluno.nome)
        if defendeu:
            aluno.status_aluno = Aluno.StatusAluno.DEFENDEU
        if status == TrajetoriaAcademica.Status.DESLIGADA:
            aluno.status_aluno = Aluno.StatusAluno.DESLIGADO
        aluno.set_unusable_password()
        aluno.save()

        if ingresso is not None:
            data_ingresso = somar_meses(self.referencia, -ingresso)

        trajetoria = TrajetoriaAcademica(aluno=aluno, nivel_curso=nivel, status=status)
        if data_ingresso is None:
            trajetoria.ingresso = MarcoCalendario.semestre_da_data(self.referencia)
        else:
            trajetoria.data_ingresso = data_ingresso
            trajetoria.ingresso = MarcoCalendario.semestre_da_data(data_ingresso)

        if limite_projeto is not None:
            limite = self.referencia + timedelta(days=limite_projeto)
            trajetoria.data_limite_qualificacao = limite
            trajetoria.prazo_qualificacao = MarcoCalendario.semestre_da_data(limite)

        if defendeu:
            trajetoria.status = TrajetoriaAcademica.Status.CONCLUIDA
            trajetoria.isQualificado = True
            trajetoria.numero_defesa = "Defesa fictícia"
            trajetoria.data_defesa = self.referencia - timedelta(days=30)

        trajetoria.save()
        return trajetoria

    def apresentar(self, trajetoria, dias_atras, conceito):
        # No mestrado, o projeto só pode ser apresentado com 12 créditos.
        if trajetoria.nivel_curso == "MESTRADO":
            DisciplinaTrajetoria.objects.create(
                trajetoria=trajetoria,
                nome="Disciplina fictícia",
                creditos=12,
                situacao=DisciplinaTrajetoria.Situacao.APROVADA,
            )
        ApresentacaoQualificacao.objects.create(
            trajetoria=trajetoria,
            data_apresentacao=self.referencia - timedelta(days=dias_atras),
            conceito=conceito,
            registrado_por=self.secretaria,
        )

    def data_no_calendario(self, data):
        # Só cria se o semestre ainda não tiver data, para não mexer em
        # datas que já estão no banco.
        semestre = MarcoCalendario.semestre_da_data(data)
        tipo = MarcoCalendario.TipoMarco.QUALIFICACAO
        if not MarcoCalendario.objects.filter(semestre=semestre, tipo=tipo).exists():
            MarcoCalendario.objects.create(
                semestre=semestre,
                tipo=tipo,
                data_limite=data,
                alterado_por=self.secretaria,
            )

    def nome_aleatorio(self):
        # Sorteia até achar um nome que ainda não saiu nesta execução.
        while True:
            nome = (
                f"{self.rng.choice(PRENOMES)} "
                f"{self.rng.choice(SOBRENOMES)} {self.rng.choice(SOBRENOMES)}"
            )
            if nome not in self.nomes_usados:
                self.nomes_usados.add(nome)
                return nome

    def email_para(self, nome):
        # "Fábio Lins Rêgo" -> fabio.lins.rego@ficticio.invalid
        sem_acento = nome.lower().translate(str.maketrans("áâãéêíóôõúç", "aaaeeiooouc"))
        return sem_acento.replace(" ", ".") + DOMINIO

    def para_datetime(self, dia):
        return timezone.make_aware(datetime.combine(dia, time(9, 0)))

    def imprimir_resumo(self, removidos, resumo):
        self.stdout.write("Removidos:")
        for categoria, total in removidos.items():
            self.stdout.write(f"  {categoria}: {total}")

        if resumo:
            self.stdout.write(self.style.SUCCESS("Criados:"))
            for categoria, total in resumo.items():
                self.stdout.write(f"  {categoria}: {total}")
