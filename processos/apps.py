from django.apps import AppConfig


class ProcessosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'processos'

    def ready(self):
        """
        Este método roda uma única vez quando o Django inicia.
        Importamos os signals aqui para que eles fiquem 'escutando' o sistema.
        """
        import processos.signals
