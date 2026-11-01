"""Off-site copies of the nightly backup in Backblaze B2 (a simulated B2; no real key is used)."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from wcs.offsite import OffsiteError, check, configured, send

ENV = {"WCS_B2_BUCKET": "wts-backups", "WCS_B2_KEY_ID": "kid", "WCS_B2_APPLICATION_KEY": "secret"}


class FakeB2:
    def __init__(self, bucket="wts-backups", fail_uploads=0, can_delete=True):
        self.bucket, self.fail_uploads, self.can_delete = bucket, fail_uploads, can_delete
        self.files: dict[str, bytes] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("b2_authorize_account"):
            if request.headers.get("authorization") != httpx.BasicAuth("kid", "secret")._auth_header:
                return httpx.Response(401)
            return httpx.Response(200, json={"accountId": "acc", "apiUrl": "https://api.b2.test",
                                             "authorizationToken": "tok",
                                             "allowed": {"bucketId": "b1", "bucketName": self.bucket}})
        assert request.headers.get("authorization") in ("tok", "up"), "every call carries a token"
        body = json.loads(request.content or b"{}") if path.startswith("/b2api") else {}
        if path.endswith("b2_get_upload_url"):
            return httpx.Response(200, json={"uploadUrl": "https://up.b2.test/upload", "authorizationToken": "up"})
        if path == "/upload":
            if self.fail_uploads:
                self.fail_uploads -= 1
                return httpx.Response(503)
            assert request.headers["x-bz-content-sha1"] == hashlib.sha1(request.content).hexdigest()  # noqa: S324
            self.files[request.headers["x-bz-file-name"]] = request.content
            return httpx.Response(200, json={"fileName": request.headers["x-bz-file-name"]})
        if path.endswith("b2_list_file_names"):
            return httpx.Response(200, json={"files": [{"fileName": n, "fileId": f"id-{n}"} for n in sorted(self.files)
                                                       if n.startswith(body["prefix"])]})
        if path.endswith("b2_delete_file_version"):
            if not self.can_delete:
                return httpx.Response(401, json={"code": "unauthorized"})
            del self.files[body["fileName"]]
            return httpx.Response(200, json={})
        return httpx.Response(404)


def archive(tmp_path, day):
    p = tmp_path / f"wcs-state-{day}.sqlite.gz"
    p.write_bytes(f"backup {day}".encode())
    return p


def test_not_set_up_does_nothing(tmp_path):
    assert configured({}) is None
    assert send(archive(tmp_path, "2026-10-12"), env={}) == {"offsite": "not set up"}
    assert check(env={})["ok"] is False


def test_upload_retries_and_keeps_the_newest(tmp_path):
    b2 = FakeB2(fail_uploads=1)
    client = httpx.Client(transport=httpx.MockTransport(b2))
    for day in ("2026-10-10", "2026-10-11", "2026-10-12"):
        out = send(archive(tmp_path, day), keep=2, env=ENV, client=client)
    assert out["ok"] and out["stored"] == 2 and out["deleted"] == 1
    assert sorted(b2.files) == ["wcs-state-2026-10-11.sqlite.gz", "wcs-state-2026-10-12.sqlite.gz"]
    assert json.loads((tmp_path / "offsite.json").read_text())["ok"]
    assert check(env=ENV, client=client) == {"ok": True, "bucket": "wts-backups", "backups": 2,
                                             "newest": "wcs-state-2026-10-12.sqlite.gz"}


def test_a_key_without_delete_rights_still_stores(tmp_path):
    b2 = FakeB2(can_delete=False)
    client = httpx.Client(transport=httpx.MockTransport(b2))
    send(archive(tmp_path, "2026-10-11"), keep=1, env=ENV, client=client)
    assert send(archive(tmp_path, "2026-10-12"), keep=1, env=ENV, client=client)["stored"] == 2


def test_failures_are_reported_and_recorded(tmp_path):
    with pytest.raises(OffsiteError, match="did not accept the key"):
        send(archive(tmp_path, "2026-10-12"), env={**ENV, "WCS_B2_APPLICATION_KEY": "wrong"},
             client=httpx.Client(transport=httpx.MockTransport(FakeB2())))
    status = json.loads((tmp_path / "offsite.json").read_text())
    assert status["ok"] is False and "wrong" not in json.dumps(status), "the key is never written down"
    with pytest.raises(OffsiteError, match="bucket"):
        send(archive(tmp_path, "2026-10-12"), env=ENV,
             client=httpx.Client(transport=httpx.MockTransport(FakeB2(bucket="someone-else"))))


def test_sites_sharing_a_bucket_keep_their_own_backups(tmp_path):
    """Where MPs Stand's backups in the same bucket are neither counted nor deleted."""
    b2 = FakeB2()
    b2.files["where-they-stand-2026-10-01.tar.gz"] = b"another site's"
    client = httpx.Client(transport=httpx.MockTransport(b2))
    for day in ("2026-10-11", "2026-10-12"):
        out = send(archive(tmp_path, day), keep=1, env=ENV, client=client)
    assert out["stored"] == 1 and "where-they-stand-2026-10-01.tar.gz" in b2.files
