"""Armazenamento de arquivos estaticos."""

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
