"""
Config file for Celery, this file sets up the Celery application, 
configures it to use Django settings, and enables automatic discovery
of tasks defined in the Django app.
"""

import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ppgec.settings")

app = Celery("ppgec")

# reads the celery config from settings.py with the CELERY_ prefix
app.config_from_object("django.conf:settings", namespace="CELERY")

# discover the tasks in the Django app
app.autodiscover_tasks()
