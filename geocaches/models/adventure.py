from django.db import models


class Adventure(models.Model):
    """Parent record for an Adventure Lab — groups all its stages."""
    code = models.CharField(max_length=20, unique=True, db_index=True)   # LC{base}, e.g. LC28NG
    adventure_guid = models.CharField(max_length=36, blank=True, db_index=True)  # UUID from AL API / lab2gpx

    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    owner = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    url = models.URLField(max_length=500, blank=True)
    themes = models.JSONField(default=list, blank=True)
    stage_count = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, blank=True)

    # Extended fields from AL API
    key_image_url = models.URLField(max_length=500, blank=True)
    adventure_type = models.CharField(max_length=50, blank=True)
    smart_link = models.URLField(max_length=500, blank=True)
    published_utc = models.DateTimeField(null=True, blank=True)
    median_time_to_complete = models.PositiveSmallIntegerField(null=True, blank=True)  # minutes

    ratings_average = models.FloatField(null=True, blank=True)
    ratings_total_count = models.PositiveIntegerField(null=True, blank=True)
    is_highly_recommended = models.BooleanField(null=True, blank=True)
    completion_date = models.DateTimeField(null=True, blank=True)
    owner_public_guid = models.CharField(max_length=36, blank=True)

    imported_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} — {self.title or '(untitled)'}"


class ALStageDetail(models.Model):
    """Stage-level AL metadata — extends Geocache without polluting the core model."""

    geocache = models.OneToOneField(
        "geocaches.Geocache",
        on_delete=models.CASCADE,
        related_name="al_detail",
    )
    question_text = models.TextField(blank=True)
    answer_hash = models.CharField(max_length=64, blank=True)   # findCodeHashBase16v2
    answer_choices = models.JSONField(default=list, blank=True)  # for SingleChoice / MultipleChoice
    key_image_url = models.URLField(max_length=500, blank=True)
    geofencing_radius = models.IntegerField(null=True, blank=True)
    challenge_type = models.CharField(max_length=50, blank=True)
    is_final = models.BooleanField(null=True, blank=True)

    stage_number = models.PositiveSmallIntegerField(null=True, blank=True)
    al_stage_uuid = models.CharField(max_length=36, blank=True, db_index=True)
    answer_code_hashes = models.JSONField(default=list, blank=True)

    # User answer storage (like passphrase on OCExtension)
    user_answer = models.TextField(blank=True)
    answer_is_correct = models.BooleanField(null=True, blank=True)  # None = unchecked

    updated_at = models.DateTimeField(auto_now=True)

    def verify_answer(self, answer: str, user_public_guid: str = "") -> bool:
        """Verify answer using MD5(userPublicGuid + normalised_answer).

        Algorithm from Adventure Lab app v1.2.15+:
          normalised = answer with all whitespace removed, lowercased
          hash = md5(userPublicGuid + normalised)
        """
        import hashlib
        import re
        normalised = re.sub(r"\s", "", answer).lower()
        candidate = hashlib.md5((user_public_guid + normalised).encode()).hexdigest()
        all_hashes = {h.lower() for h in (self.answer_code_hashes or []) if h}
        if self.answer_hash:
            all_hashes.add(self.answer_hash.lower())
        self.answer_is_correct = candidate in all_hashes if all_hashes else False
        return self.answer_is_correct

    def __str__(self):
        return f"AL detail for geocache {self.geocache_id}"


class ALJournalEntry(models.Model):
    """Stores the personal journal entry for a completed AL stage.

    Kept separate from Geocache so journal fetching can be made optional
    (the data is personal and not relevant for cache-hunting purposes).
    """
    geocache = models.OneToOneField(
        "geocaches.Geocache",
        on_delete=models.CASCADE,
        related_name="al_journal",
    )
    journal_message = models.TextField(blank=True)
    journal_image_url = models.URLField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Journal for {self.geocache_id}"
