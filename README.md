# pdf-to-musicxml

把 PDF 曲谱（印刷扫描件、手机拍照件，或 Sibelius / Finale / MuseScore 导出的矢量排版谱）
转成可编辑的 MusicXML，并顺带产出 MIDI、重排 PDF 和校对报告。

这是 WorkBuddy 的一个技能包（skill），核心是一条可直接跑通的命令行流水线。

[English](README.en.md) | **简体中文**

## 它解决什么问题

纸面乐谱 → 能编辑的音符数据。核心是光学识谱（OMR），引擎用 [Audiveris](https://github.com/Audiveris/audiveris)
（开源 OMR 里最完整的一个，自带 JRE，不依赖 Python 生态或系统 Java）。

> ⚠️ **引擎不在本仓库**。Audiveris 约 163 MB，用 `.gitignore` 排除了。
> 首次使用运行 `scripts/install_engine.py` 自动下载解包，或按
> `references/setup.md` 的说明手动下载后离线安装。

## 能力边界（实测，不是宣传口径）

| 项目 | 结果 |
|---|---|
| 音高序列匹配 | **97.5%**（有真值的矢量谱，53 小节 / 435 音符） |
| 音高 + 时值匹配 | **96.1%** |
| 已对齐部分精确匹配 | 98.6% |
| 谱号 / 调号 / 拍号 / 大谱表 | 正确 |
| 反复记号、1./2. 房子 | 正确 |
| 三连音 | **丢失**（引擎输出 `divisions=4`，表示不了 1/3 拍） |
| 页面末尾的短系统 | 容易整段丢失 |
| 装饰音 / 力度记号 | 有**误报**（会凭空多出 `tr`、`mf`、重音） |
| 歌词（矢量 PDF） | **100% 无损**，从文字层直接抽取，不靠 OCR |
| 歌词（扫描件） | 英文勉强可用；**中文 OCR 基本不可用** |

定位是「省掉 80% 打谱工作量的初稿生成器」，不是一次到位。

## 依赖

| 组件 | 必需 | 说明 |
|---|---|---|
| Python 3.9+ | 是 | 脚本运行环境 |
| `pymupdf` | 是 | PDF 解析、渲染、文字层抽取 |
| `numpy` + `opencv-python-headless` | 否 | 仅 `render.py` 的兜底预处理需要 |
| Audiveris | 是 | 识谱引擎，需单独安装 |
| MuseScore 4 | 建议 | 用于导出 MIDI / PDF |

```bash
python -m pip install pymupdf numpy opencv-python-headless
```

## 快速开始

```bash
# 0. 体检
python scripts/p2m.py doctor

# 1. 装引擎（首次，约 81 MB 下载 + 解包）
python scripts/install_engine.py

# 2. 跑
python scripts/p2m.py run "谱子.pdf"

# 只处理前 3 页
python scripts/p2m.py run "谱子.pdf" --sheets 1-3
```

输出到 PDF 同目录的 `<文件名>_musicxml/`：MusicXML、MIDI、重排 PDF、`lyrics.txt`（矢量谱）、`report.md`。

## 脚本

| 文件 | 职责 |
|---|---|
| `scripts/p2m.py` | 总控。`run` 跑全流程，`doctor` 体检，`probe` 看分诊 |
| `scripts/install_engine.py` | 下载并解包 Audiveris（不改动系统），装 OCR 语言包 |
| `scripts/extract_lyrics.py` | 从矢量 PDF 文字层无损抽歌词（替代 OCR） |
| `scripts/gh_download.py` | 分块并行下载器（应对限速环境） |
| `scripts/probe.py` | PDF 分诊：矢量排版 / 扫描图像，并反推原生 dpi |
| `scripts/render.py` | PDF 渲染 + 预处理（去噪、自动裁边），识别效果差时的兜底 |

## 已知坑（都踩过并向你交代）

1. **`-sheets` 会吞参数**：它是多值选项，会把后面的输入文件路径当成自己的值，
   必须用 `--` 隔开，否则报 `NumberFormatException`。
2. **OCR 语言默认只有 `eng`**：装了 `chi_sim` 也不会自动生效，要显式指定
   `-constant org.audiveris.omr.text.Language.defaultSpecification=eng+chi_sim`。
3. **语言包必须用完整版 `tessdata`**：`tessdata_fast` / `tessdata_best` 缺 legacy 组件，
   会报 `Could not initialize TessBaseAPI languages ... in legacy mode`。
4. **比对准确率要归一化 `divisions`**：两份 MusicXML 的 `duration` 单位不同，
   直接比会得出 0.7% 这种荒谬结论。
5. **中文歌词别指望 OCR**：矢量谱走文字层（100%），扫描件建议人工录入。

更多细节见 `references/setup.md` 与 `SKILL.md`。

## 许可证

未指定。若要开源建议补一个 `LICENSE`（如 MIT）。
