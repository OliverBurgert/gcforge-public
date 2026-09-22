"""
Unofficial geocaching.com website client (session + tRPC + scraping).

Everything here needs only a GC username/password (stored via
``accounts.keyring_util``, same as any other platform) — no partner-API token,
no OAuth app registration. That's what makes this package safe to ship in the
public build, unlike ``gcprivate/`` (which holds the official-API-only code and
is stripped from the public build entirely).

See ``docs/reference/geocaching-com-web.md`` for the full write-up: auth model,
tRPC endpoint/envelope shape, the confirmed log/trackable/image CRUD, and the
wider procedure catalog found via a Next.js bundle scan.
"""
