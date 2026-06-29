from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import TrajetoriaAcademica, EstagioDocencia 

@receiver(post_save, sender=TrajetoriaAcademica)
def criar_estagio_nao_iniciado(sender, instance, created, **kwargs):
    """Gera o estágio de docência automaticamente ao criar a trajetória."""
    if created:
        EstagioDocencia.objects.create(
            trajetoria=instance,
            status=EstagioDocencia.Status.NAO_INICIADO
        )