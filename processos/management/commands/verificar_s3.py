"""Diz, em uma execucao, se o S3 esta ligado e o que ha de cada lado.

Existe porque o diagnostico estava espalhado: uma coisa e a variavel chegar ao
processo, outra e a credencial funcionar, outra e o arquivo estar no bucket, e
cada uma falha de um jeito diferente. Perguntar as tres separadamente custa uma
ida e volta a cada resposta.
"""

from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from processos.models import DeclaracaoDeVinculo, Documento, SolicitacaoAssinatura


CAMPOS_COM_ARQUIVO = (
    (Documento, "arquivo"),
    (SolicitacaoAssinatura, "documento_pdf"),
    (SolicitacaoAssinatura, "documento_assinado_pdf"),
    (DeclaracaoDeVinculo, "arquivo"),
)

CHAVE_DE_TESTE = "documentos/_verificacao-de-conexao.txt"


class Command(BaseCommand):
    help = "Mostra o estado do armazenamento: variaveis, conexao e onde estao os arquivos."

    def handle(self, *args, **opcoes):
        self.stdout.write(self.style.MIGRATE_HEADING("1. o que este processo enxerga"))
        self.stdout.write(f"  USA_S3                    {settings.USA_S3}")
        self.stdout.write(f"  AWS_STORAGE_BUCKET_NAME   {settings.AWS_STORAGE_BUCKET_NAME or '(vazio)'}")
        self.stdout.write(f"  MEDIA_ROOT                {settings.MEDIA_ROOT}")
        self.stdout.write(f"  armazenamento em uso      {settings.STORAGES['default']['BACKEND']}")

        if not settings.USA_S3:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("  O S3 nao esta ligado neste processo."))
            self.stdout.write(
                "  Se o .env do host tem AWS_STORAGE_BUCKET_NAME, o conteiner nasceu antes\n"
                "  da edicao: env_file e lido na criacao. Recrie com\n"
                "  'docker compose -f docker-compose-prod.yml up -d' -- 'restart' mantem\n"
                "  o ambiente antigo."
            )
            self._contar_arquivos(s3_ligado=False)
            return

        self.stdout.write(f"  regiao                    {settings.AWS_S3_REGION_NAME}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("2. a credencial funciona?"))
        try:
            nome = default_storage.save(CHAVE_DE_TESTE, ContentFile(b"verificacao"))
            self.stdout.write(self.style.SUCCESS(f"  gravar    OK  ({nome})"))
            endereco = default_storage.url(nome)
            self.stdout.write(f"  host      {endereco.split('/')[2]}")
            default_storage.delete(nome)
            self.stdout.write(self.style.SUCCESS("  apagar    OK"))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"  FALHOU: {type(exc).__name__}: {exc}"))
            self.stdout.write("  Confira a credencial, a politica IAM e o nome do bucket.")
            return

        self._contar_arquivos(s3_ligado=True)

    def _contar_arquivos(self, *, s3_ligado):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("3. onde estao os arquivos"))
        media_root = Path(settings.MEDIA_ROOT)
        total = no_bucket = no_disco = em_lugar_nenhum = 0

        for modelo, campo in CAMPOS_COM_ARQUIVO:
            registros = modelo.objects.exclude(**{campo: ""}).exclude(**{f"{campo}__isnull": True})
            for registro in registros.iterator():
                chave = getattr(registro, campo).name
                if not chave:
                    continue
                total += 1
                # O disco e sempre consultado direto, e nao pelo storage: com o
                # S3 ligado, o storage responde pelo bucket e nao saberia dizer
                # o que ficou para tras.
                esta_no_disco = (media_root / chave).is_file()
                esta_no_bucket = default_storage.exists(chave) if s3_ligado else False
                if esta_no_bucket:
                    no_bucket += 1
                elif esta_no_disco:
                    no_disco += 1
                else:
                    em_lugar_nenhum += 1

        self.stdout.write(f"  registros com arquivo     {total}")
        if s3_ligado:
            self.stdout.write(f"  ja no bucket              {no_bucket}")
        self.stdout.write(f"  so no disco               {no_disco}")
        self.stdout.write(f"  em lugar nenhum           {em_lugar_nenhum}")

        self.stdout.write("")
        if em_lugar_nenhum:
            self.stdout.write(self.style.WARNING(
                f"  {em_lugar_nenhum} registro(s) apontam para arquivo que nao existe em lugar nenhum.\n"
                "  Ja estavam quebrados antes da troca; migrar_arquivos_para_s3 os lista um a um."
            ))
        if s3_ligado and no_disco:
            self.stdout.write(self.style.WARNING(
                f"  {no_disco} arquivo(s) ainda no disco. Para leva-los ao bucket:\n"
                "    python manage.py migrar_arquivos_para_s3            (simula)\n"
                "    python manage.py migrar_arquivos_para_s3 --confirmar (grava)\n"
                "  Sem --confirmar nada e gravado -- e a causa mais comum de o bucket\n"
                "  continuar vazio depois de rodar o migrador."
            ))
        elif s3_ligado and not no_disco and total:
            self.stdout.write(self.style.SUCCESS("  Todos os arquivos conhecidos estao no bucket."))
        elif s3_ligado and not total:
            self.stdout.write("  Nenhum registro com arquivo no banco: nao ha o que migrar.")
