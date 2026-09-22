from django.db import models


class CorrectedCoordinates(models.Model):
    geocache = models.OneToOneField(
        "geocaches.Geocache", on_delete=models.CASCADE, related_name="corrected_coordinates"
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    note = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Corrected coords for {self.geocache}"
