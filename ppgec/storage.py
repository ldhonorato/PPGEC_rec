"""Armazenamento de arquivos estaticos e de documentos enviados."""

from django.core.exceptions import ImproperlyConfigured
from whitenoise.storage import CompressedManifestStaticFilesStorage


class EstaticosComHash(CompressedManifestStaticFilesStorage):
    """Nomes com hash do conteudo, sem exigir collectstatic para renderizar.

    Por que existe: o comportamento padrao levanta ValueError quando o arquivo
    nao esta no manifesto, e a excecao derruba qualquer tela que use
    {% static %}. Isso acontece sempre que o manifesto ainda nao foi gerado --
    clone recem-feito, suite de testes (que roda com DEBUG=False) e CI antes do
    collectstatic. Sem este ajuste, `manage.py test` quebra em toda tela.

    manifest_strict = False sozinho nao basta: sem a entrada no manifesto o
    Django tenta calcular o hash lendo o arquivo em STATIC_ROOT, que tambem nao
    existe nesse cenario, e estoura de novo. Por isso stored_name tambem cai
    para o caminho sem hash.

    Em producao nada disso e acionado: o entrypoint.sh roda collectstatic antes
    de subir o Gunicorn, entao o manifesto existe e todos os caminhos saem com
    hash -- que e o ponto, para o cache do navegador nunca ficar desatualizado
    apos um deploy.
    """

    manifest_strict = False

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:
            # Sem manifesto e sem o arquivo em STATIC_ROOT. Serve o caminho
            # direto: perde-se o cache-busting, a pagina continua de pe.
            return name


def configuracao_s3(*, bucket, regiao, chave, segredo, expiracao_da_url):
    """Monta a entrada de STORAGES para guardar os documentos num bucket S3.

    Vive aqui, e nao no settings, para poder ser exercitada com valores
    explicitos: as opcoes abaixo sao decisoes de seguranca, e uma delas mudar
    sem que ninguem note e o tipo de coisa que so aparece em producao.

    O bucket e privado. Toda URL sai assinada e de vida curta, emitida pela
    aplicacao depois de conferir quem esta pedindo -- e o que mantem a regra de
    sigilo valendo tambem do lado do S3.
    """
    if not (chave and segredo):
        # Falhar na subida e melhor do que subir e so descobrir no primeiro
        # upload, com o usuario do outro lado.
        raise ImproperlyConfigured(
            "AWS_STORAGE_BUCKET_NAME foi definido, mas AWS_ACCESS_KEY_ID e/ou "
            "AWS_SECRET_ACCESS_KEY estao vazios. Sem credencial nao ha como "
            "gravar nem ler no bucket."
        )

    return {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": bucket,
            "region_name": regiao,
            "access_key": chave,
            "secret_key": segredo,
            # Sem assinatura nao ha leitura: e o que sustenta o bucket privado.
            "querystring_auth": True,
            "querystring_expire": expiracao_da_url,
            # Buckets novos vem com ACL desabilitada; mandar ACL faz a chamada
            # ser recusada pela AWS.
            "default_acl": None,
            # Sem isso, dois envios com o mesmo nome de arquivo -- comum em
            # "declaracao.pdf" -- sobrescrevem um ao outro em silencio. Com
            # False, o Django acrescenta sufixo e os dois coexistem.
            "file_overwrite": False,
            "signature_version": "s3v4",
        },
    }
