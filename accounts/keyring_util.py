"""
Thin wrapper around the `keyring` library for storing platform passwords.

Service name: "gcforge"
Key format:   "<platform>:<username>"   e.g. "oc_de:sarotti99"
"""

_SERVICE = "gcforge"


def _key(platform: str, username: str) -> str:
    return f"{platform}:{username}"


def store_password(platform: str, username: str, password: str) -> None:
    import keyring
    keyring.set_password(_SERVICE, _key(platform, username), password)


def get_password(platform: str, username: str) -> str | None:
    import keyring
    return keyring.get_password(_SERVICE, _key(platform, username))


def delete_password(platform: str, username: str) -> None:
    import keyring
    try:
        keyring.delete_password(_SERVICE, _key(platform, username))
    except Exception:
        pass


def _oauth_key(platform: str, user_id: str) -> str:
    return f"oauth:{platform}:{user_id}"


def store_oauth_token(platform: str, user_id: str, token: str, token_secret: str) -> None:
    import json, keyring
    keyring.set_password(_SERVICE, _oauth_key(platform, user_id),
                         json.dumps({"token": token, "secret": token_secret}))


def get_oauth_token(platform: str, user_id: str) -> tuple[str, str] | None:
    import json, keyring
    raw = keyring.get_password(_SERVICE, _oauth_key(platform, user_id))
    if not raw:
        return None
    data = json.loads(raw)
    return (data["token"], data["secret"])


def has_oauth_token(platform: str, user_id: str) -> bool:
    return get_oauth_token(platform, user_id) is not None


def delete_oauth_token(platform: str, user_id: str) -> None:
    import keyring
    try:
        keyring.delete_password(_SERVICE, _oauth_key(platform, user_id))
    except Exception:
        pass
