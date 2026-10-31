#!/usr/bin/env python3
"""在主機上下載 pg_bigm 原始碼 tarball，供 Dockerfile COPY 使用（只用標準函式庫）。

來源：PostgreSQL 官方 yum 倉庫（PGDG）的 SRPM。SRPM 內含上游 release tarball。
不從 GitHub 下載的原因：本次驗證環境無法存取 github.com；PGDG SRPM 是另一個公開、
可固定版本的來源。兩個 sha256 都固定在下方，任何一個不符就失敗。

用法：python3 -I fetch_pg_bigm.py [輸出目錄，預設為本檔旁的 vendor/]
"""
import gzip
import hashlib
import pathlib
import struct
import sys
import urllib.request

PG_BIGM_VERSION = "1.2-20250903"
SRPM_URL = (
    "https://download.postgresql.org/pub/repos/yum/srpms/15/redhat/rhel-9-x86_64/"
    "pg_bigm_15-1.2_20250903-1PGDG.rhel9.src.rpm"
)
SRPM_SHA256 = "02a1b14ea7669395b37ace83c65316d31716b87d781697f770bdf73f6b4d3c06"
SRPM_MEMBER = "v1.2-20250903.tar.gz"  # SRPM 內的檔名（= GitHub tag 封存檔）
TARBALL_NAME = f"pg_bigm-{PG_BIGM_VERSION}.tar.gz"
TARBALL_SHA256 = "4d4fb48161e3f9e99207851299379c83b3e5d1d3c1a07c35f6a3153d577b3142"  # 首次下載時記錄的值（無法對照 GitHub 上游）


def _header_len(buf: bytes, off: int) -> int:
    if buf[off:off + 3] != b"\x8e\xad\xe8":
        raise ValueError(f"RPM header magic not found at {off}")
    nindex, hsize = struct.unpack(">II", buf[off + 8:off + 16])
    return 16 + nindex * 16 + hsize


def extract_payload(rpm: bytes) -> bytes:
    if rpm[:4] != b"\xed\xab\xee\xdb":
        raise ValueError("not an RPM file")
    off = 96  # lead
    sig = _header_len(rpm, off)
    off += sig + ((8 - sig % 8) % 8)  # signature header is 8-byte aligned
    off += _header_len(rpm, off)
    payload = rpm[off:]
    if payload[:2] != b"\x1f\x8b":
        raise ValueError("payload is not gzip (unsupported compressor)")
    return gzip.decompress(payload)


def cpio_members(data: bytes):
    off = 0
    while True:
        hdr = data[off:off + 110]
        if hdr[:6] not in (b"070701", b"070702"):
            raise ValueError(f"bad cpio magic at {off}")
        fields = [int(hdr[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        filesize, namesize = fields[6], fields[11]
        name_start = off + 110
        name = data[name_start:name_start + namesize - 1].decode()
        data_start = (name_start + namesize + 3) & ~3
        if name == "TRAILER!!!":
            return
        yield name, data[data_start:data_start + filesize]
        off = (data_start + filesize + 3) & ~3


def main() -> int:
    out_dir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).with_name("vendor")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / TARBALL_NAME
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == TARBALL_SHA256:
        print(f"already present: {target}")
        return 0
    with urllib.request.urlopen(SRPM_URL, timeout=60) as resp:
        rpm = resp.read()
    got = hashlib.sha256(rpm).hexdigest()
    if got != SRPM_SHA256:
        print(f"SRPM sha256 mismatch: {got}", file=sys.stderr)
        return 1
    members = dict(cpio_members(extract_payload(rpm)))
    print("SRPM members:", ", ".join(sorted(members)))
    if SRPM_MEMBER not in members:
        print(f"{SRPM_MEMBER} not in SRPM", file=sys.stderr)
        return 1
    tar = members[SRPM_MEMBER]
    got = hashlib.sha256(tar).hexdigest()
    if got != TARBALL_SHA256:
        print(f"tarball sha256 mismatch: {got}", file=sys.stderr)
        return 1
    target.write_bytes(tar)
    print(f"wrote {target} ({len(tar)} bytes, sha256 {got})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
