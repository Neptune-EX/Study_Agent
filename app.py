import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from dotenv import load_dotenv, set_key
import streamlit as st

from mistakes.deepseek import ModelError
from mistakes.download import download_file
from mistakes.drafts import confirm_original
from mistakes.intake_ui import practice_fields, upload
from mistakes.latex import ORIGINALS_MARKER, CompileError, render_batch
from mistakes.launcher import start_worker
from mistakes.models import DIFFICULTY_LABELS, FollowupPractice, Original
from mistakes.store import Batch, Question, Store
from mistakes.workflow import RECORD_REVIEW_QUESTION, needs_figure
from paths import DATA_DIR, ENV_FILE


load_dotenv(ENV_FILE)

ERRORS = (ModelError, CompileError, ValueError, OSError, sqlite3.Error)
LOCAL_TIME = ZoneInfo("Asia/Shanghai")



@st.dialog("欢迎使用数学错题本", width="small")
def setup_api_key_dialog():
    st.write("首次使用，请配置您的 DeepSeek API Key。该信息仅保存在本地。")
    with st.form("api_key_form"):
        key = st.text_input("DeepSeek API Key", type="password", placeholder="sk-...")
        submitted = st.form_submit_button("保存并开始使用", type="primary")
        if submitted:
            key = key.strip()
            if not key:
                st.error("API Key 不能为空。")
                return
            if "\r" in key or "\n" in key:
                st.error("API Key 必须为单行。")
                return
            try:
                ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
                saved, _, _ = set_key(ENV_FILE, "DEEPSEEK_API_KEY", key)
                if not saved:
                    raise OSError("配置文件写入失败")
            except OSError as exc:
                st.error(f"保存配置失败：{exc}")
                return
            os.environ["DEEPSEEK_API_KEY"] = key
            st.rerun()

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
        mastered = store.mastered_categories(batch.id)
        category_study(store, batch, questions, mastered)
        record_chat(store, batch, questions, mastered, active)


def category_study(store: Store, batch: Batch, questions: list[Question], mastered: set[str]):
    categories: dict[str, list[Question]] = {}
    for question in questions:
        if not needs_figure(question):
            categories.setdefault(question.category, []).append(question)
    if not categories:
        return
    st.divider()
    st.subheader("分类复习")
    st.caption(f"已解决 {len(mastered.intersection(categories))} / {len(categories)} 类")
    category = st.selectbox("选择知识点类别", list(categories), key=f"study-category-{batch.id}",
                            format_func=lambda name: f"{name} · {'已解决' if name in mastered else '未解决'}")
    members = categories[category]
    solved = category in mastered
    if st.button("标记此类为未解决" if solved else "标记此类为已解决",
                 key=f"mastered-{batch.id}-{category}"):
        try:
            store.set_category_mastered(batch.id, category, not solved)
        except ERRORS as exc:
            st.error(str(exc))
        else:
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


def record_chat(store: Store, batch: Batch, questions: list[Question], mastered: set[str], active: bool):
    members = [question for question in questions if not needs_figure(question)]
    if not members:
        return
    categories = list(dict.fromkeys(question.category for question in members if not question.original.uncertainty))
    practice = None
    st.divider()
    with st.container(border=True):
        st.subheader("整份记录 · AI 对话")
        messages = store.record_messages(batch.id)
        with st.container(height=280):
            with st.chat_message("assistant"):
                st.markdown(RECORD_REVIEW_QUESTION)
            for turn in messages:
                with st.chat_message(turn["role"]):
                    st.markdown(turn["content"])
        message = st.chat_input("说说本次复习的感受，或输入追加要求", max_chars=1500, disabled=active,
                                key=f"chat-{batch.id}")
        with st.expander("快捷追加练习"):
            scope_col, count_col = st.columns([2, 1])
            scope = scope_col.selectbox("出题范围", ["仅未解决题型", "指定题型", "整份记录"],
                                       key=f"practice-scope-{batch.id}")
            count = count_col.number_input("追加题数", min_value=1, max_value=6, value=batch.plan.count,
                                           key=f"chat-count-{batch.id}")
            selected = []
            if scope == "指定题型":
                selected = st.multiselect("选择题型", categories, key=f"practice-categories-{batch.id}",
                                          format_func=lambda name: f"{name} · {'已解决' if name in mastered else '未解决'}")
            style = st.selectbox("练习方式", ["同类练习", "更难的题", "变式练习"], key=f"practice-style-{batch.id}")
            unresolved_only = scope == "仅未解决题型"
            available = bool(categories)
            if unresolved_only and not (set(categories) - mastered):
                st.caption("当前没有可出题的未解决题型。")
                available = False
            elif scope == "指定题型" and not selected:
                available = False
            if st.button("追加练习并更新 TeX", disabled=active or not available, key=f"practice-{batch.id}"):
                request = {
                    "同类练习": "请追加同类练习。",
                    "更难的题": "请追加更难的题，增加推理或运算步骤，保留原考点。",
                    "变式练习": "请追加变式，改变情境、条件或设问方式，避免只换数字。",
                }[style]
                target = "、".join(selected) if selected else scope
                scope_label = target if len(target) <= 250 else f"指定的 {len(selected)} 个题型"
                message = f"{request} 共 {count} 道。出题范围为{scope_label}。"
                practice = FollowupPractice(categories=selected, unresolved_only=unresolved_only, count=count,
                                            difficulty="适度提高" if style == "更难的题" else None)
    if message:
        try:
            store.queue_followup(batch, message, practice=practice)
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

    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        st.title("数学错题本")
        st.button("配置 DeepSeek API Key")
        setup_api_key_dialog()
        return

    store = Store(DATA_DIR / "mistakes.sqlite3")
    active_jobs = store.active_jobs()
    if active_jobs:
        start_worker(store.path)
    if st.session_state.pop("open_history", False):
        st.session_state["navigation"] = "错题记录"

    with st.sidebar:
        st.subheader("数学错题本")
        page = st.radio("导航", ["录入错题", "错题记录"], label_visibility="collapsed", key="navigation")

        st.divider()
        if st.button("修改 API Key", width="stretch", disabled=bool(active_jobs),
                     help="后台任务结束后可修改。"):
            setup_api_key_dialog()
            return

    if page == "录入错题":
        upload(store)
    else:
        history(store)


if __name__ == "__main__":
    main()

