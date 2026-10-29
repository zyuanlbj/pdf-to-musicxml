"""分块并行下载（穿过 GitHub 限速环境的可靠下载器）。

背景：在出口限速/代理环境下，单连接通常拉到 9–10 MB 就被掐断，
大文件永远下不完。而**分段请求（Range）每次都能拿满**，所以正确做法是
切成 1 MB 块并发下载，再按序拼装并校验总字节数。

用法:
    python gh_download.py <url> <目标文件> [--workers 4] [--chunk 1048576]

也可作为模块用:
    from gh_download import download
    download(url, Path("x.msi"))
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CHUNK = 1024 * 1024
WORKERS = 4          # 实测 4 并发最稳；8 并发会把代理打饱和反而大量失败
RETRIES = 6


def _proxy_args() -> list[str]:
    p = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
         or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))
    return ["-x", p] if p else []


def remote_size(url: str) -> int:
    cmd = ["curl", "-sIL", "--max-time", "40"] + _proxy_args() + [url]
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    size = 0
    for line in p.stdout.splitlines():
        if line.lower().startswith("content-length:"):
            v = line.split(":", 1)[1].strip()
            if v.isdigit() and int(v) > 0:
                size = int(v)
    return size


def _grab(url: str, start: int, end: int, out: Path) -> bool:
    want = end - start + 1
    cmd = (["curl", "-sL", "--max-time", "120", "-r", f"{start}-{end}",
            "-o", str(out)] + _proxy_args() + [url])
    for _ in range(RETRIES):
        subprocess.run(cmd, capture_output=True)
        if out.exists() and out.stat().st_size == want:
            return True
    return False


def download(url: str, dst: Path, workers: int = WORKERS, chunk: int = CHUNK) -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    total = remote_size(url)
    if total == 0:
        print("[WARN] 拿不到远端大小，回退单流下载（可能中途断流）")
        subprocess.run(["curl", "-L", "--max-time", "1800", "-C", "-",
                        "-o", str(dst)] + _proxy_args() + [url], capture_output=True)
        return dst

    if dst.exists() and dst.stat().st_size == total:
        print(f"[SKIP] 已存在且大小一致: {dst.name} ({total / 1048576:.1f} MB)")
        return dst

    n = (total + chunk - 1) // chunk
    print(f"[INFO] {dst.name}: {total / 1048576:.1f} MB, {n} 块 x {chunk // 1024} KB, {workers} 并发")

    with tempfile.TemporaryDirectory(prefix="ghdl_") as td:
        tmp = Path(td)

        def job(i: int) -> bool:
            s = i * chunk
            e = min(s + chunk - 1, total - 1)
            return _grab(url, s, e, tmp / f"{i:05d}.part")

        done = [0]
        def track(i: int) -> bool:
            ok = job(i)
            done[0] += 1
            if done[0] % 10 == 0 or done[0] == n:
                print(f"       {done[0]}/{n} 块")
            return ok

        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(track, range(n)))

        missing = [i for i, ok in enumerate(results) if not ok]
        for attempt in range(1, 4):
            if not missing:
                break
            print(f"[RETRY] 第 {attempt} 轮补块: {len(missing)} 块")
            with ThreadPoolExecutor(max_workers=workers) as ex:
                again = list(ex.map(job, missing))
            missing = [i for i, ok in zip(missing, again) if not ok]

        if missing:
            raise RuntimeError(f"仍有 {len(missing)} 块下载失败: {missing[:10]}")

        with open(dst, "wb") as fh:
            for i in range(n):
                fh.write((tmp / f"{i:05d}.part").read_bytes())

    got = dst.stat().st_size
    if got != total:
        raise RuntimeError(f"拼装后大小不符: {got} != {total}")
    print(f"[OK] {dst}  ({got / 1048576:.1f} MB)")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="分块并行下载")
    ap.add_argument("url")
    ap.add_argument("dst")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--chunk", type=int, default=CHUNK)
    a = ap.parse_args()
    if not shutil.which("curl"):
        print("[ERR] 需要 curl", file=sys.stderr)
        return 2
    download(a.url, Path(a.dst), a.workers, a.chunk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
