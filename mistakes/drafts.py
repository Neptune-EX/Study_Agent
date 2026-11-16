from pathlib import Path
from uuid import uuid4

from .deepseek import DeepSeek, ModelError
from .inputs import page_count, read_pages
from .models import DraftQuestion, DraftQuestionText, IntakeChatMessage, IntakeDraftState, Original, PracticePlan
from .store import IntakeDraft, Store
from .workflow import FIGURE_REFERENCE, IntakeRecord


def original_pages(original: Original) -> list[int]:
    return list(dict.fromkeys(int(source.split("-", 1)[0]) for source in original.sources))


def build_draft(filename: str, source: bytes, records: list[IntakeRecord], warnings: list[str],
                grade: str, plan: PracticePlan, run_id: str | None) -> IntakeDraftState:
    questions = [
        DraftQuestion(
            id=uuid4().hex, page=original_pages(original)[0], original=original,
            text=DraftQuestionText(
                kind=original.kind, number=original.number, knowledge=original.knowledge or original.topic,
                difficulty=original.difficulty, statement=original.statement, options="\n".join(original.options),
            ),
        )
        for original, *_ in records
    ]
    return IntakeDraftState(
        title=Path(filename).stem[:120], grade=grade, plan=plan, page_count=page_count(filename, source),
        questions=questions, warnings=warnings, analysis_run_id=run_id,
    )


def confirm_original(data: dict) -> Original:
    original = Original.model_validate(data)
    if "[未见题干]" in original.statement or not original.statement.strip("_ \t"):
        raise ValueError("请补全原题题干。")
    if original.kind == "choice" and len(original.options) < 2:
        raise ValueError("选择题至少需要两个选项。")
    if original.kind == "blank" and "___" not in original.statement:
        raise ValueError("请在填空题中用 ______ 标出空位。")
    return original


def confirmed_records(state: IntakeDraftState) -> list[IntakeRecord]:
    if not state.title.strip():
        raise ValueError("请输入记录名称。")
    if not state.questions:
        raise ValueError("请至少保留一道错题。")
    records = []
    for index, question in enumerate(state.questions, 1):
        text, original = question.text, question.original
        if question.defer and original and original.uncertainty:
            records.append((original, "", [], []))
            continue
        try:
            if not text.knowledge.strip():
                raise ValueError("请填写知识点。")
            if not text.statement.strip():
                raise ValueError("请补全原题题干。")
            if FIGURE_REFERENCE.search(text.statement):
                raise ValueError("目前只支持不依赖读图的题目。")
            same_page = original and question.page == original_pages(original)[0]
            data = {
                **(original.model_dump() if original else {}), **text.model_dump(),
                "topic": original.topic if original and text.knowledge == (original.knowledge or original.topic)
                else text.knowledge,
                "subtype": original.subtype if original and text.statement == original.statement
                and text.kind == original.kind and text.knowledge == (original.knowledge or original.topic) else "",
                "options": [line.strip() for line in text.options.splitlines() if line.strip()]
                if text.kind == "choice" else [],
                "sources": original.sources if same_page else [f"{question.page}-draft-{question.id}"],
                "uncertainty": "", "grade": state.grade,
            }
            if len(data["options"]) > 8 or any(len(option) > 120 for option in data["options"]):
                raise ValueError("选择题最多填写 8 个选项，每项不超过 120 字。")
            records.append((confirm_original(data), "", [], []))
        except ValueError as exc:
            raise ValueError(f"草稿中的第 {index} 道题：{exc}") from exc
    return records


def draft_image(store: Store, draft: IntakeDraft, page: int) -> bytes:
    return next(image for _, image in read_pages(draft.filename, store.draft_source(draft.id), str(page)))


def ask_about_draft(store: Store, draft: IntakeDraft, model: DeepSeek, page: int, message: str):
    state = draft.state
    if state.corrections:
        raise ValueError("请先处理上一轮的修改建议。")
    message = message.strip()
    if not message or not 1 <= page <= state.page_count:
        raise ValueError("请填写补充说明并选择有效页码。")
    questions = {
        question.id: question for question in state.questions
        if question.page == page or (
            question.original and question.page == original_pages(question.original)[0]
            and page in original_pages(question.original)
        )
    }
    reply = model.correct_intake(
        page, draft_image(store, draft, page), state.grade,
        [{"question_id": question.id, **question.text.model_dump()} for question in questions.values()],
        [entry.model_dump() for entry in state.chat[-8:]], message,
    )
    updated_ids = set()
    for correction in reply.corrections:
        target = correction.question_id
        if target and (target not in questions or target in updated_ids):
            raise ModelError("AI 建议指向了无效或重复的题目，请明确页码和题号后重试。")
        if not correction.question.statement.strip() or FIGURE_REFERENCE.search(correction.question.statement):
            raise ModelError("AI 建议未读出完整的文字题干，请提供补充说明或手动修改。")
        if target:
            updated_ids.add(target)
    state.chat.extend([IntakeChatMessage(role="user", content=message, page=page),
                       IntakeChatMessage(role="assistant", content=reply.reply, page=page)])
    state.corrections = reply.corrections
    state.correction_page = page
    store.update_draft(draft)


def apply_corrections(state: IntakeDraftState):
    by_id = {question.id: question for question in state.questions}
    for correction in state.corrections:
        if correction.question_id:
            question = by_id[correction.question_id]
            question.text = correction.question
            question.defer = False
        else:
            state.questions.append(DraftQuestion(id=uuid4().hex, page=state.correction_page, text=correction.question))
    state.corrections = []
