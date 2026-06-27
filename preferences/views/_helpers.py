from django.shortcuts import redirect
from django.urls import reverse


def _redirect_tab(tab: str):
    # Send both ?tab=…  (server-side, renders the right pane on first paint —
    # immune to a stale cached settings.js) and #fragment for the JS handler
    # so browser-back/forward and the in-page tab switcher stay consistent.
    return redirect(reverse("preferences:settings") + f"?tab={tab}#{tab}")


def _pop_msg():
    return None  # placeholder; per-action save endpoints push messages via session in Phase B+
