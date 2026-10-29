"""从矢量排版 PDF 的文字层里无损抽取歌词（替代 OCR）。

为什么需要它：Audiveris 的 OCR 对中文歌词几乎不可用（实测把「越过绵绵的高山」
读成「」= 骝」「水不」「蒯过」）。但如果 PDF 是制谱软件导出的矢量排版，
歌词本身就是**文本对象**，带精确坐标，直接抽出来是 100% 准确的，不用猜。

注意两点：
  1. MuseScore 排版中文歌词时会混入康熙部首（U+2F70 ⾼ 而不是 U+9AD8 高），
     必须做 NFKC 归一化，否则写回乐谱会变成怪异字形。
  2. 音乐符号字体（Leland / Bravura / Opus）的码位在私用区（U+E000 起），
     必须排除，否则会把谱号读成乱码字符。

用法:
    python extract_lyrics.py <input.pdf> [-o 输出目录] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf

MUSIC_FONT_HINTS = ("leland", "bravura", "opus", "maestro", "petaluma", "sonata",
                    "musescore", "emmentaler", "feta", "smufl")
# 版面噪声：页码、声部缩写、速度术语等
NOISE_WORDS = {"pno.", "piano", "pno", "page", "coda", "d.s.", "fine"}

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")


def _is_music_font(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in MUSIC_FONT_HINTS)


def _has_pua(text: str) -> bool:
    return any(0xE000 <= ord(c) <= 0xF8FF for c in text)


def _normalize(text: str) -> str:
    """NFKC 归一化：康熙部首/全角变体/兼容字形统一成标准汉字。"""
    return unicodedata.normalize("NFKC", text).strip()


def _keep(text: str, size: float, y: float, page_h: float) -> bool:
    if not text or _has_pua(text):
        return False
    if text.lower().strip(" .") in NOISE_WORDS:
        return False
    if re.fullmatch(r"[\d\s.,:;'\-–—()\[\]/]+", text):
        return False
    # 速度/表情记号：♩=92 之类常被抽成 "= 92"
    if re.fullmatch(r"[=\s\d.½¼¾]+", text):
        return False
    if size > 15:                      # 标题级文字
        return False
    if y < page_h * 0.13:              # 页眉：标题、作者、版权
        return False
    # 歌词至少两个字，或含中文
    if CJK_RE.search(text):
        return True
    return len(text.strip()) >= 2


def _join(spans: list[dict]) -> str:
    """把一行的 span 拼成字符串。

    中文之间不能插空格（CJK 字距本来就有间隙），英文单词之间要保留。
    """
    parts: list[str] = []
    prev = None
    for sp in spans:
        if prev is not None:
            gap = sp["x"] - prev["x"]
            prev_last = prev["text"][-1] if prev["text"] else ""
            cur_first = sp["text"][0] if sp["text"] else ""
            # 中文字符相邻 -> 不插空格
            cjk_adjacent = bool(CJK_RE.match(prev_last)) and bool(CJK_RE.match(cur_first))
            if not cjk_adjacent and gap > max(sp["size"] * 1.6, 6):
                parts.append(" ")
        parts.append(sp["text"])
        prev = sp
    joined = "".join(parts)
    joined = re.sub(r"[ \t]+", " ", joined).strip()
    return joined


def extract(pdf_path: str) -> dict:
    doc = pymupdf.open(pdf_path)
    pages = []
    for page in doc:
        page_h = page.rect.height
        spans = []
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                for sp in line.get("spans", []):
                    if _is_music_font(sp["font"]):
                        continue
                    raw = "".join(c["c"] for c in sp["chars"])
                    text = _normalize(raw)
                    x0, y0 = sp["bbox"][0], sp["bbox"][1]
                    if not _keep(text, sp["size"], y0, page_h):
                        continue
                    spans.append({"x": round(x0, 1), "y": round(y0, 1),
                                  "size": round(sp["size"], 1), "text": text})
        # 按 y 聚成行（容差随字号缩放），行内按 x 排序
        spans.sort(key=lambda s: (s["y"], s["x"]))
        lines: list[list[dict]] = []
        for sp in spans:
            placed = False
            for ln in lines:
                if abs(ln[0]["y"] - sp["y"]) <= max(4.0, ln[0]["size"] * 0.6):
                    ln.append(sp)
                    placed = True
                    break
            if not placed:
                lines.append([sp])
        out_lines = []
        for ln in lines:
            ln.sort(key=lambda s: s["x"])
            joined = _join(ln)
            if joined and (CJK_RE.search(joined) or len(joined) > 2):
                out_lines.append({"y": round(ln[0]["y"], 1), "text": joined,
                                  "n_chars": sum(len(s["text"]) for s in ln)})
        pages.append({"page": page.number + 1, "lines": out_lines})

    doc.close()
    total = sum(len(p["lines"]) for p in pages)
    chars = sum(l["n_chars"] for p in pages for l in p["lines"])
    has_cjk = any(CJK_RE.search(l["text"]) for p in pages for l in p["lines"])
    return {"file": str(Path(pdf_path).resolve()), "n_pages": len(pages),
            "n_lines": total, "n_chars": chars, "has_cjk": has_cjk, "pages": pages}


def to_text(res: dict) -> str:
    L = [f"# 歌词（从 PDF 文字层抽取，非 OCR）", ""]
    L.append(f"- 源文件：`{res['file']}`")
    L.append(f"- 共 {res['n_lines']} 行 / {res['n_chars']} 字")
    if not res["has_cjk"]:
        L.append("- 未检测到中文字符，可能是英文谱或文字层缺失")
    L.append("")
    for p in res["pages"]:
        if not p["lines"]:
            continue
        L.append(f"## 第 {p['page']} 页\n")
        for ln in p["lines"]:
            L.append(f"{ln['text']}\n")
    L.append("---\n")
    L.append("这些文字带精确坐标，是矢量 PDF 的真实文本内容，可直接粘贴进 MuseScore 的歌词工具。")
    L.append("注意：中文歌词按行给出，MuseScore 需要按音节逐个挂到音符上，可用「工具 → 填充歌词」粘贴。")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="从矢量 PDF 文字层抽取歌词")
    ap.add_argument("pdf_path")
    ap.add_argument("-o", "--out-dir", default=None)
    ap.add_argument("--json", action="store_true", help="同时输出带坐标的 JSON")
    a = ap.parse_args()

    if not Path(a.pdf_path).exists():
        print(f"[ERR] 找不到 {a.pdf_path}", file=sys.stderr)
        return 2

    res = extract(a.pdf_path)
    print(f"[OK] {res['n_lines']} 行 / {res['n_chars']} 字，"
          f"{'含中文' if res['has_cjk'] else '未见中文'}")
    for p in res["pages"]:
        for ln in p["lines"][:3]:
            print(f"   p{p['page']} y={ln['y']:>6} {ln['text'][:60]}")

    if a.out_dir:
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "lyrics.txt").write_text(to_text(res), encoding="utf-8")
        print(f"[OK] -> {out / 'lyrics.txt'}")
        if a.json:
            (out / "lyrics.json").write_text(
                json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] -> {out / 'lyrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
