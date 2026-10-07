"""A CargoClient that falls back to Special:CargoExport when api.php rate-limits.

Why this exists
---------------
`lol.fandom.com/api.php?action=cargoquery` sits behind a per-IP quota that a
single page load can exhaust (the app fires ~13 cargoquery calls per request).
Once exhausted it returns {"error":{"code":"ratelimited"}}.

`Special:CargoExport` renders the same Cargo query but is served through the
normal page pipeline, NOT through api.php, so it is not subject to that quota.
Measured: 15/15 consecutive requests returned 200 while api.php was refusing
every call.

Strategy: try api.php first (it is the documented path and returns richer
metadata), fall back to CargoExport on rate limit. Both produce identical
row dicts, so callers cannot tell the difference.
"""
import json
import logging
import urllib.parse
import urllib.request

from mwcleric.clients.cargo_client import CargoClient

logger = logging.getLogger(__name__)

# CargoExport is a page render, not an API call. A browser-ish UA keeps
# Cloudflare's bot heuristics happy; a bare "python-urllib" UA gets a JS
# challenge page.
USER_AGENT = (
    "Mozilla/5.0 (compatible; jackey.love-stats/1.0; +https://jackey.love)"
)

# The export page needs a short settle window; it is a full page render.
TIMEOUT = 30


def _is_rate_limited(exc):
    text = str(exc).lower()
    return "ratelimited" in text or "rate limit" in text


class CargoExportFallbackClient(CargoClient):
    """Drop-in CargoClient whose query() survives api.php rate limiting."""

    def __init__(self, client, wiki_host="lol.fandom.com", **kwargs):
        super().__init__(client, **kwargs)
        self.wiki_host = wiki_host
        self._export_url = "https://%s/wiki/Special:CargoExport" % wiki_host
        self.used_fallback = False

    def query(self, *, tables, fields, where=None, join_on=None,
              group_by=None, having=None, order_by=None, offset=None,
              limit=None, auto_continue=True):
        try:
            return super().query(
                tables=tables, fields=fields, where=where, join_on=join_on,
                group_by=group_by, having=having, order_by=order_by,
                offset=offset, limit=limit, auto_continue=auto_continue)
        except Exception as e:
            if not _is_rate_limited(e):
                raise
            logger.warning("cargoquery rate limited; retrying via "
                           "Special:CargoExport (tables=%s)", tables)
            self.used_fallback = True
            return self._query_via_export(
                tables=tables, fields=fields, where=where, join_on=join_on,
                group_by=group_by, having=having, order_by=order_by,
                offset=offset, limit=limit)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _as_str(value):
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        return str(value)

    def _query_via_export(self, *, tables, fields, where, join_on,
                          group_by, having, order_by, offset, limit):
        params = {"format": "json"}
        for name, value in (
            ("tables", tables), ("fields", fields), ("join_on", join_on),
            ("where", where), ("group_by", group_by), ("having", having),
            ("order_by", order_by),
        ):
            s = self._as_str(value)
            if s is not None:
                params[name] = s

        # CargoExport caps a single call at 500 rows; page through with offset.
        page_size = 500
        want = page_size if limit is None or limit == "max" else int(limit)
        collected = []
        cursor = 0 if offset is None else int(offset)

        while True:
            take = min(page_size, want - len(collected))
            if take <= 0:
                break
            params["limit"] = str(take)
            params["offset"] = str(cursor)
            rows = self._fetch_export(params)
            collected.extend(rows)
            if len(rows) < take:
                break
            cursor += len(rows)

        return collected

    def _fetch_export(self, params):
        url = self._export_url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        if raw.lstrip().startswith("<"):
            raise RuntimeError(
                "CargoExport returned HTML (Cloudflare challenge?) instead of "
                "JSON; first 120 chars: %s" % raw[:120])

        rows = json.loads(raw)
        if isinstance(rows, dict) and "error" in rows:
            raise RuntimeError("CargoExport error: %s" % rows["error"])

        # CargoExport can append a "__precision" companion field for datetime
        # columns. api.php does not, so strip them to keep both paths identical.
        cleaned = []
        for row in rows:
            cleaned.append({
                k: v for k, v in row.items() if not k.endswith("__precision")
            })
        return cleaned
