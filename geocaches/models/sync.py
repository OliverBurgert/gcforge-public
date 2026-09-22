from django.db import models


class SyncState(models.Model):
    """Tracks per-cache sync metadata for each platform."""
    geocache = models.ForeignKey("geocaches.Geocache", on_delete=models.CASCADE, related_name="sync_states")
    platform = models.CharField(max_length=20)
    last_synced = models.DateTimeField()
    last_modified = models.DateTimeField(null=True, blank=True)
    sync_mode = models.CharField(max_length=10, blank=True)  # light / full
    sync_error = models.CharField(max_length=500, blank=True)

    class Meta:
        unique_together = ("geocache", "platform")

    def __str__(self):
        return f"{self.geocache.gc_code or self.geocache.al_code or self.geocache.oc_code} @ {self.platform}"


class SyncQuota(models.Model):
    """Daily API quota tracking per platform and sync mode."""
    platform = models.CharField(max_length=20)
    mode = models.CharField(max_length=10)        # light / full
    date = models.DateField()
    used = models.IntegerField(default=0)
    limit = models.IntegerField()

    class Meta:
        unique_together = ("platform", "mode", "date")

    def __str__(self):
        return f"{self.platform}/{self.mode} {self.date}: {self.used}/{self.limit}"
