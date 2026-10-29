"""把 PDF 的指定页渲染成 PNG，并按页面类型做针对性预处理。

用法:
    python render.py <input.pdf> --out-dir work/pages [--pages 1-5,8] [--dpi auto|300]

对扫描页：灰度 → 去边 → 轻度去噪 → 自动裁剪四周空白（保留 padding）。
对矢量页：高 DPI 灰度直出，可选自动裁剪。
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pymupdf

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def parse_page_spec(spec: str | None, total: int) -> list[int]:
    """把 "1-5,8,11-" 解析成 1-based 页码列表。"""
    if not spec:
        return list(range(1, total + 1))
    out: list[int] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d*)", token)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else total
            out.extend(range(start, end + 1))
        elif token.isdigit():
            out.append(int(token))
        else:
            raise ValueError(f"无法解析页码片段: {token!r}")
    return sorted({p for p in out if 1 <= p <= total})


def _autocrop(gray: np.ndarray, pad: int) -> np.ndarray:
    """裁掉四周纯白（或纯黑）留白，保留 pad 像素边距。"""
    if cv2 is None:
        return gray
    # 判断前景是深色还是浅色
    dark_ratio = float((gray < 128).mean())
    mask = (gray < 200).astype(np.uint8) if dark_ratio < 0.5 else (gray > 55).astype(np.uint8)
    ys, xs = np.where(mask > 0)
    if len(ys) == 0:
        return gray
    y0, y1 = max(int(ys.min()) - pad, 0), min(int(ys.max()) + pad, gray.shape[0])
    x0, x1 = max(int(xs.min()) - pad, 0), min(int(xs.max()) + pad, gray.shape[1])
    # 裁剪过于激进时（面积掉一半以上）放弃，避免切掉谱面
    if (y1 - y0) * (x1 - x0) < 0.5 * gray.size:
        return gray
    return gray[y0:y1, x0:x1]


def _clean_scan(gray: np.ndarray) -> np.ndarray:
    """扫描件轻预处理：去噪 + 对比度拉伸。不做二值化，保留灰度给模型。"""
    if cv2 is None:
        return gray
    denoised = cv2.fastNlMeansDenoising(gray, None, h=8, templateWindowSize=7, searchWindowSize=21)
    return cv2.normalize(denoised, None, 0, 255, cv2.NORM_MINMAX)


def render_page(
    pdf_path: str,
    page_no: int,
    out_path: Path,
    dpi: int,
    kind: str,
    autocrop: bool = True,
) -> dict:
    doc = pymupdf.open(pdf_path)
    page = doc[page_no - 1]
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)

    if kind == "scan":
        img = _clean_scan(img)
    if autocrop:
        img = _autocrop(img, pad=int(dpi * 0.12))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if cv2 is not None:
        ok = cv2.imwrite(str(out_path), img)
    else:
        pymupdf.Pixmap(pymupdf.csGRAY, img.shape[1], img.shape[0], img.tobytes(), 0).save(str(out_path))
        ok = True
    doc.close()
    if not ok:
        raise RuntimeError(f"写入失败: {out_path}")
    return {"page": page_no, "path": str(out_path), "dpi": dpi, "kind": kind,
            "w": int(img.shape[1]), "h": int(img.shape[0])}


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF 页面渲染 + 预处理")
    ap.add_argument("pdf_path")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--pages", default=None, help='例如 "1-5,8"；缺省为全部')
    ap.add_argument("--dpi", default="auto", help="auto 或具体数值（默认 auto，按页类型定）")
    ap.add_argument("--no-autocrop", action="store_true")
    args = ap.parse_args()

    from probe import classify, _page_signals
    doc = pymupdf.open(args.pdf_path)
    total = len(doc)
    targets = parse_page_spec(args.pages, total)
    results = []
    for pno in targets:
        sig = _page_signals(doc[pno - 1])
        kind, _conf, auto_dpi = classify(sig)
        if kind == "blank":
            print(f"[SKIP] p{pno} 判定为空白页")
            continue
        dpi = auto_dpi if args.dpi == "auto" else int(args.dpi)
        out = Path(args.out_dir) / f"page_{pno:03d}.png"
        info = render_page(args.pdf_path, pno, out, dpi, kind, not args.no_autocrop)
        print(f"[OK] p{pno} {kind} dpi={dpi} {info['w']}x{info['h']} -> {out.name}")
        results.append(info)
    doc.close()
    print(f"[DONE] {len(results)} 页已渲染到 {args.out_dir}")
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
