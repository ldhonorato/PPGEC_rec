"""Leva para o bucket os arquivos que ficaram no disco.

Roda com o S3 ja ligado: le cada registro do banco, procura o arquivo no
MEDIA_ROOT antigo e grava no bucket com a MESMA chave. Nao toca no banco.

Por que a chave nao muda -- e por que nao ha conflito de caminho a resolver:
o que o FileField guarda e um caminho relativo ("documentos/processos/x.pdf").
Em disco ele e lido a partir do MEDIA_ROOT; no S3 ele e a propria chave do
objeto, porque a configuracao nao acrescenta prefixo nenhum. Os dois lados usam
a mesma string, entao copiar o conteudo basta: nenhum registro precisa ser
reescrito.
"""

from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from processos.models import DeclaracaoDeVinculo, Documento, SolicitacaoAssinatura


# (modelo, campo) de tudo que guarda arquivo enviado. Espelha
# _regras_de_arquivo em views.py: o que nao esta aqui tambem nao e entregue.
CAMPOS_COM_ARQUIVO = (
    (Documento, "arquivo"),
    (SolicitacaoAssinatura, "documento_pdf"),
    (SolicitacaoAssinatura, "documento_assinado_pdf"),
    (DeclaracaoDeVinculo, "arquivo"),
)


class Command(BaseCommand):
    help = "Copia para o bucket S3 os arquivos que ainda estao no disco."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirmar",
            action="store_true",
            help="Grava de verdade. Sem isto, apenas relata o que faria.",
        )
        parser.add_argument(
            "--media-root",
            default=None,
            help=(
                "Pasta de onde ler os arquivos antigos. Padrao: MEDIA_ROOT. "
                "Use quando os arquivos foram copiados para outro lugar."
            ),
        )
        parser.add_argument(
            "--sobrescrever",
            action="store_true",
            help="Regrava mesmo se a chave ja existir no bucket.",
        )

    def handle(self, *args, **opcoes):
        if not settings.USA_S3:
            # A causa mais comum nao e a variavel faltar no .env, e sim o
            # conteiner ter nascido antes dela: env_file e lido na criacao, e
            # um conteiner em execucao mantem o ambiente com que subiu. Por isso
            # a mensagem cobra o "up -d" -- "restart" reinicia o processo com o
            # ambiente antigo e o erro se repete, sem pista do motivo.
            raise CommandError(
                "O S3 nao esta ligado (AWS_STORAGE_BUCKET_NAME vazio neste processo), "
                "e sem ele a copia teria origem e destino no mesmo lugar.\n\n"
                "  1. confira o .env do host:   grep AWS .env\n"
                "  2. confira o que o conteiner ve:   printenv | grep AWS\n"
                "  3. se o .env tem e o conteiner nao, recrie o conteiner:\n"
                "     docker compose -f docker-compose-prod.yml up -d\n\n"
                "Recriar e necessario: o env_file e lido quando o conteiner nasce, "
                "entao 'docker restart' mantem o ambiente antigo."
            )

        origem = Path(opcoes["media_root"] or settings.MEDIA_ROOT)
        if not origem.is_dir():
            raise CommandError(f"Pasta de origem nao encontrada: {origem}")

        confirmar = opcoes["confirmar"]
        sobrescrever = opcoes["sobrescrever"]

        self.stdout.write(f"origem : {origem}")
        self.stdout.write(f"bucket : {settings.AWS_STORAGE_BUCKET_NAME} ({settings.AWS_S3_REGION_NAME})")
        self.stdout.write("modo   : " + ("GRAVANDO" if confirmar else "simulacao (use --confirmar)"))
        self.stdout.write("")

        contagem = {"copiados": 0, "ja_no_bucket": 0, "sem_arquivo_local": 0, "vazios": 0, "erros": 0}
        pendencias = []

        for modelo, campo in CAMPOS_COM_ARQUIVO:
            rotulo = f"{modelo.__name__}.{campo}"
            registros = modelo.objects.exclude(**{campo: ""}).exclude(**{f"{campo}__isnull": True})
            self.stdout.write(self.style.MIGRATE_HEADING(f"{rotulo} — {registros.count()} registro(s)"))

            for registro in registros.iterator():
                chave = getattr(registro, campo).name
                if not chave:
                    contagem["vazios"] += 1
                    continue

                caminho_local = origem / chave
                if not caminho_local.is_file():
                    # Registro aponta para arquivo que nao esta no disco. Ja
                    # estava quebrado antes da migracao; a troca so torna isso
                    # visivel. Relatado, e nao corrigido: apagar registro de
                    # documento e decisao de quem cuida do acervo.
                    contagem["sem_arquivo_local"] += 1
                    pendencias.append(f"  sem arquivo no disco  {rotulo} #{registro.pk}  {chave}")
                    continue

                if not sobrescrever and default_storage.exists(chave):
                    contagem["ja_no_bucket"] += 1
                    continue

                if not confirmar:
                    contagem["copiados"] += 1
                    self.stdout.write(f"  copiaria  {chave}")
                    continue

                try:
                    with caminho_local.open("rb") as conteudo:
                        # _save, e nao save: save aplica get_available_name e
                        # renomearia o arquivo por causa de file_overwrite=False,
                        # quebrando a correspondencia com o que esta no banco.
                        default_storage._save(chave, conteudo)
                    contagem["copiados"] += 1
                    self.stdout.write(f"  copiado   {chave}")
                except Exception as exc:
                    contagem["erros"] += 1
                    pendencias.append(f"  ERRO ao copiar        {rotulo} #{registro.pk}  {chave}  ({exc})")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("resumo"))
        for chave, valor in contagem.items():
            self.stdout.write(f"  {chave.replace('_', ' '):22} {valor}")

        if pendencias:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("precisam de atencao:"))
            for linha in pendencias:
                self.stdout.write(linha)

        if not confirmar:
            self.stdout.write("")
            self.stdout.write("Nada foi gravado. Repita com --confirmar.")
        elif contagem["erros"]:
            raise CommandError(f"{contagem['erros']} arquivo(s) nao foram copiados.")
