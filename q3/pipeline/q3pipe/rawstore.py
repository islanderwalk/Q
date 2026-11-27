"""原始 HTML 存檔（答案：原始 HTML 存 GCS 備重解析）。gzip 壓縮後依看板／種類／編號／抓取時間存一份。"""
from __future__ import annotations

import gzip
import os
from datetime import datetime
from pathlib import Path


def raw_key(board: str, kind: str, ident: str, fetched_at: datetime) -> str:
    return f"{board}/{kind}/{ident}/{fetched_at:%Y%m%dT%H%M%SZ}.html.gz"


class NullStore:
    def put(self, key: str, data: bytes):
        return None


class LocalStore:
    def __init__(self, root: str):
        self.root = Path(root)

    def put(self, key: str, data: bytes):
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(gzip.compress(data))
        return p.as_uri()


class GcsStore:
    def __init__(self, bucket: str, project: str):
        from google.cloud import storage
        if os.environ.get("STORAGE_EMULATOR_HOST"):  # 本機 fake-gcs-server
            from google.auth.credentials import AnonymousCredentials
            self.client = storage.Client(project=project, credentials=AnonymousCredentials())
            b = self.client.bucket(bucket)
            if not b.exists():
                self.client.create_bucket(bucket)
        else:
            self.client = storage.Client(project=project)
        self.bucket = self.client.bucket(bucket)

    def put(self, key: str, data: bytes):
        blob = self.bucket.blob(key)
        blob.upload_from_string(gzip.compress(data), content_type="application/gzip")
        return f"gs://{self.bucket.name}/{key}"


def make_store(cfg):
    if cfg.raw_backend == "gcs":
        return GcsStore(cfg.raw_bucket, cfg.project)
    if cfg.raw_backend == "local":
        return LocalStore(cfg.raw_dir)
    return NullStore()
