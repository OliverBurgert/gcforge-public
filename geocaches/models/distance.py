from django.db import models


class DistanceCache(models.Model):
    """Pre-computed distance and bearing from a reference point to a geocache.

    Eliminates the need for per-row Python haversine callbacks in SQLite.
    Recomputed in bulk (~1 s for 50 k caches) when caches are imported or
    the reference point changes.
    """
    geocache = models.ForeignKey("geocaches.Geocache", on_delete=models.CASCADE)
    ref_point = models.ForeignKey(
        "preferences.ReferencePoint", on_delete=models.CASCADE,
    )
    distance_km = models.FloatField()
    bearing_deg = models.FloatField()

    class Meta:
        unique_together = ("geocache", "ref_point")
        indexes = [
            models.Index(fields=["ref_point", "distance_km"]),
        ]

    def __str__(self):
        return f"{self.geocache} → {self.ref_point}: {self.distance_km:.1f} km"
