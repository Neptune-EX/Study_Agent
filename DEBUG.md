# 数学错题本调试与维护指南

## 查看模型的每一步回复

在项目目录打开 PowerShell，先停止正在运行的 Streamlit。编辑现有的 .env，加入以下配置，然后重新启动应用。

~~~dotenv
MISTAKE_DEBUG=1
~~~

~~~powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
~~~

用一张小样本复现问题。建议先指定一个题号，每道原题只生成一道练习。识别确认页和错题记录分别显示识别与后台生成的调试编号。每次操作写入一份 data/debug/<编号>.jsonl，可以在编辑器打开。日志只覆盖开启后执行的操作。

以下命令读取最新一份日志。若多人同时使用应用，按界面上的编号打开对应文件。

~~~powershell
$log = Get-ChildItem -LiteralPath .\data\debug -Filter *.jsonl |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$events = Get-Content -LiteralPath $log.FullName -Encoding UTF8 |
    ForEach-Object { ConvertFrom-Json -InputObject $_ }

$events | Format-Table time,event,stage,attempt,elapsed_ms,finish_reason -AutoSize

$events | Where-Object event -eq 'model.response' |
    Format-List stage,call_id,question_id,attempt,reply
~~~

如果只想看复核回复及程序拒绝原因，可以执行下面这段。要追溯这一轮收到的提示词和材料，用同一个 call_id 找到 model.request。

~~~powershell
$events | Where-Object {
    $_.stage -eq 'Flash 快速验算' -or $_.event -eq 'practice.rejected'
} | Format-List event,stage,call_id,question_id,input,reply,reason
~~~

reply 是应用收到的可见回答文本，包含模型返回的题干、答案、解析或复核意见。日志保存请求提示词、文本输入、输出、耗时和返回的用量字段。图片输入仅记录编码长度和摘要。日志不读取 API Key、请求头或模型内部推理字段。请求和回复仍可能包含学生作答、题干和教学大纲，分享前请脱敏。调试结束后将 MISTAKE_DEBUG 改为 0 并重启。

## 从日志判断故障在哪一步

| 现象 | 先看哪里 | 如何判断 |
| --- | --- | --- |
| 找不到指定题号或识别错页 | Flash 识图的 reply，接着看 intake.selection | 对照印刷题号、kind、continuation、requested 和 selected_sources。来源 2-3 表示第 2 页返回的第 3 个片段 |
| 图形题没有被跳过 | Flash 识图、Flash 整理原题的 reply | 对照 kind、requires_figure 以及题干中的读图引用。筛选逻辑在 analyze_upload |
| 关闭页面后想检查生成进度 | 错题记录或 data/mistakes.worker.log | generation_jobs 保存后台状态，generation_checkpoints 保存已完成的模型步骤。浏览器轮询进度，独立进程执行任务 |
| 知识点或难度判断不对 | Flash 识图和 Flash 整理原题 | 对比回复的 knowledge、topic、subtype、difficulty 和 sources。确认页可更正知识点和难度 |
| 有回复但未生成练习 | model.invalid_output、practice.rejected、batch.question_error | 前者说明 JSON 或字段校验失败，后两者说明程序规则、答案复核或原题疑义挡住了出题 |
| 练习答案与题意不符 | Flash 或 Pro 生成练习、Flash 快速验算、practice.rejected | 简单等式由程序代入，其余题才会出现在 Flash 的验算请求中。按 question_id 对照生成与验算结果 |
| 没有 PDF | batch.saved、tex.compiled、tex.error | batch.saved 的 tex_bytes 大于 0 表示可点击「编译 PDF」。tex.error 的 output_tail 包含 XeLaTeX 输出末尾 |
| PDF 已生成但下载失败 | 浏览器开发者工具的 Console | 点击下载后看是否有脚本错误，并检查下载列表。download_file 在浏览器内用已保存的字节创建下载文件 |

同一个 call_id 下的 attempt 表示同一次模型调用因输出格式问题而重试。生成练习、独立解题或复核这一整轮重做时会产生新的 call_id。model.api_error 可显示 HTTP 状态码或连接异常类型。finish_reason 为 length 表示模型输出被截断。调整后的新版本未全部通过或编译失败时，界面会继续提供上一版讲义。

## App 结构与代码入口

~~~mermaid
flowchart TD
    A["上传与设置 app.py"] --> B["渲染 PDF 或读取图片 inputs.py"]
    B --> C["Flash 识图 deepseek.py"]
    C --> D["题号筛选与过滤图形题 workflow.py"]
    D --> E["Flash 整理原题、归纳知识点并判断难度"]
    E --> F["用户编辑并确认，保存原题和任务 store.py"]
    F --> G["后台出题与验算 worker.py"]
    G --> H["保存 TeX 并绑定记录"]
    H --> I["按需编译和下载 PDF"]
~~~

| 文件 | 主要职责 | 从哪里读起 |
| --- | --- | --- |
| [app.py](app.py) | Streamlit 页面、上传对话框、整份调整和记录 | intake_dialog、confirm_upload、practice_fields、batch_dialog、history |
| [inputs.py](mistakes/inputs.py)、[selection.py](mistakes/selection.py) | 页码解析、PDF 渲染、题号范围解析 | read_pages、read_targets |
| [models.py](mistakes/models.py) | 原题、练习、命题设置、图形和复核的结构定义 | Original、Exercise、PracticePlan、Diagram、Review |
| [deepseek.py](mistakes/deepseek.py) | LangChain 调用、各阶段提示词和 JSON 校验 | recognize、recognize_figure、intake、practice、solve、review、_request |
| [workflow.py](mistakes/workflow.py) | 筛题、知识点归纳、生成重试、整份复核和导出 | choose_questions、analyze_upload、check_exercises、generate、finish_batch |
| [store.py](mistakes/store.py) | SQLite 存储、批次与原题关系、旧记录兼容和版本检查 | save_intake、batch_questions、save_batch、batch_result |
| [worker.py](mistakes/worker.py)、[launcher.py](mistakes/launcher.py) | 后台进程、任务执行与独占锁 | start_worker、process_queue、drain_queue |
| [verify.py](mistakes/verify.py) | 对简单四则运算等式做精确代入 | substitution |
| [latex.py](mistakes/latex.py)、[latex_board](latex_board/) | 数学文本排版、TikZ 图形、整份练习、答案分页和编译 | render_batch、draw_diagram、typeset、compile_tex |
| [download.py](mistakes/download.py) | 下载已保存的 PDF 和 LaTeX 文件 | download_file |
| [debug.py](mistakes/debug.py) | 为操作分配编号，写入本地 JSONL | debug_run、debug_context、record |

## 修改与优化功能

### 调整题量、难度和知识点归纳

- 改默认题量或可选难度时，先改 models.py 的 PracticePlan，再同步 app.py 的 practice_fields。调整题量上限时同时检查界面输入范围和 Pydantic 校验范围。
- 改知识点归纳或难度阈值时，用同一个样本对照 Flash 识图和整理原题的输入与输出。判断规则在 deepseek.py 的 intake，出题模型选择在 practice。保持 sources 对应原始片段。
- 改题型、难度或出题风格时，先修改 deepseek.py 的 practice 与 review 两处规则。题量、选项数量、填空占位符和是否允许配图等可确定的约束，在 workflow.py 的 check_exercises 中校验。

### 提高识图和出题准确性

先用相同样本复现，再只修改一个变量。题号读错时，先检查页面渲染和 Flash 转录。题型识别正确但新题偏离考点时，用已入库的原题走「修改记录」，跳过再次上传和识图。改变题量、难度或出题偏好会重新出题。使用相同设置重试会复用已保存的模型步骤和练习，仅处理缺失项并重新编译。

要压缩耗时，可以先按 stage 汇总 elapsed_ms。扫描 PDF 的开销随页数增加，指定页码可减少 Flash 调用。出题和验算按原题顺序执行，改并发前应先验证接口限流、结果顺序和批次写入。

### 改 PDF 留白、答案或配图

新练习填入「应用题错题模板.tex」的题目区，保留原模板的 problem 环境。标题使用两位题号和识别出的知识点。作答留白和每三题换页由模板的 vfill 和 problemcount 控制。答案另起一页。历史记录中的旧图形练习仍可导出。

可以用已保存的批次在本地重新渲染，不调用模型。把批次编号换成日志中 intake.saved 的 batch_id，然后在 PowerShell 执行。

~~~powershell
$env:MISTAKE_BATCH_ID = '替换成批次编号'
@'
import os
from pathlib import Path
from mistakes.latex import compile_batch
from mistakes.store import Store

store = Store(Path("data/mistakes.sqlite3"))
batch = store.get_batch(os.environ["MISTAKE_BATCH_ID"])
questions = store.batch_questions(batch.id)
if not questions or any(not q.exercises for q in questions):
    raise SystemExit("该批次的练习尚未全部生成")
output = Path("data/debug")
output.mkdir(parents=True, exist_ok=True)
pdf, tex = compile_batch(questions, batch.plan)
(output / "preview.tex").write_bytes(tex)
(output / "preview.pdf").write_bytes(pdf)
'@ | .\.venv\Scripts\python.exe -
~~~

如需检查 XeLaTeX 的完整日志，可在 data/debug 目录对 preview.tex 手动运行以下命令。该目录会保留 preview.log，便于搜索首个错误。

~~~powershell
Push-Location .\data\debug
try {
    xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error -file-line-error preview.tex
} finally {
    Pop-Location
}
~~~

如果应用配置了自定义的 XELATEX 路径，在 PowerShell 用调用运算符 & 和实际路径执行同一个命令。

## 修改后检查

~~~powershell
.\.venv\Scripts\python.exe -m compileall -q app.py mistakes
.\.venv\Scripts\python.exe -m pip check
~~~

用一份固定的小样本比较改动前后的日志。至少检查指定题号、整份材料中的两个不同考点、修改识别文字、跳过图形题、关闭浏览器后后台继续、答案另页和浏览器下载。修改数据库结构前先停应用并备份 data/mistakes.sqlite3，用备份副本验证旧记录仍可打开。
