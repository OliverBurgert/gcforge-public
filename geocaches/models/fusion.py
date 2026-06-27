from django.db import models


class CacheFusionRecord(models.Model):
    """Tracks the relationship between GC and OC records for the same physical cache."""

    DECISION_FUSE = "fuse"
    DECISION_DONT_FUSE = "dont_fuse"
    DECISION_POSTPONE = "postpone"
    DECISION_CHOICES = [
        (DECISION_FUSE, "Fuse"),
        (DECISION_DONT_FUSE, "Don't fuse"),
        (DECISION_POSTPONE, "Postpone"),
    ]

    gc_code = models.CharField(max_length=20, db_index=True)
    oc_code = models.CharField(max_length=20, db_index=True)
    # True if the OC platform's own data references this GC code (owner-confirmed link).
    # False if the match was found by coordinate proximity only.
    auto_linked = models.BooleanField(default=False)
    user_decision = models.CharField(
        max_length=20, choices=DECISION_CHOICES, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("gc_code", "oc_code")]

    def __str__(self):
        decision = self.user_decision or "undecided"
        link = "auto" if self.auto_linked else "proximity"
        return f"{self.gc_code}/{self.oc_code} ({link}, {decision})"
