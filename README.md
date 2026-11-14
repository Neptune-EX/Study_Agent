# 数学错题本

上传数学题照片或 PDF，收录选择题、填空题和文字应用题。系统跳过图形题及依赖配图的题。一次上传生成一份讲义，按 [现有 TeX 模板](latex_board/) 为同类练习保留作答留白，末尾另页集中放练习的答案和解析。

## 启动

在项目目录打开 PowerShell。首次使用需要 Python 3.11 或以上版本，以及含 CTeX、TikZ、tcolorbox 和中文字体的 XeLaTeX 环境。

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
~~~

在 .env 中填写自己的 DEEPSEEK_API_KEY。XeLaTeX 已加入 PATH 时，保留默认的 XELATEX=xelatex。也可以把 XELATEX 设为 xelatex.exe 的绝对路径。

~~~powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
~~~

打开 <http://127.0.0.1:8501>。

## Windows EXE 打包

桌面版用 `MathMistakeBook.spec` 打包 `launch.py`，生成 `MathMistakeBook.exe` 及其运行依赖。脚本另将 Windows TeX Live 复制到 EXE 同级的 `texlive`，供发布版编译 PDF。

### GitHub 仓库最少保留什么

| 内容 | 需保留的文件 |
| --- | --- |
| 构建入口和依赖 | `build.ps1`、`MathMistakeBook.spec`、`requirements.txt`。请把 `MathMistakeBook.spec` 纳入 Git，新 clone 才会带上它。 |
| 应用及后台任务 | `launch.py`、`app.py`、`paths.py`、`pyi_runtime.py` 和整个 `mistakes/` 源码目录。 |
| 随包资源 | `.streamlit/config.toml`、`latex_board/应用题错题模板.tex`、`latex_board/图形题错题模板.tex`。可继续保留 `latex_board/` 根目录的其他模板。 |

`.env.example` 可保留供源码版配置使用。`.env`、历史数据库 `data/`、`.venv/`、`build/`、`dist/`、`build-exe.log` 和旧的 `collect_deps.py` 都无需上传。`latex_board/files/` 和 `latex_board/Picture/` 中的示例文件可按需保留。

`texlive/` 可以从 Git 索引移除并留在本机。若之前已跟踪它，在项目根目录执行 `git rm -r --cached -- texlive`，然后提交索引中的删除和 `.gitignore`。这个命令会保留本地文件，旧提交中已经保存的 TeX Live 仍会留在 Git 历史里。 [GitHub 忽略文件说明](https://docs.github.com/en/get-started/git-basics/ignoring-files)

### 从源码构建

在项目根目录打开 PowerShell，使用 Windows Python 3.11 或以上版本。先创建或复用本机虚拟环境，并安装依赖。

~~~powershell
if (-not (Test-Path .\.venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
~~~

准备一套可用的 Windows TeX Live 安装目录，含 `bin/windows/`、宏包树 `texmf-dist/`、安装生成的配置和格式文件，以及中文字体。应保留安装目录的完整结构。新 clone 可把原有便携安装目录放回项目的 `texlive/`，也可使用 `-TeXLivePath` 指向另一处安装目录。重新安装时可在 TeX Live 安装器中选择便携模式，安装 XeLaTeX、CTeX、TikZ、tcolorbox 和中文字体。只有 `xelatex.exe` 时，仍缺少 DLL、宏包和 PDF 转换程序。 [TeX Live 官方安装说明](https://tug.org/texlive/acquire-netinstall.html)

用 `-CheckOnly` 检查本机的 PyInstaller、spec 和 TeX Live 关键文件。检查通过后执行第二条命令开始打包。

~~~powershell
.\build.ps1 -CheckOnly
.\build.ps1
~~~

如果 TeX Live 在项目之外，检查和构建都传入实际的安装根目录，例如 `.\build.ps1 -TeXLivePath 'C:\texlive\2026'`，检查时再加 `-CheckOnly`。构建时无需 DeepSeek API Key。

若 PowerShell 的执行策略拦截脚本，可用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1` 为本次打包运行脚本。

打包完成后，双击 `dist\MathMistakeBook\MathMistakeBook.exe` 验证启动。发布时请分发整个 `dist\MathMistakeBook` 目录。首次启动时在窗口里填写自己的 DeepSeek API Key。EXE 将配置写入 %LOCALAPPDATA%\MathMistakeBook\.env，数据库写入同目录下的 data。下载 PDF 或 LaTeX 时，在「选择保存位置」对话框中浏览文件夹或粘贴完整路径，也可以修改文件名。需要迁移源码版历史时，完全退出应用后，将旧的 data/mistakes.sqlite3 复制到 EXE 版的 data 目录。

入口启动错误可查看 %LOCALAPPDATA%\MathMistakeBook\desktop.log，后台生成和编译错误可查看 data/mistakes.worker.log。EXE 也支持通过 --server-only 8524 启动本地浏览器调试入口。

## macOS 桌面版打包方案

以下是适配后在 Mac 上执行的步骤。目标产物是可从 Finder 双击打开的 `MathMistakeBook.app`。PyInstaller 需要在 macOS 上运行。Apple 芯片使用 `arm64`，Intel 芯片使用 `x86_64`。要兼容两种 Mac，可以分别打包；单个 `universal2` 版本需要 Python 和随包分发的原生依赖都提供所需的架构。 [PyInstaller 平台说明](https://pyinstaller.org/en/stable/) · [macOS 架构说明](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support)

### 需要适配的地方

| 位置 | macOS 需要做的调整 |
| --- | --- |
| [MathMistakeBook.spec](MathMistakeBook.spec)、[build.ps1](build.ps1) | 从现有 spec 派生一个 `MathMistakeBook.mac.spec`，保留 `launch.py`、Streamlit 配置、TeX 模板和运行时钩子。在 `COLLECT` 后用 `BUNDLE(coll, name="MathMistakeBook.app", bundle_identifier="…")` 生成窗口应用，填写自己的包标识符。构建命令改在 macOS 终端执行。 [PyInstaller 的 macOS spec 说明](https://pyinstaller.org/en/stable/spec-files.html#spec-file-options-for-a-macos-bundle) |
| [paths.py](paths.py) | 给打包版的 `APP_HOME` 加 macOS 分支，例如 `~/Library/Application Support/MathMistakeBook/`。将 `.env`、`data/mistakes.sqlite3` 和 `desktop.log` 写到这个可写目录，随包资源继续从 `BUNDLE` 读取。 [Apple 应用数据目录说明](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/MacOSXDirectories/MacOSXDirectories.html) |
| [paths.py](paths.py) 中的 `find_xelatex()` | macOS 可采用外部 MacTeX。优先读取用户配置的 `XELATEX`，默认检查 `/Library/TeX/texbin/xelatex`，然后查找 `PATH`。Finder 启动时的 `PATH` 与终端不同。目标电脑也需要安装 MacTeX，以提供 XeLaTeX、宏包和中文字体。若要随包提供 PDF 编译环境，则需提供 macOS 版 TeX Live 及其宏包和字体。 [MacTeX](https://tug.org/mactex/) · [PyInstaller 的 Finder 环境说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#macos) |
| [launch.py](launch.py)、[mistakes/launcher.py](mistakes/launcher.py)、[mistakes/worker.py](mistakes/worker.py) | 桌面窗口继续用 pywebview，在 Mac 的虚拟环境确认 Cocoa、WebKit 等 PyObjC 依赖能导入并随包收集。后台进程已有 Unix 启动和文件锁分支，需从 Finder 实测 Streamlit 启动、`--worker` 派发和应用退出后的任务处理。 [pywebview 安装说明](https://pywebview.flowrl.com/guide/installation.html#macos) |
| [mistakes/download.py](mistakes/download.py) | 保存对话框可沿用。把 Windows 路径示例改成 macOS 路径，例如 `~/Downloads`。如需「打开保存目录」，macOS 可用 `open` 打开 Finder。 |

### 适配后的构建与发布

1. 在 Mac 上安装符合目标架构的 Python 3.11 或以上版本，并安装 MacTeX 验证 PDF 编译。在项目根目录用 macOS Python 创建虚拟环境，安装依赖。

   ~~~sh
   python3 -m venv .venv
   ./.venv/bin/python -m pip install -r requirements.txt
   ./.venv/bin/python -c 'import platform; print(platform.machine())'
   /Library/TeX/texbin/xelatex --version
   ~~~

2. 完成上述适配并保存 macOS spec 后，在同一目录执行打包。以下命令以 `MathMistakeBook.mac.spec` 为例。

   ~~~sh
   ./.venv/bin/python -m PyInstaller --clean --noconfirm --distpath dist/macos MathMistakeBook.mac.spec
   open dist/macos/MathMistakeBook.app
   ~~~

3. 从 Finder 启动，依次验证首次填写 API Key、上传错题、后台出题、中文 PDF 编译和 TeX 下载。重启后检查配置和错题记录能否读回。使用外部 MacTeX 的版本，发布时应注明目标电脑需要安装 MacTeX。
4. 向其他用户直接分发时，用 `Developer ID Application` 证书签名并启用 Hardened Runtime，将应用打成 ZIP 或 DMG，提交 Apple 公证。完成后在目标 Mac 上用下载的发行包验证能否打开。 [Apple 打包说明](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution) · [Apple 公证说明](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

## 使用

1. 在「录入错题」上传 PNG、JPEG、WebP、GIF 或 PDF。单文件最大 32 MB，GIF 识别首帧。
2. 在上传对话框选择年级和识别范围。题号可写 2,5-7 或 3:9（第 3 页第 9 题）。也可选择整份文件。需要限制指定题号的 PDF 扫描范围时，填写 1,3-5 等页码。每次最多检查 12 页。可设置练习数、难度和补充要求。
3. 点击「识别题目与知识点」，可直接修改每题的识别文字、知识点、难度和选择题选项。确认后任务进入后台队列，页面可以关闭。
4. 确认录入时可为记录命名。在「错题记录」可按名称、文件名、题干或知识点搜索，按年级和上传日期筛选，也可改名、修改题目或删除整次记录。
5. 在「分类复习」查看各知识点的原题和练习，并标记为已解决或未解决。下方「整份记录 · AI 对话」会询问本次复习的反馈。可以回复「只按未解决的题型再出 3 道」「给时间换算出 2 道变式」或「这次先不加题」。也可展开「快捷追加练习」，选择仅未解决题型、指定题型或整份记录，设置同类练习、更难的题或变式。追加题数是本次总数，每次 1–6 道。
6. 追加成功后，系统将新题加入原题和已有练习，更新记录的 TeX。可直接下载新版 LaTeX，或点击「编译 PDF」生成新版 PDF，答案与解析在末尾。旧记录可用「用原题和已存练习更新 TeX」补入原题。

## 处理流程

- PDF 在本地逐页转成图片。LangChain 调用 DeepSeek Flash 读取印刷题号、题干、作答形式及是否需要读图。系统在整理原题和出题前过滤图形题。
- Flash 整理原题并判断难度。基础题用 Flash 出题，奥数和压轴题用 Pro。旧记录未分级的题在下次出题时默认用 Flash，可在「修改记录」中改为进阶。可识别的四则等式由程序验算，其余练习由对应模型按题号复核。答案错时修正答案与解析，题设有问题时修正题设，再重新验算。只有通过的练习才会保存。
- 新讲义将题号、知识点和题干填入原 TeX 模板，保留弹性留白及每三题换页。答案与解析另起一页。
- 独立后台进程从 SQLite 领取任务并保存已完成的模型步骤。短暂网络错误会自动重试，进程异常退出时可从检查点继续。PDF 编译任务只运行 XeLaTeX。
- 错题、图形裁片、练习和导出文件保存在 data/mistakes.sqlite3。备份该文件即可保存历史。图片及转录内容在处理时会发送到配置的 DeepSeek API。

默认模型为 deepseek-flash 和 deepseek-v4-pro，可在 .env 中调整。模型接口详见 [DeepSeek 视觉文档](https://api-docs.deepseek.com/guides/vision/) 和 [模型说明](https://api-docs.deepseek.com/quick_start/pricing/)。

## 调试与维护

查看中间模型回复、定位复核与编译失败、修改出题和排版规则，见 [调试与维护指南](DEBUG.md)。历次功能变化见 [更新记录](UPDATE.md)。
