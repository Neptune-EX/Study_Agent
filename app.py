from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import ValidationError
import streamlit as st

from mistakes.debug import debug_run, record
from mistakes.deepseek import DeepSeek, ModelError
from mistakes.download import download_file
from mistakes.inputs import IMAGE_TYPES, MAX_PAGES, MAX_UPLOAD_MB
from mistakes.latex import ORIGINALS_MARKER, CompileError, render_batch
from mistakes.launcher import start_worker
from mistakes.models import DIFFICULTY_LABELS, Original, PracticePlan
from mistakes.selection import read_targets
from mistakes.store import Batch, Question, Store
from mistakes.workflow import IntakeRecord, analyze_upload, needs_figure

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
ERRORS = (ModelError, CompileError, ValueError, OSError, sqlite3.Error)
GRADES = ["", "一年级", "二年级", "三年级", "四年级", "五年级", "六年级", "七年级", "八年级", "九年级",
          "高一", "高二", "高三"]
LEVELS = ["巩固基础", "与原题相当", "适度提高"]
LOCAL_TIME = ZoneInfo("Asia/Shanghai")


def upload(store: Store):
    st.title("录入错题")
    st.caption("上传图片或 PDF，确认 AI 归纳的考点，再生成同类练习。")
    with st.container(border=True):
        uploaded = st.file_uploader(
            "题目文件", type=["pdf", *sorted(suffix[1:] for suffix in IMAGE_TYPES)],
            help=f"支持扫描件、照片和 PDF。单文件最大 {MAX_UPLOAD_MB} MB。",
        )
        if uploaded is None:
            for key in ("upload_id", "upload_at", "upload_open", "upload_preview", "upload_batch"):
                st.session_state.pop(key, None)
            return

        if uploaded.file_id != st.session_state.get("upload_id"):
            st.session_state.update({
                "upload_id": uploaded.file_id, "upload_open": True,
                "upload_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f"),
            })
            st.session_state.pop("upload_preview", None)
            st.session_state.pop("upload_batch", None)
        if st.session_state.get("upload_batch"):
            st.success("已保存到错题记录。")
            return
        if not st.session_state.get("upload_open") and st.button("继续录入", type="primary"):
            st.session_state["upload_open"] = True
    if st.session_state.get("upload_open"):
        intake_dialog(store, uploaded)


def close_intake_dialog():
    st.session_state["upload_open"] = False


def practice_fields(default: PracticePlan) -> PracticePlan:
    left, right = st.columns(2)
    count = left.number_input("每道原题的练习数", 1, 6, default.count)
    difficulty = right.selectbox("难度", LEVELS, index=LEVELS.index(default.difficulty))
    instructions = st.text_area(
        "补充要求（选填）", default.instructions, max_chars=1500,
        placeholder="例如 用小学生能理解的方法解析，重点练习单位换算。",
    )
    return PracticePlan(count=count, difficulty=difficulty, instructions=instructions,
                        allow_diagrams=default.allow_diagrams)


def review_fields(original: Original, key: str, *, defer_by_default: bool = False) -> tuple[dict, bool]:
    label = f"第 {original.number} 题" if original.number else "原题"
    source = original.sources[0]
    page = source.split("-", 1)[0]
    with st.expander(f"第 {page} 页 · {label} · {original.knowledge or original.topic}",
                     expanded=not defer_by_default or bool(original.uncertainty)):
        if original.uncertainty:
            st.warning(original.uncertainty)
        defer = bool(original.uncertainty) and st.checkbox("这道题稍后核对", value=defer_by_default,
                                                          key=f"{key}-defer")
        knowledge = st.text_input("知识点", original.knowledge or original.topic, max_chars=120, key=f"{key}-topic")
        levels = [None, *DIFFICULTY_LABELS] if original.difficulty is None else list(DIFFICULTY_LABELS)
        difficulty = st.radio(
            "原题难度", levels, index=levels.index(original.difficulty),
            format_func=lambda value: DIFFICULTY_LABELS.get(value, "未分级 · Flash"),
            key=f"{key}-difficulty", horizontal=True,
        )
        statement = st.text_area("识别的题干", original.statement, max_chars=4000, key=f"{key}-statement")
        options = original.options
        if original.kind == "choice":
            options = st.text_area("选项，每行一项", "\n".join(options), key=f"{key}-options").splitlines()
        data = {
            **original.model_dump(), "statement": statement, "difficulty": difficulty,
            "options": [option for option in options if option.strip()],
            "uncertainty": "",
        }
        if knowledge.strip() != (original.knowledge or original.topic):
            data["knowledge"] = data["topic"] = knowledge
            data["subtype"] = ""
        return data, defer


def confirm_original(data: dict) -> Original:
    original = Original.model_validate(data)
    if "[未见题干]" in original.statement or not original.statement.strip("_ \t"):
        raise ValueError("请补全原题题干。")
    if original.kind == "choice" and len(original.options) < 2:
        raise ValueError("选择题至少需要两个选项。")
    if original.kind == "blank" and "___" not in original.statement:
        raise ValueError("请在填空题中用 ______ 标出空位。")
    return original


@st.dialog("录入错题", width="large", on_dismiss=close_intake_dialog)
def intake_dialog(store: Store, uploaded):
    st.caption(uploaded.name)
    if preview := st.session_state.get("upload_preview"):
        confirm_upload(store, uploaded, *preview)
        return

    st.subheader("选择识别范围")
    st.caption("图形题和需要读图的题会自动跳过。")
    scope = st.radio("错题范围", ["指定题号", "整份都是错题"], horizontal=True, key="upload_scope")
    whole = scope == "整份都是错题"
    with st.form(f"analyze-{uploaded.file_id}"):
        left, right = st.columns(2)
        grade = left.selectbox("年级", GRADES, format_func=lambda grade: grade or "请选择")
        selection = "" if whole else right.text_input("错题题号", placeholder="例如 2,5-7 或 3:9",
                                                        help="3:9 表示第 3 页的第 9 题。")
        pages = ""
        if uploaded.name.lower().endswith(".pdf") and not whole:
            pages = st.text_input("PDF 页码范围（选填）", placeholder="例如 1,3-5",
                                  help=f"留空检查全部页面，指定题号时最多检查 {MAX_PAGES} 页。")
        plan = practice_fields(PracticePlan())
        submitted = st.form_submit_button("识别题目与知识点", type="primary")
    if not submitted:
        return
    try:
        targets = read_targets(selection, whole)
        with debug_run("analyze_upload", filename=uploaded.name, pages=pages, whole=whole) as run_id, \
                st.status("正在识别并归纳知识点", expanded=True) as status:
            if run_id:
                st.caption(f"调试编号 {run_id}")
            records, warnings = analyze_upload(DeepSeek(), uploaded.name, uploaded.getvalue(), pages,
                                               grade, targets, st.write)
            status.update(label="识别完成", state="complete")
    except ERRORS as exc:
        st.error(str(exc))
        return
    st.session_state["upload_preview"] = (records, warnings, grade, plan, run_id)
    st.rerun()


def confirm_upload(store: Store, uploaded, records: list[IntakeRecord], warnings: list[str],
                   grade: str, plan: PracticePlan, analysis_run_id: str | None):
    st.subheader("确认题目与知识点")
    st.caption(f"{grade} · 识别到 {len(records)} 道题")
    if analysis_run_id:
        st.caption(f"识别调试编号 {analysis_run_id}")
    for warning in warnings:
        st.warning(warning)
    if st.button("返回修改识别范围"):
        st.session_state.pop("upload_preview", None)
        st.rerun()
    with st.form(f"confirm-{uploaded.file_id}"):
        title = st.text_input("记录名称", Path(uploaded.name).stem[:120], max_chars=120)
        rows = [review_fields(original, f"{uploaded.file_id}-{index}")
                for index, (original, *_) in enumerate(records)]
        submitted = st.form_submit_button("确认并生成练习", type="primary")
    if not submitted:
        return
    try:
        if not title.strip():
            raise ValueError("请输入记录名称。")
        confirmed = [
            record if defer else (confirm_original(data), *record[1:])
            for (data, defer), record in zip(rows, records)
        ]
    except (ValidationError, ValueError) as exc:
        st.error(str(exc))
        return
    try:
        with debug_run("confirm_upload", filename=uploaded.name) as run_id:
            batch_id = store.save_intake(
                uploaded.name, confirmed, grade, "", plan, uploaded_at=st.session_state["upload_at"],
                queue_generation=True, title=title,
            )
            record("intake.saved", batch_id=batch_id, count=len(confirmed))
            st.session_state["upload_batch"] = batch_id
            start_worker(store.path, submitted=True)
    except ERRORS as exc:
        st.error(str(exc))
        return
    st.session_state["history_notice"] = (batch_id, warnings, run_id)
    st.session_state["last_batch"] = batch_id
    st.session_state["open_history"] = True
    st.session_state.pop("upload_preview", None)
    close_intake_dialog()
    st.rerun()


@st.dialog("修改错题记录", width="large")
def batch_dialog(store: Store, batch: Batch):
    questions = store.batch_questions(batch.id)
    with st.form(f"edit-{batch.id}"):
        title = st.text_input("记录名称", batch.title or Path(batch.filename).stem[:120], max_chars=120)
        plan = practice_fields(batch.plan)
        pending = []
        for question in questions:
            if not needs_figure(question):
                data, defer = review_fields(question.original, question.id, defer_by_default=True)
                if not defer:
                    pending.append((question, data))
        save = st.form_submit_button("保存修改", type="primary")
        retry = st.form_submit_button("继续生成未完成的题目")
    if not (save or retry):
        return
    try:
        if not title.strip():
            raise ValueError("请输入记录名称。")
        corrected = []
        for question, data in pending:
            if data["statement"] != question.original.statement:
                data["subtype"] = ""
            original = confirm_original(data)
            if original != question.original:
                corrected.append((question, original, question.figure_description))
        if plan != batch.plan or corrected or retry:
            store.queue_edit(batch, plan, corrected, title=title)
            start_worker(store.path, submitted=True)
        else:
            store.rename_batch(batch.id, title)
    except ERRORS as exc:
        st.error(str(exc))
        return
    st.rerun()


@st.dialog("删除错题记录")
def delete_dialog(store: Store, batch: Batch):
    st.write(f"删除「{batch.title or batch.filename}」及其对话、练习、TeX 和 PDF？")
    if st.button("确认删除", type="primary"):
        try:
            store.delete_batch(batch.id)
        except ERRORS as exc:
            st.error(str(exc))
            return
        st.session_state.pop("batch_selection", None)
        st.rerun()


def upload_time(created_at: str) -> str:
    moment = datetime.fromisoformat(created_at)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LOCAL_TIME).strftime("%Y-%m-%d %H:%M:%S")


def history(store: Store):
    st.title("错题记录")
    all_batches = store.batches()
    search_col, grade_col, date_col = st.columns([2, 1, 1])
    keyword = search_col.text_input("搜索记录", placeholder="名称、文件名或知识点").strip().casefold()
    grades = list(dict.fromkeys(batch.grade for batch in all_batches if batch.grade))
    grade = grade_col.selectbox("年级", ["全部年级", *grades])
    date = date_col.date_input("上传日期", value=None)
    matches = store.search_batches(keyword) if keyword else None
    batches = {
        batch.id: batch for batch in all_batches
        if (grade == "全部年级" or batch.grade == grade)
        and (date is None or upload_time(batch.created_at).startswith(date.isoformat()))
        and (matches is None or batch.id in matches)
    }
    if not batches:
        st.info("没有符合条件的错题记录。" if all_batches else "录入错题后，练习和源文件会保存在这里。")
        return
    recent = st.session_state.pop("last_batch", None)
    if recent in batches:
        st.session_state["batch_selection"] = recent
    left, right = st.columns([1, 2])
    with left:
        batch_id = st.selectbox("选择记录", list(batches), key="batch_selection",
                                format_func=lambda key: f"{batches[key].title or batches[key].filename}"
                                f" · {upload_time(batches[key].created_at)}")
    with right:
        batch_details(store, batch_id)


@st.fragment(run_every=4)
def batch_details(store: Store, batch_id: str):
    batch = store.get_batch(batch_id)
    job = store.latest_job(batch_id)
    active = job is not None and job.state in {"queued", "running"}
    if active:
        start_worker(store.path)
    questions = store.batch_questions(batch_id)
    exercises = [exercise for question in questions for exercise in question.exercises]
    pdf, tex, warnings = store.batch_result(batch_id)
    notice = st.session_state.get("history_notice")
    if notice and notice[0] == batch_id:
        warnings = list(dict.fromkeys([*warnings, *notice[1]]))
    with st.container(border=True):
        heading, edit, delete = st.columns([4, 2, 1], vertical_alignment="center")
        heading.subheader(batch.title or batch.filename)
        if edit.button("修改记录", width="stretch", disabled=active):
            batch_dialog(store, batch)
        if delete.button("删除", width="stretch", disabled=active):
            delete_dialog(store, batch)
        if active:
            st.info(f"后台处理中 · {job.message}")
        elif job and job.state == "done":
            st.success(job.message)
        elif job and job.state == "needs_attention" and job.message not in warnings:
            warnings.append(job.message)
        if job and job.debug_id:
            st.caption(f"生成调试编号 {job.debug_id}")
        if notice and notice[0] == batch_id and notice[2]:
            st.caption(f"调试编号 {notice[2]}")
        if pdf or tex:
            pdf_col, tex_col = st.columns(2)
            if pdf:
                with pdf_col:
                    download_file(pdf, f"practice-{batch_id[:8]}.pdf", "下载 PDF", "application/pdf")
            if tex:
                with tex_col:
                    download_file(tex, f"practice-{batch_id[:8]}.tex", "下载 LaTeX", "text/plain")
                if not pdf and pdf_col.button("编译 PDF", disabled=active, width="stretch"):
                    try:
                        store.queue_pdf(batch)
                        start_worker(store.path, submitted=True)
                    except ERRORS as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
        pending = sum(not question.exercises and not needs_figure(question) for question in questions)
        if pending and not active:
            st.caption(f"{pending} 道原题尚未生成。可在「修改记录」中核对或重试。")
        if questions and (not tex or ORIGINALS_MARKER not in tex) and st.button("用原题和已存练习更新 TeX",
                                                                                   disabled=active):
            try:
                prepared = [question for question in questions
                            if question.exercises or (not needs_figure(question) and not question.original.uncertainty)]
                if not prepared:
                    raise ValueError("这份记录没有可导出的题。")
                store.restore_tex(batch, render_batch(prepared, batch.plan, True).encode("utf-8"),
                                  replace_existing=True)
            except ERRORS as exc:
                st.error(str(exc))
            else:
                st.rerun()
        if warnings:
            with st.expander("处理详情", expanded=not bool(pdf)):
                for message in warnings:
                    st.warning(message)
        if not exercises and not active:
            st.write("这次还没有生成练习。")
        category_study(store, batch, questions, active)


def category_study(store: Store, batch: Batch, questions: list[Question], active: bool):
    categories: dict[str, list[Question]] = {}
    for question in questions:
        if not needs_figure(question):
            categories.setdefault(question.category, []).append(question)
    if not categories:
        return
    st.divider()
    st.subheader("分类复习")
    mastered = store.mastered_categories(batch.id)
    st.caption(f"已解决 {len(mastered.intersection(categories))} / {len(categories)} 类")
    category = st.selectbox("选择知识点类别", list(categories), key=f"study-category-{batch.id}",
                            format_func=lambda name: f"{name} · {'已解决' if name in mastered else '未解决'}")
    members = categories[category]
    solved = category in mastered
    if st.button("标记此类为未解决" if solved else "标记此类为已解决", key=f"mastered-{batch.id}-{category}"):
        store.set_category_mastered(batch.id, category, not solved)
        st.rerun()
    with st.expander(f"此类原题和练习（{len(members)} 道原题）"):
        for index, question in enumerate(members, 1):
            st.markdown(f"**原题 {index}**  {escape_markdown(question.original.statement)}")
            for option_index, option in enumerate(question.original.options):
                st.markdown(f"{chr(65 + option_index)}. {escape_markdown(option)}")
            for number, exercise in enumerate(question.exercises, 1):
                st.markdown(f"练习 {index}.{number}  {escape_markdown(exercise.statement)}")
                for option_index, option in enumerate(exercise.options):
                    st.markdown(f"{chr(65 + option_index)}. {escape_markdown(option)}")
    with st.container(border=True):
        st.markdown("**AI 对话**")
        messages = store.category_messages(batch.id, category)
        with st.container(height=280):
            if not messages:
                st.caption("可以发来你的解题过程，或告诉 AI 你卡在哪一步。")
            for turn in messages:
                with st.chat_message(turn["role"]):
                    st.markdown(turn["content"])
        message = st.chat_input("输入问题或解题过程", max_chars=1500, disabled=active or solved,
                                key=f"chat-{batch.id}-{category}")
        eligible = [question for question in members if not question.original.uncertainty]
        practice = st.button("再出此类练习", disabled=active or solved or not eligible,
                             key=f"practice-{batch.id}-{category}")
    if message or practice:
        try:
            # Cycle through the category's originals as exercises accumulate.
            source = min(eligible, key=lambda question: len(question.exercises)) if practice else members[0]
            store.queue_followup(batch, source, message or "", practice=practice)
            start_worker(store.path, submitted=True)
        except ERRORS as exc:
            st.error(str(exc))
        else:
            st.rerun()


def escape_markdown(text: str) -> str:
    # Preserve model-supplied question text while allowing the app's item number.
    return text.translate(str.maketrans({char: "\\" + char for char in r"\$*_[]<>"}))


def main():
    st.set_page_config(page_title="数学错题本", layout="wide")
    st.html("""
        <style>
        .stMainBlockContainer {max-width: 72rem; padding-top: 3rem;}
        [data-testid="stToolbar"] [data-testid="stAppDeployButton"] {display: none;}
        [data-testid="stSidebar"] {border-right: 1px solid #e0e9e7;}
        [data-testid="stFileUploaderDropzone"] {border: 1px dashed #a6c1ba; border-radius: .8rem;}
        [data-testid="stDialog"] [role="dialog"] {max-width: 58rem;}
        </style>
    """)
    store = Store(ROOT / "data" / "mistakes.sqlite3")
    if store.active_jobs():
        start_worker(store.path)
    if st.session_state.pop("open_history", False):
        st.session_state["navigation"] = "错题记录"
    with st.sidebar:
        st.subheader("数学错题本")
        page = st.radio("导航", ["录入错题", "错题记录"], label_visibility="collapsed", key="navigation")
    if page == "录入错题":
        upload(store)
    else:
        history(store)


if __name__ == "__main__":
    main()

