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

## 使用

1. 在「录入错题」上传 PNG、JPEG、WebP、GIF 或 PDF。单文件最大 32 MB，GIF 识别首帧。
2. 在上传对话框选择年级和识别范围。题号可写 2,5-7 或 3:9（第 3 页第 9 题）。也可选择整份文件。需要限制指定题号的 PDF 扫描范围时，填写 1,3-5 等页码。每次最多检查 12 页。可设置练习数、难度和补充要求。
3. 点击「识别题目与知识点」，可直接修改每题的识别文字、知识点、难度和选择题选项。确认后任务进入后台队列，页面可以关闭。
4. 确认录入时可为记录命名。在「错题记录」可按名称、文件名、题干或知识点搜索，按年级和上传日期筛选，也可改名、修改题目或删除整次记录。
5. 在单条记录的「分类复习」中选择录入时归纳的知识点类别。该类原题和练习共用一个 AI 对话栏，可以提问、发解题过程或追加同类练习。对话和解决状态随类别保存。掌握后可标记整类为「已解决」。
6. TeX 会随记录保存。点击「编译 PDF」即可将原题和累计练习导出，答案与解析在末尾。每次追加练习都会更新 TeX，下一次编译直接使用新版。旧记录可用「用原题和已存练习更新 TeX」补入原题。

## 处理流程

- PDF 在本地逐页转成图片。LangChain 调用 DeepSeek Flash 读取印刷题号、题干、作答形式及是否需要读图。系统在整理原题和出题前过滤图形题。
- Flash 整理原题并判断难度。基础题用 Flash 出题，奥数和压轴题用 Pro。旧记录未分级的题在下次出题时默认用 Flash，可在「修改记录」中改为进阶。程序对可识别的四则运算等式做精确代入检查，其他题由 Flash 一次批量验算。出题最多尝试三轮。
- 新讲义将题号、知识点和题干填入原 TeX 模板，保留弹性留白及每三题换页。答案与解析另起一页。
- 独立后台进程从 SQLite 领取任务并保存已完成的模型步骤。短暂网络错误会自动重试，进程异常退出时可从检查点继续。PDF 编译任务只运行 XeLaTeX。
- 错题、图形裁片、练习和导出文件保存在 data/mistakes.sqlite3。备份该文件即可保存历史。图片及转录内容在处理时会发送到配置的 DeepSeek API。

默认模型为 deepseek-flash 和 deepseek-v4-pro，可在 .env 中调整。模型接口详见 [DeepSeek 视觉文档](https://api-docs.deepseek.com/guides/vision/) 和 [模型说明](https://api-docs.deepseek.com/quick_start/pricing/)。

## 调试与维护

查看中间模型回复、定位复核与编译失败、修改出题和排版规则，见 [调试与维护指南](DEBUG.md)。
