from django.apps import AppConfig


class GeocachesConfig(AppConfig):
    name = 'geocaches'

    def ready(self):
        import geocaches.signals  # noqa: F401
