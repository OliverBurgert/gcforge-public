from django.db import models


class DistanceCache(models.Model):
    """Pre-computed distance and bearing from a reference point to a geocache.

    Eliminates the need for per-row Python haversine callbacks in SQLite.
    Maintained per cache on every coordinate write (geocaches.services.coords);
    gaps are filled by a background task, because a full rebuild costs 6-13 s
    for 70,919 rows.  See geocaches/geo/distance_cache.py.
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
