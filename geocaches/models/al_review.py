from django.db import models


class ALReview(models.Model):
    adventure = models.ForeignKey(
        "geocaches.Adventure", on_delete=models.CASCADE, related_name="al_reviews"
    )
    review_id = models.IntegerField(db_index=True)
    player_username = models.CharField(max_length=255, blank=True)
    player_public_guid = models.CharField(max_length=36, blank=True)
    player_avatar_url = models.TextField(blank=True)
    player_geocache_find_count = models.PositiveIntegerField(null=True, blank=True)
    player_completed_adventure_count = models.PositiveIntegerField(null=True, blank=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)  # 1–5
    review_text = models.TextField(blank=True)
    recommended = models.BooleanField(default=False)
    is_creator = models.BooleanField(default=False)
    adventure_completed_utc = models.DateTimeField(null=True, blank=True)
    created_utc = models.DateTimeField()
    modified_utc = models.DateTimeField(null=True, blank=True)
    images = models.JSONField(default=list, blank=True)  # list of {id, url}

    class Meta:
        ordering = ["-created_utc"]
        unique_together = [("adventure", "review_id")]

    def __str__(self):
        return f"Review {self.review_id} by {self.player_username}"
