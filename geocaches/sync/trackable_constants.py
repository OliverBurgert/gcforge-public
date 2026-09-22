"""Non-secret GC trackable reference data.

Shared between the public trackable sync service and the private
``gcprivate.trackable_client``.
"""

# GC trackable log type IDs. Verified against /trackablelogtypes 2026-05-11.
# Names are exact strings the API uses (note the capitalised "To" in the move
# log types). These are the fallback when the endpoint isn't reachable.
DEFAULT_TRACKABLE_LOG_TYPE_IDS: dict[str, int] = {
    "Write note":                    4,
    "Retrieve It from a Cache":      13,
    "Dropped Off":                   14,
    "Transfer":                      15,
    "Mark Missing":                  16,
    "Grab It (Not from a Cache)":    19,
    "Discovered It":                 48,
    "Move To Collection":            69,
    "Move To Inventory":             70,
    "Visited":                       75,
}
