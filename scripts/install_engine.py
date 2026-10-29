"""安装 Audiveris 识别引擎（下载 + 解包，不污染系统）。

Audiveris 的 Windows 包是 .msi，但它自带 JRE，所以不需要系统装 Java。
本脚本不执行安装程序，而是把 .msi 当压缩包解到技能目录下的 engine/，
需要时用 AUDIVERIS_BIN 指向它即可，卸载就删目录。

用法:
    python install_engine.py                     # 下载并解包最新验证版本
    python install_engine.py --from-file x.msi   # 用已下载好的 msi
    python install_engine.py --check             # 只检查现有安装
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
ENGINE_DIR = SKILL_DIR / "engine"

VERSION = "5.11.0"
ASSET = f"Audiveris-{VERSION}-windowsConsole-x86_64.msi"
URL = f"https://github.com/Audiveris/audiveris/releases/download/{VERSION}/{ASSET}"

# Audiveris 用 Tesseract 识别歌词与和弦文字。
# 注意必须用 tessdata（完整版，含 legacy + LSTM），tessdata_fast / _best 只有 LSTM，
# 会报 "Could not initialize TessBaseAPI languages: ... in legacy mode"。
TESS_URL = "https://gh-proxy.com/https://github.com/tesseract-ocr/tessdata/raw/main/{lang}.traineddata"
TESS_FALLBACK = [
    "https://github.com/tesseract-ocr/tessdata/raw/main/{lang}.traineddata",
    "https://ghproxy.net/https://github.com/tesseract-ocr/tessdata/raw/main/{lang}.traineddata",
    "https://ghfast.top/https://github.com/tesseract-ocr/tessdata/raw/main/{lang}.traineddata",
    # 不走 GitHub 的备用源，代理挂掉时还能用
    "https://raw.gitmirror.com/tesseract-ocr/tessdata/main/{lang}.traineddata",
    "https://gitee.com/mirrors_tesseract-ocr/tessdata/raw/main/{lang}.traineddata",
]


def tessdata_dir() -> Path:
    """定位 Audiveris 的语言数据目录。"""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "AudiverisLtd" / "audiveris" / "config" / "tessdata"
    return Path.home() / ".config" / "AudiverisLtd" / "audiveris" / "config" / "tessdata"


def ensure_tessdata(langs: list[str]) -> bool:
    """把缺失的语言包下到 Audiveris 的 tessdata 目录。"""
    td = tessdata_dir()
    td.mkdir(parents=True, exist_ok=True)
    import gh_download
    all_ok = True
    for lang in langs:
        dst = td / f"{lang}.traineddata"
        if dst.exists() and dst.stat().st_size > 1024 * 1024:
            print(f"    语言包已存在: {lang}")
            continue
        ok = False
        for tpl in [TESS_URL] + TESS_FALLBACK:
            url = tpl.format(lang=lang)
            try:
                print(f"    下载语言包 {lang} <- {url.split('/')[2]}")
                gh_download.download(url, dst)
                ok = True
                break
            except Exception as e:  # noqa: BLE001
                print(f"      失败: {str(e)[:120]}")
        if not ok:
            all_ok = False
            print(f"    [WARN] {lang} 下载失败，歌词/和弦文字将无法识别")
    return all_ok


def find_7z() -> str | None:
    for c in (shutil.which("7z"), shutil.which("7za"),
              r"C:\Program Files\7-Zip\7z.exe",
              r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if c and Path(c).exists():
            return c
    return None


def locate_exe(root: Path) -> Path | None:
    if not root.exists():
        return None
    direct = root / "Audiveris.exe"
    if direct.exists():
        return direct
    for p in root.rglob("Audiveris.exe"):
        return p
    return None


def verify(exe: Path) -> bool:
    """跑一下 -version 确认能启动（附带验证自带 JRE 可用）。"""
    try:
        p = subprocess.run([str(exe), "-version"], capture_output=True, text=True,
                           timeout=180, encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        print(f"    版本探测输出: {out.strip()[:200]}")
        return p.returncode == 0 or "Audiveris" in out
    except subprocess.TimeoutExpired:
        print("    [WARN] -version 超时，但可执行文件存在，继续")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"    [WARN] 版本探测失败: {e}")
        return False


def extract_msi(msi: Path, target: Path) -> bool:
    """解包 .msi。

    实测结论：7-Zip 打不开 Audiveris 的 msi（报 "Unexpected end of archive"，
    只能把内部 File 表裸流倒出来，没有目录结构），所以 **msi 一律先走 msiexec /a**。
    另有两点关键：
      - msiexec 必须写到**技能目录内**（沙箱可写区），写到 C:\\tmp 之类会报 1619；
      - /a 是管理式解包，不注册到系统，卸载只需删目录。
    """
    target.mkdir(parents=True, exist_ok=True)
    print(f"    用 msiexec /a 解包 -> {target}")
    try:
        p = subprocess.run(["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={target}"],
                           capture_output=True, text=True, errors="replace", timeout=600)
        if p.returncode == 0 and locate_exe(target):
            return True
        print(f"    msiexec 返回 {p.returncode}"
              f"{'（1619=无法打开安装包，通常是目标目录不可写）' if p.returncode == 1619 else ''}")
    except subprocess.TimeoutExpired:
        print("    msiexec 超时")

    # 兜底：7-Zip（对少数 msi 有效，能出文件但常常没有目录结构）
    sz = find_7z()
    if sz:
        print("    回退 7-Zip")
        p = subprocess.run([sz, "x", str(msi), f"-o{target}", "-y"],
                           capture_output=True, text=True, errors="replace")
        if locate_exe(target):
            return True
        print(f"    7z 失败: {(p.stderr or '')[-200:]}")
    return False


def install_tessdata_files(paths: list[str]) -> bool:
    """把手工下载好的 .traineddata 拷进 Audiveris 的 tessdata 目录。

    离线场景用：网络拿不到时，用户自己下载后交给脚本落位并校验。
    """
    td = tessdata_dir()
    td.mkdir(parents=True, exist_ok=True)
    ok = True
    for raw in paths:
        src = Path(raw).expanduser()
        if not src.is_file():
            print(f"    [ERR] 找不到 {src}")
            ok = False
            continue
        if src.suffix.lower() != ".traineddata":
            print(f"    [WARN] {src.name} 不是 .traineddata，仍然复制")
        dst = td / src.name
        shutil.copyfile(src, dst)
        mb = dst.stat().st_size / 1048576
        print(f"    已放入 {src.name} ({mb:.1f} MB)")
        # tessdata_fast 约 2MB、tessdata_best 约 13MB，完整版通常 >20MB
        if "chi_sim" in src.name and mb < 20:
            print(f"    [WARN] chi_sim 只有 {mb:.1f} MB，很可能是 tessdata_fast/_best 版本，"
                  f"缺 legacy 组件，Audiveris 会加载失败。请下载 tessdata 完整版（约 40 MB）")
            ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="安装 Audiveris 引擎")
    ap.add_argument("--check", action="store_true", help="只检查")
    ap.add_argument("--from-file", default=None, help="使用本地已有的 .msi")
    ap.add_argument("--version", default=VERSION)
    ap.add_argument("--lang", default="eng",
                    help="OCR 语言包，逗号分隔（eng / chi_sim / eng,chi_sim）；传 none 跳过")
    ap.add_argument("--tessdata-file", default=None,
                    help="手工下载好的 .traineddata 路径（逗号分隔），直接落位不联网")
    args = ap.parse_args()

    # 离线安装语言包：最高优先级，做完即返回
    if args.tessdata_file:
        files = [x.strip() for x in args.tessdata_file.split(",") if x.strip()]
        print("[OCR] 离线安装语言包")
        ok = install_tessdata_files(files)
        print(f"     目录: {tessdata_dir()}")
        return 0 if ok else 1

    existing = locate_exe(ENGINE_DIR)
    if existing:
        print(f"[OK] 引擎已安装: {existing}")
        verify(existing)
        if args.lang.lower() != "none":
            print("[OCR] 检查语言包")
            ensure_tessdata([x.strip() for x in args.lang.split(",") if x.strip()])
        print(f"\n使用方法:\n  export AUDIVERIS_BIN=\"{existing}\"")
        return 0
    if args.check:
        print("[MISS] 未安装 Audiveris")
        return 1

    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    if args.from_file:
        msi = Path(args.from_file).resolve()
        if not msi.exists():
            print(f"[ERR] 找不到 {msi}")
            return 2
    else:
        import gh_download
        msi = ENGINE_DIR / "_install" / ASSET
        print(f"[1/4] 下载 {ASSET}")
        try:
            gh_download.download(URL, msi)
        except Exception as e:  # noqa: BLE001
            print(f"[ERR] 下载失败: {e}")
            print("      可手动下载后用: python install_engine.py --from-file <路径>")
            return 1

    print("[2/4] 解包")
    staging = ENGINE_DIR / "_extract"
    staging.mkdir(parents=True, exist_ok=True)
    if not extract_msi(msi, staging):
        print("[ERR] 解包失败。可安装 7-Zip 后重试，或直接把 msi 装到默认目录。")
        return 1

    # msi 解出来常带一层多余目录，收敛到 ENGINE_DIR/Audiveris
    print("[3/4] 整理目录")
    exe = locate_exe(staging)
    if not exe:
        print(f"[ERR] 解包后在 {staging} 下找不到 Audiveris.exe")
        return 1
    # 找到包含 Audiveris.exe 的那一层作为根
    home = exe.parent
    while home.parent != staging and home.parent != home:
        # 若上一级只有这一个子项，就上浮
        try:
            if len(list(home.parent.iterdir())) == 1:
                home = home.parent
            else:
                break
        except OSError:
            break
    final = ENGINE_DIR / "Audiveris"
    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
    try:
        shutil.move(str(home), str(final))
    except Exception:  # noqa: BLE001
        final = home
    shutil.rmtree(staging, ignore_errors=True)
    (ENGINE_DIR / "_install").exists() and shutil.rmtree(ENGINE_DIR / "_install", ignore_errors=True)

    print("[4/4] 校验")
    exe = locate_exe(final) or exe
    verify(exe)
    if args.lang.lower() != "none":
        print("[OCR] 安装语言包")
        ensure_tessdata([x.strip() for x in args.lang.split(",") if x.strip()])

    print(f"\n[OK] 引擎就位: {exe}")
    print("     后续运行会自动发现它；也可显式指定：")
    print(f'     export AUDIVERIS_BIN="{exe}"')
    print(f"     卸载：直接删除 {ENGINE_DIR}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(SCRIPT_DIR))
    raise SystemExit(main())
