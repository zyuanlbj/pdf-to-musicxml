---
name: pdf-to-musicxml
description: 把 PDF 格式的曲谱（印刷扫描件、手机拍照件，或 Sibelius / Finale / MuseScore 导出的矢量排版谱）转成可编辑的 MusicXML，并顺带产出 MIDI 试听文件、重排 PDF 和带逐页对比图与识别告警的校对报告。当用户提供 PDF 乐谱并要求"转成 MusicXML / 转成可编辑谱 / 扒谱 / 导入 MuseScore 编辑 / 生成 MIDI 试听"时使用本技能。也适用于多页曲谱的整册识别。不适用于音频转谱（音频扒谱）或 MusicXML 之间的格式互转。
agent_created: true
---

# PDF 曲谱 → MusicXML

把纸面乐谱变成能编辑的音符数据。核心是光学识谱（OMR）：引擎把 PDF 每页拆成五线谱系统，识别符头、符干、符尾、谱号、调号、拍号、小节线，再还原成带节奏信息的 MusicXML。

引擎用 **Audiveris**（开源 OMR 里最完整的一个，自带 JRE，不依赖 Python 生态和系统 Java）。

## 什么时候用

- 「这份 PDF 谱能转成 MusicXML 吗」「帮我把这个谱扒下来」
- 「我想在 MuseScore / Sibelius 里改这份谱」
- 「识别一下这份谱，我要 MIDI 试听」
- 「这几页谱帮我合成一份完整乐谱」

不适用：音频转谱（那是音高识别，另一条路）、MusicXML ↔ MIDI ↔ LilyPond 之间的互转（MuseScore 直接能做，不需要本技能）。

## 能力边界（实测数据，不是宣传口径）

下面这张表来自真实测试：一份钢琴弹唱谱（A4 扫描件、254dpi、28 小节、大谱表 + 和弦记号 + 英文歌词）。

| 项目 | 结果 |
|---|---|
| 小节数 | 识别 27 / 实际 28（差 1，丢在反复跳房子段） |
| 音符 | 485 个，音高与时值整体可靠 |
| 谱号 / 调号 / 拍号 | 全部正确 |
| 大谱表声部分离 | 正确（单 part、双谱表） |
| 反复记号与 1./2. 房子 | 正确保留 |
| 延音线、连音线、附点 | 正确 |
| **和弦记号** | 21 个 `<harmony>`，但根音常有偏差（Bb 被读成 Gm 之类），**必须逐个核对** |
| **歌词** | 12 行文字，内容基本完整（"It's been a long day without you my friend…"），但有个别字符错认 |
| 装饰音 | 有误报（原谱无 tr，结果里出现了 tr） |

### 歌词是「行级文字」，不是逐音节歌词

这是最容易误判的一点：Audiveris 不产出挂在音符上的 `<lyric>`，而是把谱表下方的歌词
输出成 `<direction><direction-type><words>`——**整行文本 + 位置信息**。

后果：

- 文字内容大体拿得到，导入 MuseScore 后显示为位置正确的文本框；
- 但**不能按音节自动对齐**，要人工拆成音节并挂到音符上；
- 用 `.//lyric` 去统计会得到 0，误以为没识别出来。判断有没有歌词要查 `.//words`。

同一条文本流里还会混进噪声：八度记号 `(8va)` 的括号会被读成一串引号，和弦名也可能重复落进文字行。

### 有真值的矢量件实测（数据最硬的一组）

素材：一份 `.mxl` 真值谱，用 MuseScore 导出成矢量 PDF（3 页）再识别，所以能算**真实准确率**。

| 指标 | 真值 | 识别结果 |
|---|---|---|
| 小节 | 53 | 51 |
| 音符条目（含休止） | 435 | 424 |
| 音高序列匹配 | — | **97.5%** |
| 音高 + 时值匹配 | — | **96.1%** |
| 已对齐部分精确匹配 | — | 418/424 = **98.6%** |
| `divisions` | 12 | 4 |
| 连音（`time-modification`） | 6 | **0** |
| 中文歌词 | 184 个音节 | 0（缺语言包） |

具体缺陷，都值得记：

1. **三连音全部丢失**。识别的 `divisions=4` 在数学上就表示不了 1/3 拍，所以三连音被就近压平成普通音符
   （真值 `0.5 / 0.167 / 0.333` → 变成 `0.75 / 0.25 / 0.5`）。这是**格式精度**问题，不是没看见。
2. **页面末尾的短系统整段丢失**。真值最后一页底部有个 2 小节的收尾系统，识别直接跳到末尾结束。
   短系统、不完整系统是漏识的重灾区。
3. **凭空多出记号**。结果里出现了 `mf`、`f`、`>`、`^` 和「1.」房子括号，原谱并没有。
   误报比漏识别更隐蔽，因为它看起来"合理"。
4. 中文歌词为 0，原因是语言包只有 `eng`，不是不识别。

### 比对准确率时的一个陷阱

不要直接拿两份 MusicXML 的 `<duration>` 相比。`duration` 是**以 `divisions` 为单位**的整数，
真值用 `divisions=12`、Audiveris 输出 `divisions=4`，同一个四分音符一边是 12 一边是 4。
不归一化就会得出「匹配率 0.7%」这种荒谬结论（实际 96%）。

正确做法：先读各自第一个 `<divisions>`，把 `duration / divisions` 换算成"拍"，再比。

### 矢量排版 vs 扫描件（同内容闭环实测）

把上面这份识别结果由 MuseScore 重新导出成矢量 PDF，再跑一次识别，结果：

| 指标 | 扫描件 | 矢量件再识别 |
|---|---|---|
| 小节 | 27 | 27（一致） |
| 音符 | 485 | 485，**逐条 100% 相同（494 条含休止，零差异）** |
| 和弦记号 | 21 | 15 |
| 文字行 | 12 | 10，且错认更多（`0n9 day`、`PM`） |

结论：

1. **矢量输入不会额外损失音符**——闭环 100% 复现，说明音符链路是稳的；
2. 和弦变少是**渲染侧**的损失（MuseScore 没把全部 `<harmony>` 排出来），不是识别退化；
3. 文字行反而变差，因为重排时文字换了字体、字号变小，二次 OCR 更容易错。
   所以「矢量 PDF 一定比扫描件识别得好」这个直觉**不成立**，文字尤其如此。

**影响精度的因素**（按权重排）：

1. 源图有效分辨率——低于 200dpi 就别抱期望，说明书会提前告警；
2. 页面倾斜与透视（拍照件常见）；
3. 音符密集度——十六分音符与密集和弦是错音高发区；
4. 跨页结构——反复、跳房子最容易丢；
5. 文字字号——歌词字号小时 OCR 掉得很快。

把本技能定位成「省掉 80% 打谱工作量的初稿生成器」，不是「一次到位」。

### 中文曲谱：矢量谱走文字层，别走 OCR

**关键结论**：中文歌词靠 OCR 基本没戏。实测把「越过绵绵的高山」读成「」= 骝」「水不」「蒯过」，
即使装好 `chi_sim` 且 `Installed OCR languages: chi_sim,eng` 也照样乱码。

但**矢量排版的 PDF 根本不需要 OCR**——歌词本身就是文本对象，带精确坐标：

```bash
python scripts/extract_lyrics.py "谱子.pdf" -o 输出目录
```

实测结果：184 个字，与真值**逐字完全一致（100%）**，零损失。主流程会自动做这一步，
产出 `lyrics.txt` 并写进报告。

两个必须处理的细节：

1. **康熙部首**：MuseScore 排中文歌词时会混入 U+2F70 `⾼` 这类康熙部首字符（不是 U+9AD8 `高`），
   必须做 NFKC 归一化，否则粘进乐谱显示异常。
2. **音乐字体**：Leland / Bravura 的符号码位在私用区（U+E000 起），必须按字体名排除，
   否则谱号会被当成乱码字符抽出来。

扫描件没有文字层，只能退回 OCR——这时中文歌词建议直接人工录入，别浪费时间调参。

### OCR 语言必须显式指定

装好 `chi_sim` **不会自动生效**。Audiveris 的 OCR 语言来自常量，默认只有 `eng`：

```bash
Audiveris ... -constant org.audiveris.omr.text.Language.defaultSpecification=eng+chi_sim
```

不指定时中文不会被送去 OCR（日志里连 OCR 尝试都没有），表现为「文字行数为 0」。
主流程会从文字层自动判断谱子里有没有中文，有就加上 `eng+chi_sim`；也可用
`--ocr-lang eng+chi_sim` 手动指定。语言包安装见 `references/setup.md`。

**缺语言包时 Audiveris 不报错、只是静默跳过所有文字**，所以很容易误判成「这谱没歌词」。
`report.md` 里文字行数为 0 时会自动打一条提示，别忽略它。

网络不通时手工下载后离线落位：

```bash
python scripts/install_engine.py --tessdata-file "D:/下载/chi_sim.traineddata"
```

脚本会拷到 Audiveris 的 tessdata 目录并校验体积（chi_sim 小于 20 MB 就警告，说明下成了
`_fast`/`_best` 版本，缺 legacy 组件用不了）。

必须用 `tesseract-ocr/tessdata` 仓库里的版本，**不要** `tessdata_fast` / `tessdata_best`。
体积可作判据：`chi_sim` 完整版约 40 MB，`_best` 约 13 MB，`_fast` 约 2 MB。

## 快速开始

```bash
# 0. 体检（首次会提示缺引擎）
python scripts/p2m.py doctor

# 1. 首次：装引擎（下载 85MB，解包到技能目录，不动系统）
python scripts/install_engine.py

# 2. 跑
python scripts/p2m.py run "谱子.pdf"

# 只处理前 3 页先试水
python scripts/p2m.py run "谱子.pdf" --sheets 1-3

# 保留中间文件（排查识别问题时用）
python scripts/p2m.py run "谱子.pdf" --keep-work
```

默认输出到 PDF 同目录的 `<文件名>_musicxml/`：

```
<name>.musicxml      主产物，可直接拖进 MuseScore / Sibelius / Finale
<name>.mxl           Audiveris 原生导出的压缩 MusicXML（备份一份）
<name>.mid           MIDI，用于试听校验
<name>_rendered.pdf  识别结果重新排版后的 PDF，与原谱并排对比
<name>_rendered.png  重排后的首页图
lyrics.txt           矢量谱专供：从文字层无损抽出的歌词（非 OCR）
report.md            校对报告
visuals/             原谱逐页预览图（报告引用）
work/                中间文件（--keep-work 时保留：.omr 工程、日志、分诊 JSON）
```

## 校对方法

不要一上来就翻 MusicXML 源码。按这个顺序查，效率最高：

1. **先听 MIDI**。调性、拍号、节奏错误在听觉上一耳就能暴露，比逐音符核对快得多。
2. **读 `report.md` 的告警段**。Audiveris 的日志会指名道姓报出"第几小节节奏不完整""某符号未识别"，这是最精准的问题定位入口。
3. **看对比图**。原谱预览与重排结果并排，漏行、串行、整体错位一眼可见。
4. **数小节**。重排后总小节数应与原谱一致，缺节通常是漏识别了全休止符或跨行连线。
5. **补人工内容**。和弦根音要逐个核；歌词是整行文字，得手工拆成音节挂到音符上；力度、指法、踏板基本没有，直接手加。
6. **小心装饰音误报**。识别结果偶尔会凭空多出 `tr`、装饰性小音符，看到不认识的符号先怀疑识别而非原谱。

## 流水线各阶段

### 1. 分诊（`scripts/probe.py`）

按页判断是**矢量排版**还是**扫描图像**，并反推扫描页的原生分辨率。用途是提前预警质量风险：有效分辨率低于 200dpi 的页面会在报告里被标红，因为那种源图再怎么处理也识别不准。

判据：整页被一张位图覆盖 → 扫描件；矢量路径数量大或命中 Bravura / Opus / Maestro 等音乐字体 → 数字排版。

### 2. 识谱（Audiveris）

```bash
Audiveris -batch -transcribe -export -output <dir> <input.pdf>
```

要点：

- `-batch` 必须加，否则会弹 GUI；
- 整册 PDF 由 Audiveris **原生多页处理**，不需要手工拼接，跨页的反复结构和声部延续由它自己维护；
- 产出 `.mxl`（压缩 MusicXML）和 `.log`；
- `-sheets 1-3` 可只跑部分页，试水时很有用；**但 `-sheets` 是多值选项，会把后面所有参数都吞成自己的值**，输入文件必须用 `--` 隔开，否则报 `NumberFormatException`；
- 首次运行会慢一些（初始化符号分类器），之后正常；
- 识别歌词和和弦文字依赖 Tesseract 语言包，缺了**不会报错、只是静默丢文字**，体检时要专门看这一项。

### 3. 解包与导出

`.mxl` 本质是个 zip（`META-INF/container.xml` + 真正的乐谱 xml），脚本取出其中最大的 xml 落成普通 `.musicxml`，这样 MuseScore、Sibelius 和文本编辑都能直接吃。

再用 MuseScore 4 CLI 派生：

- `-o x.mid` → MIDI
- `-o x.pdf` → 重排 PDF
- PDF 首页用 PyMuPDF 抓成 PNG（比让 MuseScore 直出 PNG 稳）

### 4. 报告

`report.md` 汇总：逐页分诊表、Audiveris 日志里的 ERROR/WARN 聚合、导出产物状态、原谱与重排的对比图、人工复核要点。

## 调参与排错

```bash
python scripts/p2m.py probe "谱子.pdf"     # 单独看分诊结论
python scripts/p2m.py run "谱子.pdf" --keep-work   # 保留 .omr 工程和日志
```

| 症状 | 原因 | 处理 |
|---|---|---|
| 整页乱码 / 大量错音 | 源图分辨率过低或严重倾斜 | 先用 `probe` 看原生 dpi；低于 200 建议重新扫描 |
| 只识别出几行 | 页面留白/装订线干扰，或谱表间距异常 | 用 `scripts/render.py` 预处理成 PNG 再喂 |
| 声部数不对 | 大谱表被拆成两个声部 | 属正常现象，导入 MuseScore 后合并声部 |
| 卡在某一步不动 | 复杂页耗时较长 | 加大 `--timeout`，或 `--sheets` 分页跑 |
| 找不到引擎 | 未安装 | `python scripts/install_engine.py` |
| MIDI/PDF 没生成 | 未装 MuseScore | 装 MuseScore 4，或设 `MUSESCORE_BIN` |
| 中文路径报错 | 引擎对非 ASCII 路径敏感 | 把 PDF 复制到纯英文路径再跑 |

环境与网络（下载被限速、镜像、代理）的细节见 `references/setup.md`。

## 脚本清单

| 文件 | 职责 |
|---|---|
| `scripts/p2m.py` | 总控。`run` 跑全流程，`doctor` 体检，`probe` 看分诊 |
| `scripts/install_engine.py` | 下载并解包 Audiveris（不改动系统），装 OCR 语言包 |
| `scripts/extract_lyrics.py` | 从矢量 PDF 文字层无损抽歌词（替代 OCR） |
| `scripts/gh_download.py` | 分块并行下载器（应对 GitHub 限速） |
| `scripts/probe.py` | PDF 分诊，判断矢量/扫描并反推 dpi |
| `scripts/render.py` | PDF 渲染 + 预处理（去噪、自动裁边），识别效果差时的兜底入口 |

`probe.py` / `render.py` 都能脱离主流程单独用，排查时很顺手。
