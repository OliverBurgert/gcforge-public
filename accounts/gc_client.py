"""
Geocaching.com client — stub for public build.

GC API support is not included in this release.
"""


def test_credentials(username: str, password: str) -> tuple[bool, str]:
    raise NotImplementedError("GC API support not yet available")


def has_api_tokens() -> bool:
    return False


def get_api_token_info() -> dict | None:
    return None


def get_api_client():
    raise NotImplementedError("GC API support not yet available")


def is_gc_api_verified() -> bool:
    return False


def set_gc_api_verified(verified: bool = True) -> None:
    pass


def ensure_gc_checked() -> None:
    pass
