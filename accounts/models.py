from django.db import models


class UserAccount(models.Model):
    """
    A geocaching identity on a specific platform.

    Multiple accounts are supported (e.g. two GC accounts, one OC account).
    "Mine" detection uses ALL accounts for the matching platform — is_default
    only controls which account is pre-selected in per-account filters.
    """

    PLATFORM_GC = "gc"
    PLATFORM_OC_DE = "oc_de"
    PLATFORM_CHOICES = [
        ("gc",    "geocaching.com"),
        ("oc_de", "opencaching.de"),
        ("oc_pl", "opencaching.pl"),
        ("oc_uk", "opencaching.uk"),
        ("oc_nl", "opencaching.nl"),
        ("oc_us", "opencaching.us"),
    ]

    platform   = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    user_id    = models.CharField(max_length=50, blank=True,
                                  help_text="Stable platform-assigned numeric/UUID identifier (preferred for ownership matching)")
    username   = models.CharField(max_length=100,
                                  help_text="Login name at time of entry — informational, may change")
    label      = models.CharField(max_length=100, blank=True,
                                  help_text="Display label; defaults to username@platform if empty")
    profile_url = models.URLField(blank=True)
    is_default = models.BooleanField(default=False,
                                     help_text="Pre-selected in per-account filters; does not restrict ownership detection")
    notes      = models.CharField(max_length=255, blank=True)

    # OKAPI credentials (OC platforms only)
    consumer_key       = models.CharField(max_length=200, blank=True,
                                          help_text="OKAPI consumer key (public, register at <node>/okapi/signup.html)")
    membership_level = models.IntegerField(
        default=0,
        help_text="GC membership level: 0=Unknown, 1=Basic, 2=Charter, 3=Premium"
    )

    class Meta:
        ordering = ["platform", "username"]
        unique_together = [("platform", "user_id")]

    def get_label(self) -> str:
        return self.label or f"{self.username}@{self.get_platform_display()}"

    def __str__(self) -> str:
        return self.get_label()
