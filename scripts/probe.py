"""PDF 曲谱分诊：判断每一页是「矢量排版」还是「扫描图像」，并给出推荐渲染 DPI。

用法:
    python probe.py <input.pdf> [--json-out probe.json]

输出一份 JSON，供 p2m.py 决定预处理分支。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

# 常见制谱软件使用的音乐字体族（用于判定「数字排版」页）
MUSIC_FONT_HINTS = (
    "bravura", "opus", "maestro", "engraver", "petaluma", "sonata",
    "lilypond", "emmentaler", "feta", "smufl", "segoe ui symbol",
)


def _page_signals(page: pymupdf.Page) -> dict:
    """采集单页的排版特征。"""
    images = page.get_images(full=True)
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    text_dict = page.get_text("dict")
    fonts = {s.get("font", "").lower() for b in text_dict.get("blocks", [])
             for l in b.get("lines", []) for s in l.get("spans", [])}
    n_glyph_spans = sum(len(l.get("spans", [])) for b in text_dict.get("blocks", [])
                        for l in b.get("lines", []))
    music_fonts = sorted(f for f in fonts if any(h in f for h in MUSIC_FONT_HINTS))

    # 图像覆盖面积占比：整页一张大图 => 扫描件
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    img_area = 0.0
    img_px_width = 0
    for img in images:
        try:
            for rect in page.get_image_rects(img[0]):
                img_area += abs(rect.width * rect.height)
                img_px_width = max(img_px_width, img[2])
        except Exception:
            continue

    return {
        "page": page.number + 1,
        "size_pt": [round(page.rect.width, 1), round(page.rect.height, 1)],
        "n_images": len(images),
        "image_coverage": round(min(img_area / page_area, 1.0), 3),
        "max_image_px_width": img_px_width,
        "n_drawings": len(drawings),
        "n_text_chars": len(page.get_text("text")),
        "n_glyph_spans": n_glyph_spans,
        "music_fonts": music_fonts,
        "rotation": page.rotation,
    }


def classify(sig: dict) -> tuple[str, float, int]:
    """返回 (类别, 置信度, 推荐 DPI)。

    类别: vector（制谱软件导出） / scan（图像扫描或拍照） / blank（空白页）
    """
    if sig["n_drawings"] < 5 and sig["n_glyph_spans"] < 3 and sig["image_coverage"] < 0.02:
        return "blank", 0.9, 300

    # 整页几乎被一张位图覆盖 => 扫描/拍照
    if sig["image_coverage"] > 0.6:
        # 反推原生 DPI：像素宽 / 页面英寸宽
        native_dpi = 0
        if sig["max_image_px_width"]:
            native_dpi = round(sig["max_image_px_width"] / max(sig["size_pt"][0] / 72.0, 0.1))
        # 超过原生分辨率渲染没有意义，但低于 300 时补到 300 有助模型
        dpi = 300 if native_dpi < 300 else min(native_dpi, 600)
        return "scan", 0.9, dpi

    # 大量矢量路径 或 命中音乐字体 => 数字排版
    if sig["n_drawings"] > 150 or sig["music_fonts"]:
        return "vector", 0.9, 400
    if sig["n_drawings"] > 30:
        return "vector", 0.6, 400

    return "scan", 0.5, 300


def probe(pdf_path: str) -> dict:
    doc = pymupdf.open(pdf_path)
    pages = []
    for page in doc:
        sig = _page_signals(page)
        kind, conf, dpi = classify(sig)
        sig.update({"kind": kind, "confidence": conf, "recommended_dpi": dpi})
        pages.append(sig)

    kinds = [p["kind"] for p in pages]
    non_blank = [k for k in kinds if k != "blank"]
    if not non_blank:
        overall = "blank"
    elif all(k == "vector" for k in non_blank):
        overall = "vector"
    elif all(k == "scan" for k in non_blank):
        overall = "scan"
    else:
        overall = "mixed"

    result = {
        "file": str(Path(pdf_path).resolve()),
        "n_pages": len(pages),
        "overall": overall,
        "pages": pages,
        "engine": "oemer",
        "notes": [],
    }
    if overall == "mixed":
        result["notes"].append("该 PDF 混合了矢量页与扫描页，将按页分别选择 DPI。")
    if any(p["rotation"] for p in pages):
        result["notes"].append("检测到页面旋转属性，渲染时会按旋转后方向输出。")
    docs_meta = doc.metadata or {}
    result["pdf_metadata"] = {k: v for k, v in docs_meta.items() if v}
    doc.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF 曲谱分诊：矢量 / 扫描 / 空白")
    ap.add_argument("pdf_path")
    ap.add_argument("--json-out", default=None, help="写入 JSON 的路径")
    args = ap.parse_args()

    if not Path(args.pdf_path).exists():
        print(f"[ERR] 文件不存在: {args.pdf_path}", file=sys.stderr)
        return 2

    res = probe(args.pdf_path)
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
        print(f"[OK] 已写入 {args.json_out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
