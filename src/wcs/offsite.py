"""Off-site copies of the nightly backup, in a Backblaze B2 bucket.

Uses B2's own API over HTTPS (no extra software). Set in deploy/.env:

    WCS_B2_BUCKET=<bucket name>
    WCS_B2_KEY_ID=<keyID>
    WCS_B2_APPLICATION_KEY=<applicationKey>

The charity's sites can share one bucket and key: each site's backups have their own name (``PREFIX``), and only
its own old copies are deleted. The key should be limited to that one bucket. Each backup is uploaded with its SHA-1 checked by B2; the newest
``keep`` copies are kept there and older ones deleted. Nothing here ever prints the key.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from wcs.backup import PREFIX

log = logging.getLogger(__name__)

AUTHORIZE = "https://api.backblazeb2.com/b2api/v2/b2_authorize_account"
KEEP = 60


class OffsiteError(RuntimeError):
    pass


def configured(env: dict[str, str] | None = None) -> dict[str, str] | None:
    env = env if env is not None else dict(os.environ)
    keys = {k: (env.get(f"WCS_B2_{k.upper()}") or "").strip() for k in ("bucket", "key_id", "application_key")}
    return keys if all(keys.values()) else None


class B2:
    def __init__(self, bucket: str, key_id: str, application_key: str, client: httpx.Client | None = None):
        self.bucket = bucket
        self._own = client is None
        self.client = client or httpx.Client(timeout=300)
        resp = self.client.get(AUTHORIZE, auth=(key_id, application_key))
        if resp.status_code in (401, 403):
            raise OffsiteError("Backblaze did not accept the key: check WCS_B2_KEY_ID and WCS_B2_APPLICATION_KEY")
        resp.raise_for_status()
        auth = resp.json()
        self.account = auth["accountId"]
        self.api = auth["apiUrl"]
        self.token = auth["authorizationToken"]
        allowed = auth.get("allowed") or {}
        if allowed.get("bucketName") and allowed["bucketName"] != bucket:
            raise OffsiteError(f"the key is for the bucket {allowed['bucketName']!r}, not {bucket!r}")
        self.bucket_id = allowed.get("bucketId") or self._find_bucket()

    def close(self) -> None:
        if self._own:
            self.client.close()

    def _call(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self.client.post(f"{self.api}/b2api/v2/{name}", headers={"Authorization": self.token}, json=body)
        if resp.status_code >= 400:
            raise OffsiteError(f"{name}: {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def _find_bucket(self) -> str:
        found = self._call("b2_list_buckets", {"accountId": self.account, "bucketName": self.bucket}).get("buckets")
        if not found:
            raise OffsiteError(f"there is no bucket called {self.bucket!r} that this key can see")
        return found[0]["bucketId"]

    def upload(self, path: Path, tries: int = 3) -> dict[str, Any]:
        data = Path(path).read_bytes()
        sha1 = hashlib.sha1(data).hexdigest()  # noqa: S324 (B2 checks uploads with SHA-1)
        last = ""
        for _ in range(tries):  # B2 asks for a fresh upload address after a failed upload
            target = self._call("b2_get_upload_url", {"bucketId": self.bucket_id})
            resp = self.client.post(target["uploadUrl"], content=data, headers={
                "Authorization": target["authorizationToken"],
                "X-Bz-File-Name": urllib.parse.quote(Path(path).name),
                "Content-Type": "application/gzip", "X-Bz-Content-Sha1": sha1})
            if resp.status_code < 400:
                return resp.json()
            last = f"{resp.status_code} {resp.text[:200]}"
        raise OffsiteError(f"upload failed: {last}")

    def backups(self) -> list[dict[str, Any]]:
        files = self._call("b2_list_file_names", {"bucketId": self.bucket_id, "prefix": PREFIX,
                                                  "maxFileCount": 1000}).get("files") or []
        return sorted(files, key=lambda f: f["fileName"])

    def prune(self, keep: int = KEEP) -> int:
        old = self.backups()[:-keep] if keep > 0 else []
        for f in old:
            self._call("b2_delete_file_version", {"fileName": f["fileName"], "fileId": f["fileId"]})
        return len(old)


def _status(dest: Path, status: dict[str, Any]) -> None:
    with contextlib.suppress(OSError):
        (Path(dest) / "offsite.json").write_text(json.dumps(status))


def send(archive: Path, *, keep: int = KEEP, env: dict[str, str] | None = None,
         client: httpx.Client | None = None) -> dict[str, Any]:
    """Upload one backup and prune old copies. Raises OffsiteError if it could not be stored."""
    cfg = configured(env)
    if cfg is None:
        return {"offsite": "not set up"}
    when = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        b2 = B2(cfg["bucket"], cfg["key_id"], cfg["application_key"], client)
        try:
            b2.upload(archive)
            try:
                pruned = b2.prune(keep)
            except OffsiteError as exc:  # a key without delete rights still stores backups
                log.warning("old off-site backups not deleted: %s", exc)
                pruned = 0
            stored = len(b2.backups())
        finally:
            b2.close()
    except (OffsiteError, httpx.HTTPError) as exc:
        _status(archive.parent, {"ok": False, "when": when, "error": str(exc)[:300]})
        raise OffsiteError(str(exc)) from exc
    status = {"ok": True, "when": when, "file": archive.name, "bucket": cfg["bucket"], "stored": stored,
              "deleted": pruned}
    _status(archive.parent, status)
    return status


def check(env: dict[str, str] | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """Whether the key works, and what is in the bucket (for `wcs offsite`)."""
    cfg = configured(env)
    if cfg is None:
        return {"ok": False, "error": "not set up: add WCS_B2_BUCKET, WCS_B2_KEY_ID and "
                                      "WCS_B2_APPLICATION_KEY to deploy/.env"}
    b2 = B2(cfg["bucket"], cfg["key_id"], cfg["application_key"], client)
    try:
        files = b2.backups()
    finally:
        b2.close()
    return {"ok": True, "bucket": cfg["bucket"], "backups": len(files),
            "newest": files[-1]["fileName"] if files else None}
