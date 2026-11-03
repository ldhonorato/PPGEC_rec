"""Mostra o e-mail do aviso do dia sem gravar nada e sem enviar.

Calcula os avisos como a rotina diária calcularia hoje, mas dentro de uma
transação que é desfeita no fim e sem chamar o envio de e-mail. Serve para
conferir o texto e os destinatários com a secretaria antes de ligar o aviso.

Uso:
    python manage.py mostrar_aviso_do_dia
    python manage.py mostrar_aviso_do_dia --data 2026-10-22
    python manage.py mostrar_aviso_do_dia --html email.html
"""

import argparse
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from acompanhamento.avisos import (
    atualizar_avisos,
    avisos_pendentes,
    destinatarios,
    montar_email,
)


def _data(texto):
    try:
        return date.fromisoformat(texto)
    except ValueError as erro:
        raise argparse.ArgumentTypeError(
            f"Data inválida: {texto!r}. Use o formato AAAA-MM-DD."
        ) from erro


def _dia(data):
    return f"{data:%d/%m/%Y}" if data else "-"


class Command(BaseCommand):
    help = (
        "Mostra o e-mail do aviso de alunos atrasados do dia, sem gravar "
        "nada no banco e sem enviar e-mail."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--data",
            type=_data,
            help="Data de referência (AAAA-MM-DD). Padrão: hoje.",
        )
        parser.add_argument(
            "--html",
            metavar="ARQUIVO",
            help="Grava o corpo do e-mail neste arquivo HTML, para abrir no navegador.",
        )

    def handle(self, *args, **opcoes):
        data = opcoes["data"] or timezone.localdate()

        # Tudo o que a rotina grava é desfeito no fim do bloco. As linhas são
        # montadas aqui dentro, enquanto os avisos ainda existem no banco.
        with transaction.atomic():
            novos = {aviso.pk for aviso in atualizar_avisos(data)}
            avisos = avisos_pendentes()
            emails = destinatarios()
            assunto, html = montar_email(avisos) if avisos else ("", "")
            linhas = [
                (
                    f"- {aviso.trajetoria.aluno.nome} | "
                    f"{aviso.trajetoria.get_nivel_curso_display()} | "
                    f"{aviso.get_marco_display()} | "
                    f"{aviso.get_situacao_display()} | "
                    f"limite {_dia(aviso.data_limite)} | "
                    f"reapresentação até {_dia(aviso.prazo_reapresentacao)} | "
                    f"{aviso.motivo}"
                )
                for aviso in avisos
            ]
            quantos_novos = sum(1 for aviso in avisos if aviso.pk in novos)
            transaction.set_rollback(True)

        escrever = self.stdout.write
        escrever(
            f"Prévia do aviso de {data:%d/%m/%Y}. "
            "Nada foi gravado e nenhum e-mail foi enviado."
        )
        escrever("")

        if not avisos:
            escrever("Nenhum aviso para enviar: nenhum e-mail sairia hoje.")
            return

        if emails:
            escrever(f"Destinatários: {', '.join(emails)}")
        else:
            escrever(
                "Destinatários: NENHUM. Ninguém está na Secretaria PPGEC nem na "
                "Coordenação PPG, então os avisos ficariam pendentes."
            )
        escrever(f"Assunto: {assunto}")
        escrever(
            f"Avisos: {len(avisos)} ({quantos_novos} novo(s), "
            f"{len(avisos) - quantos_novos} pendente(s) de dias anteriores)"
        )
        escrever("")
        for linha in linhas:
            escrever(linha)

        if opcoes["html"]:
            caminho = Path(opcoes["html"])
            caminho.write_text(html, encoding="utf-8")
            escrever("")
            escrever(f"Corpo do e-mail gravado em {caminho}.")
        else:
            escrever("")
            escrever("Para ver o e-mail como a secretaria vai ver, use --html email.html.")
