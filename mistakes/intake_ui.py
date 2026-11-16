from collections.abc import Callable
import sqlite3
from uuid import uuid4

import streamlit as st

from .debug import debug_run, record
from .deepseek import DeepSeek, ModelError
from .drafts import apply_corrections, ask_about_draft, build_draft, confirmed_records, draft_image
from .inputs import IMAGE_TYPES, MAX_PAGES, MAX_UPLOAD_MB
from .launcher import start_worker
from .models import DIFFICULTY_LABELS, MISTAKE_LABELS, DraftQuestion, DraftQuestionText, PracticePlan
from .selection import read_targets
from .store import IntakeDraft, Store
from .workflow import analyze_upload


GRADES = ["", "一年级", "二年级", "三年级", "四年级", "五年级", "六年级", "七年级", "八年级", "九年级",
          "高一", "高二", "高三"]
LEVELS = ["巩固基础", "与原题相当", "适度提高"]
KINDS = ["choice", "blank", "word"]
ERRORS = (ModelError, ValueError, OSError, sqlite3.Error)


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


def upload(store: Store):
    st.title("录入错题")
    if error := st.session_state.pop("draft_error", None):
        st.error(error)
    if draft_id := st.session_state.get("active_intake_draft"):
        if draft := store.get_draft(draft_id):
            if st.session_state.get("rescope_draft") == draft.id:
                if st.button("返回核对草稿"):
                    st.session_state.pop("rescope_draft", None)
                    st.rerun()
                st.subheader("调整识别范围")
                st.caption(f"{draft.filename} · 再次识别会另存为一份草稿，当前草稿会保留。")
                analyze_file(store, draft.filename, lambda: store.draft_source(draft.id), draft.id,
                             default_grade=draft.state.grade, default_plan=draft.state.plan)
                return
            review_draft(store, draft)
            return
        st.session_state.pop("active_intake_draft", None)
        st.info("这条草稿已经提交或删除。")

    if drafts := store.draft_summaries():
        st.subheader("录入草稿")
        st.caption("继续核对已识别的错题，无需重新上传或识别。")
        for draft in drafts:
            title, action = st.columns([4, 1])
            title.write(draft["title"] or draft["filename"])
            title.caption(f'{draft["filename"]} · {draft["count"]} 道题')
            if action.button("继续核对", key=f'open-{draft["id"]}'):
                st.session_state["active_intake_draft"] = draft["id"]
                st.rerun()

    st.subheader("上传新文件")
    uploaded = st.file_uploader(
        "题目文件", type=["pdf", *sorted(suffix[1:] for suffix in IMAGE_TYPES)],
        help=f"支持扫描件、照片和 PDF。单文件最大 {MAX_UPLOAD_MB} MB。",
    )
    if uploaded is not None:
        analyze_file(store, uploaded.name, uploaded.getvalue, uploaded.file_id)


def analyze_file(store: Store, filename: str, get_source: Callable[[], bytes], key: str,
                 default_grade: str = "", default_plan: PracticePlan | None = None):
    st.caption("图形题和需要读图的题会自动跳过。")
    scopes = ["指定题号", "整份都是错题", "仅收录有订正痕迹的题"]
    scope = st.radio("错题范围", scopes, horizontal=True, key=f"upload-scope-{key}",
                     help="订正痕迹包括批改红叉、红圈、扣分和异色订正等。")
    whole, marked_only = scope == scopes[1], scope == scopes[2]
    with st.form(f"analyze-{key}"):
        left, right = st.columns(2)
        grade = left.selectbox("年级", GRADES, index=GRADES.index(default_grade),
                               format_func=lambda grade: grade or "请选择")
        selection, pages = "", ""
        if not (whole or marked_only):
            selection = right.text_input("错题题号", placeholder="例如 2,5-7 或 3:9",
                                         help="3:9 表示第 3 页的第 9 题。")
            if filename.lower().endswith(".pdf"):
                pages = st.text_input("PDF 页码范围（选填）", placeholder="例如 1,3-5",
                                      help=f"留空检查全部页面，指定题号时最多检查 {MAX_PAGES} 页。")
        plan = practice_fields(default_plan or PracticePlan())
        submitted = st.form_submit_button("识别题目与知识点", type="primary")
    if not submitted:
        return
    try:
        targets = read_targets(selection, whole or marked_only)
        source = get_source()
        with debug_run("analyze_upload", filename=filename, pages=pages, whole=whole) as run_id, \
                st.status("正在识别并归纳知识点", expanded=True) as status:
            if run_id:
                st.caption(f"调试编号 {run_id}")
            records, warnings = analyze_upload(DeepSeek(), filename, source, pages,
                                               grade, targets, st.write, marked_only=marked_only)
            state = build_draft(filename, source, records, warnings, grade, plan, run_id)
            draft_id = store.create_draft(filename, source, state)
            status.update(label="识别完成，草稿已保存", state="complete")
    except ERRORS as exc:
        st.error(str(exc))
        return
    st.session_state["active_intake_draft"] = draft_id
    st.session_state.pop("rescope_draft", None)
    st.rerun()


def save_field(store: Store, draft_id: str, question_id: str | None, field: str, widget_key: str):
    try:
        draft = store.get_draft(draft_id)
        if draft is None:
            raise ValueError("这条草稿已经提交或删除。")
        value = st.session_state[widget_key]
        if question_id is None:
            draft.state.title = value
        else:
            question = next((item for item in draft.state.questions if item.id == question_id), None)
            if question is None:
                raise ValueError("这道题已经从草稿移除。")
            item = question if field in ("page", "defer") else question.text
            if getattr(item, field) == value:
                return
            setattr(item, field, value)
            if draft.state.corrections:
                draft.state.corrections = []
                st.session_state["draft_notice"] = "草稿题目已改动，上一轮 AI 建议已撤下；可以继续对话。"
        store.update_draft(draft)
    except ERRORS as exc:
        st.session_state["draft_error"] = f"保存修改失败：{exc}"


def question_editor(store: Store, draft: IntakeDraft, question: DraftQuestion, index: int, key: str):
    text = question.text
    prefix = f"{key}-{question.id}"

    def field(widget, label: str, name: str, **kwargs):
        widget_key = f"{prefix}-{name}"
        return widget(label, key=widget_key, on_change=save_field,
                      args=(store, draft.id, question.id, name, widget_key), **kwargs)

    number = f" · 原题号 {text.number}" if text.number else ""
    with st.expander(f"第 {index} 道 · 第 {question.page} 页{number} · {text.knowledge or '待填写知识点'}",
                     expanded=True, key=prefix):
        if question.original and question.original.uncertainty:
            st.warning(question.original.uncertainty)
            field(st.checkbox, "暂缓核对，保留识别原文", "defer", value=question.defer)
        number_col, kind_col, difficulty_col = st.columns(3)
        field(number_col.text_input, "原题号（选填）", "number", value=text.number, max_chars=35)
        field(kind_col.selectbox, "题型", "kind", options=KINDS, index=KINDS.index(text.kind),
              format_func=MISTAKE_LABELS.get)
        difficulties = [None, *DIFFICULTY_LABELS]
        field(difficulty_col.selectbox, "原题难度", "difficulty", options=difficulties,
              index=difficulties.index(text.difficulty),
              format_func=lambda value: DIFFICULTY_LABELS.get(value, "未分级 · Flash"))
        if draft.state.page_count > 1:
            field(st.number_input, "原文件页码", "page", min_value=1, max_value=draft.state.page_count,
                  value=question.page, step=1)
        field(st.text_input, "知识点", "knowledge", value=text.knowledge, max_chars=120)
        field(st.text_area, "题干", "statement", value=text.statement, max_chars=4000,
              placeholder="填空位置请用 ______ 标出。")
        if text.kind == "choice":
            field(st.text_area, "选项，每行一项", "options", value=text.options, max_chars=1000)
        if st.button("删除这道题", key=f"{prefix}-delete"):
            draft.state.questions.remove(question)
            save_question_list(store, draft)


def save_question_list(store: Store, draft: IntakeDraft):
    had_corrections = bool(draft.state.corrections)
    draft.state.corrections = []
    try:
        store.update_draft(draft)
    except ERRORS as exc:
        st.error(str(exc))
        return
    if had_corrections:
        st.session_state["draft_notice"] = "草稿题目已改动，上一轮 AI 建议已撤下；可以继续对话。"
    st.rerun()


def review_draft(store: Store, draft: IntakeDraft):
    state = draft.state
    key = f"intake-{draft.id}"
    back, rescope = st.columns([1, 4])
    if back.button("返回录入首页", key=f"{key}-back"):
        st.session_state.pop("active_intake_draft", None)
        st.rerun()
    if rescope.button("调整识别范围", key=f"{key}-rescope"):
        st.session_state["rescope_draft"] = draft.id
        st.rerun()
    st.subheader("核对录入草稿")
    st.caption(f"{draft.filename} · {state.grade} · {len(state.questions)} 道题 · 编辑完成后自动保存到本机")
    if notice := st.session_state.pop("draft_notice", None):
        st.info(notice)
    if state.analysis_run_id:
        st.caption(f"识别调试编号 {state.analysis_run_id}")
    for warning in state.warnings:
        st.warning(warning)
    title_key = f"{key}-title"
    st.text_input("记录名称", state.title, max_chars=120, key=title_key, on_change=save_field,
                  args=(store, draft.id, None, "title", title_key))
    if not state.questions:
        st.info("草稿里还没有错题。可以手动添加，或请 AI 对照原图补录。")
    epoch = st.session_state.get(f"{key}-editor-epoch", 0)
    for index, question in enumerate(state.questions, 1):
        question_editor(store, draft, question, index, f"{key}-{epoch}")
    if st.button("添加一道题", key=f"{key}-add"):
        page = state.questions[-1].page if state.questions else 1
        state.questions.append(DraftQuestion(id=uuid4().hex, page=page, text=DraftQuestionText()))
        save_question_list(store, draft)

    intake_chat(store, draft, key)
    st.divider()
    if st.button("确认并生成练习", type="primary", key=f"{key}-generate", disabled=bool(state.corrections)):
        submit_draft(store, draft)
    with st.expander("删除草稿"):
        st.caption("删除后无法恢复这份草稿中的题目、原文件和对话。")
        if st.button("确认删除草稿", key=f"{key}-discard"):
            try:
                store.delete_draft(draft)
            except ERRORS as exc:
                st.error(str(exc))
                return
            st.session_state.pop("active_intake_draft", None)
            st.rerun()


def intake_chat(store: Store, draft: IntakeDraft, key: str):
    st.subheader("AI 核对原图")
    st.caption("例如：「第 5 题是 12.5，不是 125」或「请补录本页右下角遗漏的第 8 题」。"
               "AI 会查看选中的原页，修改建议由你确认。")
    state = draft.state
    page = 1
    if state.page_count > 1:
        page = st.number_input("原图页码", 1, state.page_count, state.questions[0].page if state.questions else 1,
                               step=1, key=f"{key}-chat-page")
    if st.checkbox("查看原页", key=f"{key}-show-page"):
        try:
            st.image(draft_image(store, draft, page), caption=f"原文件第 {page} 页")
        except ERRORS as exc:
            st.error(str(exc))
    with st.container():
        for entry in state.chat:
            with st.chat_message(entry.role):
                if entry.role == "user":
                    st.caption(f"第 {entry.page} 页")
                st.text(entry.content)
        if message := st.chat_input("告诉 AI 哪里读错了，或者漏了哪道题", max_chars=1700,
                                    key=f"{key}-chat", disabled=bool(state.corrections)):
            try:
                with st.spinner("AI 正在核对原图…"):
                    ask_about_draft(store, draft, DeepSeek(), page, message)
            except ERRORS as exc:
                st.error(str(exc))
                return
            st.rerun()
    if not state.corrections:
        return
    by_id = {question.id: (index, question) for index, question in enumerate(state.questions, 1)}
    with st.container(border=True):
        st.markdown("**待确认的修改建议**")
        for correction in state.corrections:
            text = correction.question
            if correction.question_id:
                index, question = by_id[correction.question_id]
                st.markdown(f"**修改草稿中的第 {index} 道题**")
                st.text(f"原内容：{question_summary(question.text)}")
            else:
                st.markdown(f"**补录第 {state.correction_page} 页的题目**")
            st.text(question_summary(text))
        apply_col, ignore_col = st.columns(2)
        apply = apply_col.button("应用建议", type="primary", key=f"{key}-apply")
        ignore = ignore_col.button("忽略建议", key=f"{key}-ignore")
        if apply or ignore:
            if apply:
                apply_corrections(state)
            else:
                state.corrections = []
            try:
                store.update_draft(draft)
            except ERRORS as exc:
                st.error(str(exc))
                return
            if apply:
                epoch_key = f"{key}-editor-epoch"
                st.session_state[epoch_key] = st.session_state.get(epoch_key, 0) + 1
            st.rerun()


def question_summary(text: DraftQuestionText) -> str:
    number = f"第 {text.number} 题 · " if text.number else ""
    options = f"\n{text.options}" if text.kind == "choice" and text.options else ""
    difficulty = DIFFICULTY_LABELS.get(text.difficulty, "未分级")
    return f"{number}{MISTAKE_LABELS[text.kind]} · {text.knowledge or '待填写知识点'} · {difficulty}\n{text.statement}{options}"


def submit_draft(store: Store, draft: IntakeDraft):
    state = draft.state
    try:
        confirmed = confirmed_records(state)
        with debug_run("confirm_upload", filename=draft.filename) as run_id:
            batch_id = store.save_intake(draft.filename, confirmed, state.grade, "", state.plan,
                                         uploaded_at=draft.created_at, queue_generation=True,
                                         title=state.title, draft=draft)
            record("intake.saved", batch_id=batch_id, count=len(confirmed))
    except ERRORS as exc:
        st.error(str(exc))
        return
    st.session_state["history_notice"] = (batch_id, state.warnings, run_id)
    st.session_state["last_batch"] = batch_id
    st.session_state["open_history"] = True
    st.session_state.pop("active_intake_draft", None)
    start_worker(store.path)
    st.rerun()
