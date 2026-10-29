# 环境准备与依赖

## 依赖总览

| 组件 | 作用 | 必需 | 本机验证版本 |
|---|---|---|---|
| Python 3.9+ | 运行全部脚本 | 是 | 3.13.14 |
| `pymupdf` | PDF 解析、分诊、页面渲染、预览图 | 是 | 2.x |
| `numpy` + `opencv-python-headless` | 图像预处理（去噪、自动裁边） | 仅 `render.py` 兜底路径需要 | 5.x |
| **Audiveris** | 光学识谱核心引擎 | 是 | 5.11.0 |
| MuseScore 4 | MusicXML → MIDI / PDF | 建议 | 4.x |
| curl | 下载引擎安装包 | 装引擎时 | Win10+ 自带 |
| 7-Zip | 解包 .msi | 装引擎时（有则更快） | 已装 |

Audiveris 的 Windows 包**自带 JRE**，所以不需要系统里装 Java，也不挑 Java 版本。

## 安装

```bash
# Python 依赖
python -m pip install pymupdf numpy opencv-python-headless
```

MuseScore 4 从官网下载安装即可，脚本会自动在
`C:\Program Files\MuseScore 4\bin\MuseScore4.exe` 找到它。
装在别处就用环境变量指定：

```bash
export MUSESCORE_BIN="/path/to/MuseScore4"
```

## 安装 Audiveris

```bash
python scripts/install_engine.py
```

它会：

1. 下载 `Audiveris-<ver>-windowsConsole-x86_64.msi`（约 85 MB）到 `engine/_install/`；
2. 用 7-Zip 把 msi 当压缩包解开到 `engine/Audiveris/`（失败则回退 `msiexec /a` 管理式解包）；
3. 跑 `Audiveris -version` 验证能启动。

**不改注册表、不写系统目录**，卸载就是删掉 `engine/` 目录。

已装好时可以用环境变量显式指向：

```bash
export AUDIVERIS_BIN="C:/path/to/Audiveris.exe"
```

### 为什么选 `windowsConsole` 那个包

同一版本有三个 Windows 包：`windows-x86_64.msi`、`windowsConsole-x86_64.msi`、以及 Linux/macOS 包。
`windowsConsole` 是专门为无界面批量处理编译的，跑 `-batch` 时不会有控制台窗口或 GUI 初始化的怪问题。

## 为什么不用 oemer

`oemer`（Python 生态里最常被推荐的 OMR）在本机装好后是跑不起来的，原因值得记下来，省得下次再踩：

1. **权重不在 pip 包里**。首次运行才联网下载，走的是 `urllib` + CONNECT 隧道，在需要代理的环境下直接 `502 Bad Gateway`；而且没有断点续传、没有镜像回退。
2. **权重与新版 onnxruntime 不兼容**。`unet_big/model.onnx` 的 `ConvTranspose` 用了负值 `pads`（老 TF→ONNX 转换的产物），onnxruntime 1.31 直接拒绝加载：
   `Attribute pads must not contain negative values`。而 Python 3.13 只能装 1.20+ 的 onnxruntime，无解。
3. **识别范围窄**。只输出音符层，歌词、和弦记号、反复记号全部丢失——对弹唱谱来说基本没什么用。

Audiveris 三条都不占：自带 JRE、无 Python 依赖、并且会尝试识别歌词与和弦。

## 网络受限环境（下载被限速）

GitHub release 资源在部分出口会被限速：**单连接跑到约 9–10 MB 必被掐断**，
随后速度衰减到 0。实测结论：

- 单流 + `-C -` 续传**不可靠**（常常整块拿不到）；
- **分段请求（`Range`）每次都能拿满**，这是唯一稳定的路子；
- 并发不是越高越好：**4 并发最稳，8 并发会把代理打饱和导致大面积失败**；
- 拉完必须校验总字节数，缺块要单独补——不校验就拼装会得到损坏的安装包。

`scripts/gh_download.py` 就是按这些结论写的，可直接复用：

```bash
python scripts/gh_download.py "<url>" "<目标文件>" --workers 4
```

镜像回退顺序（`gh_download.py` 不含镜像，需要时可手动换 URL 前缀）：

```
https://gh-proxy.com/<原始 github url>
https://ghproxy.net/<原始 github url>
https://ghfast.top/<原始 github url>
```

## pip 装不上包的情况

本机遇到过一个具体现象：`curl` 能正常访问 PyPI 镜像，但 `pip install` 一律报
`Could not find a version that satisfies the requirement ... (from versions: none)`，
连 `--isolated --proxy` 都不行。这种情况下：

- 用 `curl` 直接下 wheel，再 `pip install <本地 whl> --no-deps`（依赖需自行补齐）；
- 或者干脆绕开 Python 依赖——本技能的主链路只强依赖 `pymupdf`。
