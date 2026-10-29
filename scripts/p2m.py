"""PDF 曲谱 → MusicXML 流水线总控（Audiveris 引擎）。

一条命令跑完：环境检查 → PDF 分诊 → OMR 识别 → MusicXML 导出 → MIDI/PDF 渲染 → 校对报告。

用法:
    python p2m.py run <input.pdf> [-o 输出目录] [--sheets 1-3] [--keep-work]
    python p2m.py doctor          # 环境体检
    python p2m.py probe <pdf>     # 只看分诊结论

产物（默认写到 PDF 同目录的 <文件名>_musicxml/）:
    <name>.musicxml     主产物，可直接拖进 MuseScore / Sibelius / Finale
    <name>.mxl          Audiveris 原生导出的压缩 MusicXML（保留一份）
    <name>.mid          MIDI，用于试听校验
    <name>_rendered.pdf 识别结果重新排版后的 PDF，用于与原谱对比
    <name>_rendered.png 重排后的首页图
    report.md           校对报告（含逐页对比图与 Audiveris 告警汇总）
    work/               中间文件（--keep-work 时保留）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import probe as probe_mod  # noqa: E402

DEFAULT_TIMEOUT_PER_SHEET = 420  # 秒/页，给慢机器留余量


# ------------------------------------------------------------ 引擎定位
def find_audiveris() -> str | None:
    """按 环境变量 → PATH → 常见安装目录 的顺序定位 Audiveris 可执行文件。"""
    env = os.environ.get("AUDIVERIS_BIN")
    if env and Path(env).exists():
        return env

    names = ["Audiveris.exe", "Audiveris.bat", "Audiveris", "audiveris"]
    for n in names:
        p = shutil.which(n)
        if p:
            return p

    roots = [
        Path(__file__).resolve().parent.parent / "engine" / "Audiveris",
        Path.home() / "Audiveris",
        Path(r"C:\Program Files\Audiveris"),
        Path(r"C:\Program Files (x86)\Audiveris"),
        Path("/opt/audiveris"),
        Path("/Applications/Audiveris.app/Contents/MacOS"),
    ]
    for r in roots:
        if not r.is_dir():
            continue
        for n in names:
            cand = r / n
            if cand.is_file():
                return str(cand)
        # 有时在 bin/ 子目录里
        for sub in r.glob("*/Audiveris.exe"):
            return str(sub)
    return None


def find_musescore() -> str | None:
    env = os.environ.get("MUSESCORE_BIN")
    if env and Path(env).exists():
        return env
    for n in ["MuseScore4.exe", "MuseScore3.exe", "mscore", "musescore", "mscore4"]:
        p = shutil.which(n)
        if p:
            return p
    roots = [
        r"C:\Program Files\MuseScore 4\bin",
        r"C:\Program Files\MuseScore 3\bin",
        r"C:\Program Files (x86)\MuseScore 4\bin",
        "/Applications/MuseScore 4.app/Contents/MacOS",
        "/usr/bin", "/usr/local/bin",
    ]
    for r in roots:
        for n in ["MuseScore4.exe", "MuseScore3.exe", "mscore", "musescore"]:
            p = Path(r) / n
            if p.exists():
                return str(p)
    return None


# ------------------------------------------------------------ 引擎调用
def run_audiveris(audi: str, pdf: Path, out_dir: Path, sheets: str | None,
                  timeout: int, ocr_lang: str | None = None) -> dict:
    """调用 Audiveris 无界面识别。

    坑 1：`-sheets` 是多值选项，会把后面所有参数都当自己的值（包括输入文件路径），
    导致 NumberFormatException。必须用 `--` 把输入文件与选项隔开。
    坑 2：OCR 语言默认只认 `eng`，装了 chi_sim 也不会自动用，必须显式指定
    `Language.defaultSpecification`，否则中文歌词连尝试都不会尝试。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [audi, "-batch", "-transcribe", "-export", "-output", str(out_dir)]
    if ocr_lang:
        cmd += ["-constant",
                f"org.audiveris.omr.text.Language.defaultSpecification={ocr_lang}"]
    if sheets:
        cmd += ["-sheets", sheets]      # 写成 "1-3,5" 这种单 token 形式
    cmd += ["--", str(pdf)]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "error": f"Audiveris 超时（>{timeout}s）",
                "seconds": timeout,
                "stdout_tail": (e.stdout or "")[-800:] if isinstance(e.stdout, str) else ""}
    dur = round(time.time() - t0, 1)
    return {"ok": True, "returncode": rc, "seconds": dur,
            "stdout_tail": (out or "")[-1500:], "stderr_tail": (err or "")[-1500:]}


def unzip_mxl(mxl: Path, dst: Path) -> bool:
    """把压缩的 .mxl 解成普通 MusicXML。

    .mxl 是个 zip：META-INF/container.xml + 真正的乐谱 xml。取其中体积最大的 xml。
    """
    try:
        with zipfile.ZipFile(mxl) as z:
            cands = [i for i in z.infolist()
                     if i.filename.lower().endswith((".xml", ".musicxml"))
                     and "META-INF" not in i.filename]
            if not cands:
                return False
            pick = max(cands, key=lambda i: i.file_size)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(z.read(pick))
            return dst.stat().st_size > 200
    except zipfile.BadZipFile:
        return False


def collect_logs(omr_dir: Path, work: Path) -> list[Path]:
    """收集 Audiveris 产生的日志。"""
    found: list[Path] = []
    for pat in ("*.log", ".Audiveris/*.log"):
        found.extend(sorted(omr_dir.glob(pat)))
    return found


def summarize_log(paths: list[Path]) -> dict:
    """从日志里抓 WARN / ERROR 行，做归类计数。"""
    warns: list[str] = []
    errors: list[str] = []
    for p in paths:
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                low = line.lower()
                if " error " in low or line.startswith("ERROR"):
                    errors.append(line.strip())
                elif " warn " in low or line.startswith("WARN"):
                    warns.append(line.strip())
        except OSError:
            continue
    return {"warnings": warns[:40], "errors": errors[:40],
            "n_warnings": len(warns), "n_errors": len(errors)}


def music_stats(xml_path: Path) -> dict:
    """从 MusicXML 里数出关键指标，用于报告里一眼判断识别质量。

    关于歌词：Audiveris 不产出逐音节的 `<lyric>`，而是把谱表下方的文字行
    输出成 `<direction><direction-type><words>`（行级文本）。
    只统计 `<lyric>` 会误判为「没有歌词」，两者都要看。
    """
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(str(xml_path)).getroot()
        parts = root.findall("part")
        notes = root.findall(".//note")
        words = [(w.text or "").strip()
                 for w in root.findall(".//direction/direction-type/words")]
        return {
            "measures": sum(len(p.findall("measure")) for p in parts),
            "parts": len(parts),
            "notes": len([n for n in notes if n.find("pitch") is not None]),
            "rests": len([n for n in notes if n.find("rest") is not None]),
            "harmonies": len(root.findall(".//harmony")),
            "lyric_els": len([l for l in root.findall(".//lyric")
                              if (l.findtext("text") or "").strip()]),
            "text_words": len([w for w in words if w]),
            "text_sample": " / ".join(w for w in words if w)[:180],
        }
    except Exception:  # noqa: BLE001
        return {}


def musescore_export(ms: str, src: Path, dst: Path, fmt_desc: str = "") -> dict:
    try:
        p = subprocess.run([ms, "-f", "-o", str(dst), str(src)],
                           capture_output=True, text=True, timeout=900,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"MuseScore 超时（{fmt_desc}）"}
    ok = dst.exists() and dst.stat().st_size > 0
    return {"ok": ok, "error": None if ok else (p.stderr or "")[-400:]}


def pdf_first_page_png(pdf: Path, dst: Path, dpi: int = 130) -> dict:
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf))
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72))
        pix.save(str(dst))
        doc.close()
        return {"ok": dst.exists() and dst.stat().st_size > 0}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------ 主流程
def run(pdf_path: str, out_dir: str | None, sheets: str | None,
        keep_work: bool, skip_midi: bool, timeout: int | None,
        ocr_lang: str | None = None) -> int:
    pdf = Path(pdf_path).resolve()
    if not pdf.exists():
        print(f"[ERR] 找不到文件: {pdf}")
        return 2

    audi = find_audiveris()
    if not audi:
        print("[ERR] 未找到 Audiveris。先运行: python scripts/install_engine.py")
        return 2

    out_root = Path(out_dir).resolve() if out_dir else pdf.parent / f"{pdf.stem}_musicxml"
    work = out_root / "work"
    omr_dir = work / "omr"
    visuals = out_root / "visuals"
    for d in (out_root, omr_dir, visuals):
        d.mkdir(parents=True, exist_ok=True)
    stem = pdf.stem

    # ---- 1. 分诊 ----
    print(f"[1/5] 分诊 PDF: {pdf.name}")
    pj = probe_mod.probe(str(pdf))
    (work / "probe.json").write_text(json.dumps(pj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      {pj['n_pages']} 页，类型 = {pj['overall']}")
    low_dpi = [p for p in pj["pages"]
               if p["kind"] == "scan" and p["max_image_px_width"]
               and p["max_image_px_width"] / max(p["size_pt"][0] / 72.0, .1) < 200]
    quality_note = ""
    if low_dpi:
        quality_note = (f"第 {', '.join(str(p['page']) for p in low_dpi)} 页有效分辨率低于 200dpi，"
                        f"识别精度会明显下降。")
        print(f"      [WARN] {quality_note}")

    # ---- 2. 原谱预览 + 歌词抽取 ----
    print("[2/5] 生成原谱预览 / 抽取文字层")
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf))
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(matrix=pymupdf.Matrix(110 / 72, 110 / 72))
            pix.save(str(visuals / f"source_p{i + 1:03d}.png"))
        doc.close()
        print(f"      {len(list(visuals.glob('source_p*.png')))} 张预览图")
    except Exception as e:  # noqa: BLE001
        print(f"      [WARN] 预览失败: {e}")

    # 矢量 PDF 的歌词是真实文本，直接抽比 OCR 准得多（实测可达 100%）
    lyr = None
    if pj["overall"] in ("vector", "mixed"):
        try:
            import extract_lyrics as ly
            res = ly.extract(str(pdf))
            if res["n_lines"]:
                (out_root / "lyrics.txt").write_text(ly.to_text(res), encoding="utf-8")
                lyr = res
                print(f"      [OK] 文字层抽取 {res['n_lines']} 行 / {res['n_chars']} 字"
                      f"{'（含中文）' if res['has_cjk'] else ''} -> lyrics.txt")
            else:
                print("      文字层无可抽取的歌词（可能 PDF 的文字已转曲）")
        except Exception as e:  # noqa: BLE001
            print(f"      [WARN] 文字层抽取失败: {e}")
    else:
        print("      扫描件无文字层，歌词只能靠 OCR（中文需 chi_sim 语言包）")

    # ---- 3. OMR ----
    tmo = timeout or max(DEFAULT_TIMEOUT_PER_SHEET * pj["n_pages"], 600)
    print(f"[3/5] Audiveris 识谱（最多等 {tmo}s，页数多时更久）")
    lang = ocr_lang or ("eng+chi_sim" if (lyr and lyr.get("has_cjk")) else None)
    if lang:
        print(f"      OCR 语言: {lang}")
    r = run_audiveris(audi, pdf, omr_dir, sheets, tmo, lang)
    if not r.get("ok"):
        print(f"      [ERR] {r.get('error')}")
        write_report(out_root, pdf, pj, r, None, None, {}, quality_note, lyr=lyr)
        return 1
    print(f"      完成，耗时 {r['seconds']}s（returncode={r['returncode']}）")

    # 找产物
    mxls = sorted(omr_dir.glob("*.mxl")) + sorted(omr_dir.glob("*.musicxml"))
    if not mxls:
        # 有时会写到 PDF 同级目录
        mxls = sorted(pdf.parent.glob(f"{stem}*.mxl"))
    main_xml = out_root / f"{stem}.musicxml"
    got_xml = False
    if mxls:
        src = mxls[0]
        if src.suffix.lower() == ".mxl":
            got_xml = unzip_mxl(src, main_xml)
            shutil.copyfile(src, out_root / f"{stem}.mxl")
        else:
            shutil.copyfile(src, main_xml)
            got_xml = main_xml.stat().st_size > 200
        print(f"      MusicXML: {'ok' if got_xml else '解压失败'} -> {main_xml.name}")
    else:
        print("      [ERR] Audiveris 没有产出 MusicXML")

    logs = collect_logs(omr_dir, work)
    logsum = summarize_log(logs)

    # ---- 4. 导出 MIDI / PDF ----
    print("[4/5] 导出 MIDI / 重排 PDF")
    ms = find_musescore()
    exports: dict = {}
    if got_xml and ms:
        if not skip_midi:
            exports["midi"] = musescore_export(ms, main_xml, out_root / f"{stem}.mid", "MIDI")
            print(f"      MIDI: {'ok' if exports['midi']['ok'] else exports['midi'].get('error')}")
        exports["pdf"] = musescore_export(ms, main_xml, out_root / f"{stem}_rendered.pdf", "PDF")
        print(f"      PDF : {'ok' if exports['pdf']['ok'] else exports['pdf'].get('error')}")
        if exports["pdf"].get("ok"):
            exports["png"] = pdf_first_page_png(out_root / f"{stem}_rendered.pdf",
                                                out_root / f"{stem}_rendered.png")
    elif not ms:
        print("      未找到 MuseScore，跳过 MIDI/PDF（设置 MUSESCORE_BIN 可指定路径）")

    # ---- 5. 报告 ----
    print("[5/5] 生成校对报告")
    mstats = music_stats(main_xml) if got_xml else {}
    if mstats:
        print(f"      小节 {mstats['measures']} / 音符 {mstats['notes']} / "
              f"和弦 {mstats['harmonies']} / 文字行 {mstats['text_words']}")
    report = write_report(out_root, pdf, pj, r, got_xml, main_xml, exports,
                          quality_note, logsum, logs, mstats, lyr)
    print(f"\n[DONE] 产物目录: {out_root}")
    if got_xml:
        print(f"       主文件: {main_xml.name}")
    print(f"       报告  : {report.name}")

    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if got_xml else 1


# ------------------------------------------------------------ 报告
def write_report(out_root: Path, pdf: Path, pj: dict, engine: dict,
                 got_xml: bool | None, main_xml: Path | None,
                 exports: dict, quality_note: str = "",
                 logsum: dict | None = None, logs: list[Path] | None = None,
                 mstats: dict | None = None, lyr: dict | None = None) -> Path:
    L: list[str] = []
    L.append(f"# 校对报告 · {pdf.stem}\n")
    L.append(f"- 源文件：`{pdf}`")
    L.append(f"- 页数：{pj['n_pages']}　页面类型：**{pj['overall']}**")
    L.append(f"- 引擎：Audiveris")
    if engine.get("seconds") is not None:
        L.append(f"- 识别耗时：{engine['seconds']}s")
    if got_xml is not None:
        L.append(f"- MusicXML：{'✅ 已生成' if got_xml else '❌ 未生成'}")
    if quality_note:
        L.append(f"- ⚠️ 图像质量：{quality_note}")
    L.append("")

    # 识别统计：一眼判断质量
    if mstats:
        L.append("## 〇、识别统计\n")
        L.append("| 指标 | 数量 | 怎么用 |")
        L.append("|---|---|---|")
        L.append(f"| 小节 | {mstats['measures']} | 与原谱总小节数对一下，少了就是漏识别 |")
        L.append(f"| 声部 | {mstats['parts']} | 钢琴谱应为 1 个 part + 双谱表 |")
        L.append(f"| 音符 | {mstats['notes']} | 结合密密麻麻程度估漏识率 |")
        L.append(f"| 休止符 | {mstats['rests']} | 异常多说明把音符读成了休止 |")
        L.append(f"| 和弦记号 | {mstats['harmonies']} | 0 = OCR 没生效；有值也要逐个核对根音 |")
        L.append(f"| 文字行（歌词等） | {mstats['text_words']} | 歌词以行级文字输出，不是逐音节 `<lyric>` |")
        L.append("")
        L.append("> Audiveris 把谱表下方的歌词输出成 `<direction>/<words>`（整行文本），"
                 "而不是挂在音符上的 `<lyric>`。所以导入 MuseScore 后歌词是"
                 "**位置正确的文本框**，不能按音节自动对齐，需要手工拆音节。")
        L.append("")
        if mstats.get("text_sample"):
            L.append(f"识别到的文字内容（截断）：\n\n> {mstats['text_sample']}\n")
        if mstats.get("text_words", 0) == 0:
            L.append("> ⚠️ 文字行数为 0。若原谱确实有歌词/和弦文字，最可能的原因是"
                     "**OCR 语言包缺失**，而不是识别失败——缺语言包时 Audiveris 静默跳过文字、"
                     "不报错。中文歌词需要 `chi_sim`：\n"
                     "> ```\n> python scripts/install_engine.py --lang eng,chi_sim\n> ```\n")

    # 矢量 PDF 的文字层歌词（比 OCR 准得多）
    if lyr and lyr.get("n_lines"):
        L.append("## 〇之二、歌词（文字层抽取，非 OCR）\n")
        L.append(f"这份 PDF 是矢量排版，歌词是真实文本对象，直接从文字层抽出，"
                 f"**{lyr['n_lines']} 行 / {lyr['n_chars']} 字，内容无损失**。完整内容见 `lyrics.txt`。\n")
        for p in lyr["pages"]:
            if not p["lines"]:
                continue
            L.append(f"**第 {p['page']} 页**\n")
            for ln in p["lines"]:
                L.append(f"- {ln['text']}")
            L.append("")
        L.append("> 这些文字带精确坐标，但 MusicXML 里的音符没有坐标信息，无法可靠地自动逐音节挂载。"
                 "建议在 MuseScore 里用「工具 → 填充歌词」粘贴对应行，比对着 OCR 结果改快得多。\n")

    # 逐页
    L.append("## 一、逐页分诊\n")
    L.append("| 页 | 类型 | 尺寸(pt) | 原生分辨率 | 矢量路径 |")
    L.append("|---|---|---|---|---|")
    for p in pj["pages"]:
        native = ""
        if p["max_image_px_width"]:
            native = f"{round(p['max_image_px_width'] / max(p['size_pt'][0] / 72.0, .1))} dpi"
        L.append(f"| {p['page']} | {p['kind']} | {p['size_pt'][0]}×{p['size_pt'][1]} | {native} | {p['n_drawings']} |")
    L.append("")
    for n in pj.get("notes", []):
        L.append(f"> {n}")
    if pj.get("notes"):
        L.append("")

    # 引擎告警
    L.append("## 二、识别过程告警\n")
    if logsum and (logsum["n_warnings"] or logsum["n_errors"]):
        L.append(f"Audiveris 日志：{logsum['n_errors']} 条 ERROR，{logsum['n_warnings']} 条 WARN。\n")
        if logsum["errors"]:
            L.append("**错误：**\n")
            for e in logsum["errors"][:15]:
                L.append(f"- `{e[:180]}`")
            L.append("")
        if logsum["warnings"]:
            L.append("**告警（前 15 条）：**\n")
            for w in logsum["warnings"][:15]:
                L.append(f"- `{w[:180]}`")
            L.append("")
        L.append("这些告警通常指向具体小节（如节奏不完整、符号未识别），是人工复核的第一入口。\n")
    elif engine and not engine.get("ok"):
        L.append(f"识别未完成：{engine.get('error', '')}\n")
    else:
        L.append("未发现 ERROR/WARN。\n")
    if logs:
        L.append("原始日志：")
        for p in logs:
            L.append(f"- `{os.path.relpath(p, out_root).replace(chr(92), '/')}`")
        L.append("")

    # 导出
    L.append("## 三、导出产物\n")
    if exports:
        L.append("| 格式 | 状态 |")
        L.append("|---|---|")
        for k, v in exports.items():
            L.append(f"| {k.upper()} | {'✅' if v.get('ok') else '❌ ' + str(v.get('error', ''))[:60]} |")
    else:
        L.append("（未执行导出，可能未安装 MuseScore）")
    L.append("")

    # 视觉对比
    L.append("## 四、原谱 vs 识别结果\n")
    src_imgs = sorted(visuals.glob("source_p*.png")) if (visuals := out_root / "visuals").is_dir() else []
    rendered_png = out_root / f"{pdf.stem}_rendered.png"
    if src_imgs:
        L.append("**原谱**\n")
        for p in src_imgs[:4]:
            L.append(f"![{p.stem}](visuals/{p.name})\n")
    if rendered_png.exists():
        L.append("**识别结果重排后**\n")
        L.append(f"![rendered]({rendered_png.name})\n")
    if not src_imgs and not rendered_png.exists():
        L.append("（无可用对比图）\n")

    L.append("## 五、人工复核要点\n")
    L.append("1. **先听 MIDI**：调性、拍号、节奏错误在听觉上最容易暴露，比逐音符核对快得多。")
    L.append("2. **抓小节数**：重排后的总小节数应与原谱一致，缺节通常是漏识别了全休止或跨行连线。")
    L.append("3. **看日志里的小节号**：上面每条告警基本都对应一个具体位置，按图索骥即可。")
    L.append("4. **和弦记号与歌词**：Audiveris 会尝试识别，但准确率有限，重点核对这两项。")
    L.append("5. **反复记号与跳房子**：跨页的反复结构最容易丢，建议手工确认。")
    L.append("")

    path = out_root / "report.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return path


# ------------------------------------------------------------ doctor
def doctor() -> int:
    print("=== 环境自检 ===")
    ok = True
    try:
        import pymupdf
        print(f"[OK] pymupdf {getattr(pymupdf, '__version__', '')}")
    except ImportError:
        print("[MISS] pymupdf  ->  pip install pymupdf")
        ok = False

    audi = find_audiveris()
    if audi:
        print(f"[OK] Audiveris: {audi}")
        # 歌词/和弦依赖 Tesseract 语言包，缺了不会报错、只是静默丢文字
        try:
            import install_engine as ie
            td = ie.tessdata_dir()
            langs = [p.stem for p in td.glob("*.traineddata")] if td.is_dir() else []
            if langs:
                print(f"[OK] OCR 语言包: {', '.join(sorted(langs))}  ({td})")
            else:
                print(f"[WARN] OCR 语言包缺失（{td}）")
                print("       歌词与和弦文字将无法识别 -> python scripts/install_engine.py --lang eng")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 无法检查 OCR 语言包: {e}")
    else:
        print("[MISS] Audiveris  ->  python scripts/install_engine.py")
        ok = False

    ms = find_musescore()
    print(f"[{'OK' if ms else 'MISS'}] MuseScore: {ms or '未找到（MIDI/PDF 导出将跳过）'}")

    print("=== " + ("全部就绪" if ok else "存在缺失项") + " ===")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF 曲谱 → MusicXML（Audiveris）")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="完整流水线")
    r.add_argument("pdf_path")
    r.add_argument("-o", "--out-dir", default=None)
    r.add_argument("--sheets", default=None, help='只处理部分页，如 "1-3"')
    r.add_argument("--keep-work", action="store_true")
    r.add_argument("--no-midi", action="store_true")
    r.add_argument("--timeout", type=int, default=None, help="Audiveris 总超时（秒）")
    r.add_argument("--ocr-lang", default=None,
                   help='OCR 语言，如 "eng" / "eng+chi_sim"；缺省按文字层是否含中文自动判断')

    sub.add_parser("doctor", help="检查运行环境")

    p = sub.add_parser("probe", help="只看 PDF 分诊")
    p.add_argument("pdf_path")

    args = ap.parse_args()
    if args.cmd == "doctor":
        return doctor()
    if args.cmd == "probe":
        print(json.dumps(probe_mod.probe(args.pdf_path), ensure_ascii=False, indent=2))
        return 0
    if args.cmd != "run":
        ap.print_help()
        return 2
    return run(args.pdf_path, args.out_dir, args.sheets, args.keep_work,
               args.no_midi, args.timeout, args.ocr_lang)


if __name__ == "__main__":
    raise SystemExit(main())
