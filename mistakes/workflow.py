from collections.abc import Callable
from dataclasses import replace
import re
from time import sleep

from .debug import debug_context, record
from .deepseek import DeepSeek, ModelError
from .inputs import read_pages
from .latex import render_batch
from .models import MISTAKE_LABELS, Exercise, ExerciseIssue, Followup, Original, PracticePlan, TextFragment
from .selection import Target, printed_number
from .store import Batch, GenerationJob, Question, Store
from .verify import arithmetic_check

Source = tuple[int, str, TextFragment]
IntakeRecord = tuple[Original, str, list[bytes], list[bytes]]
FIGURE_REFERENCE = re.compile(r"如图|图中|见图|下图|上图|右图|左图|图示|根据图")
RECORD_REVIEW_QUESTION = (
    "这次错题复习得怎么样？要针对未解决的题型继续出题，还是挑几个题型做同类练习？"
    "也可以告诉我哪部分需要讲解，或者本次先不加题。"
)


def needs_figure(question: Question) -> bool:
    original = question.original
    return bool(original.kind == "geometry" or original.diagram or question.figure_description
                or FIGURE_REFERENCE.search(original.statement))


def choose_questions(sources: list[Source], targets: list[Target]) -> tuple[list[list[Source]], list[str]]:
    groups: list[list[Source]] = []
    for source in sources:
        page, _, fragment = source
        if fragment.continuation and groups:
            previous_page = groups[-1][-1][0]
            number = printed_number(fragment.number)
            previous_number = printed_number(groups[-1][0][2].number)
            if page == previous_page + 1 and (number is None or number == previous_number):
                groups[-1].append(source)
                continue
        groups.append([source])

    if not targets:
        return [group for group in groups if group[0][2].kind in MISTAKE_LABELS], []

    selected = set()
    warnings = []
    for target in targets:
        matches = [
            index for index, group in enumerate(groups)
            if printed_number(group[0][2].number) == target.number
            and (target.page is None or group[0][0] == target.page)
        ]
        if len(matches) != 1:
            reason = "未找到" if not matches else "重复，请用页码:题号指定"
            warnings.append(f"题号 {target} {reason}。")
        elif groups[matches[0]][0][2].kind not in MISTAKE_LABELS:
            warnings.append(f"题号 {target} 不属于选择、填空或应用题。")
        else:
            selected.add(matches[0])
    return [group for index, group in enumerate(groups) if index in selected], warnings


def analyze_upload(model: DeepSeek, filename: str, data: bytes, pages: str, grade: str,
                   targets: list[Target], progress: Callable[[str], None]) -> tuple[list[IntakeRecord], list[str]]:
    if not grade.strip():
        raise ValueError("请选择年级。")
    sources = []
    for number, image in read_pages(filename, data, pages, whole_file=not targets):
        progress(f"Flash 正在识别第 {number} 页")
        selected_numbers = tuple(target.number for target in targets if target.page in (None, number))
        for index, fragment in enumerate(model.recognize(number, image, selected_numbers).fragments, 1):
            source_id = f"{number}-{index}"
            sources.append((number, source_id, fragment))
    groups, warnings = choose_questions(sources, targets)
    text_groups = [
        group for group in groups
        if not any(fragment.kind == "geometry" or fragment.requires_figure
                   or FIGURE_REFERENCE.search(fragment.text)
                   for _, _, fragment in group)
    ]
    if excluded := len(groups) - len(text_groups):
        warnings.append(f"已跳过 {excluded} 道图形题或需要读图的题。")
    groups = text_groups
    record("intake.selection", requested=[str(target) for target in targets],
           recognized_count=len(sources), selected_sources=[[item[1] for item in group] for group in groups],
           warnings=warnings)
    if not groups:
        raise ModelError(" ".join(warnings) or "没有识别到选择题、填空题或应用题。")

    evidence = [
        [{"id": source_id, "page": number, **fragment.model_dump(include={
            "text", "number", "kind", "knowledge", "continuation", "uncertainty",
        })}
         for number, source_id, fragment in group]
        for group in groups
    ]

    progress("Flash 正在整理错题并判断难度")
    originals = []
    for start in range(0, len(evidence), 8):
        batch = evidence[start:start + 8]
        expected = [tuple(item["id"] for item in group) for group in batch]
        for _ in range(2):
            result = model.intake(batch, grade, "", "").questions
            by_source = {tuple(item.sources): item for item in result}
            if len(result) == len(expected) and set(by_source) == set(expected):
                originals.extend(by_source[key] for key in expected)
                break
            record("intake.rejected", reason="题目来源不匹配", expected=expected, actual=list(by_source))
        else:
            raise ModelError("整理时遗漏或混合了题目，请缩小识别范围后重试。")

    records = []
    excluded = 0
    for original, group in zip(originals, groups):
        number, _, first = group[0]
        if original.kind == "geometry" or FIGURE_REFERENCE.search(original.statement):
            excluded += 1
            continue
        if original.kind not in MISTAKE_LABELS:
            record("intake.rejected", reason="题型不匹配", sources=original.sources,
                   expected_kind=first.kind, actual_kind=original.kind)
            raise ModelError("整理后的题目与选中的原题或题型不一致，请重试。")
        issues = [original.uncertainty]
        for _, source_id, fragment in group:
            if fragment.uncertainty and not original.uncertainty:
                record("intake.ocr_note", source_id=source_id, note=fragment.uncertainty)
        if first.continuation:
            issues.append(f"第 {number} 页以续题开头，请确认是否缺少前页题干。")
        if original.kind == "choice" and len(original.options) < 2:
            issues.append("选择题选项识别不完整，请核对。")
        if original.kind == "blank" and "___" not in original.statement:
            issues.append("未识别到填空位置，请用 ______ 标出。")
        original = Original.model_validate({**original.model_dump(),
            "number": first.number, "grade": grade, "knowledge": original.knowledge or first.knowledge or original.topic,
            "uncertainty": "；".join(dict.fromkeys(filter(None, issues)))[:2000],
            "answer": "", "solution": "",
        })
        records.append((original, "", [], []))
    if excluded:
        warnings.append(f"整理时又跳过了 {excluded} 道需要读图的题。")
    if not records:
        raise ModelError("所选题目均需要读图，已过滤。")
    return records, warnings


def problem_signature(item: Original | Exercise) -> tuple[str, tuple[str, ...]]:
    return "".join(item.statement.split()), tuple(sorted("".join(option.split()) for option in item.options))


def check_exercises(original: Original, exercises: list[Exercise], plan: PracticePlan) -> str:
    signatures = [problem_signature(item) for item in [original, *exercises]]
    if len(exercises) != plan.count or len(set(signatures)) != len(signatures):
        return "练习数量不符或题干重复。"
    for index, item in enumerate(exercises, 1):
        if item.diagram:
            return f"练习 {index} 应仅提供完整题干。"
        if FIGURE_REFERENCE.search(item.statement):
            return f"练习 {index} 引用了不存在的图，请将题设完整写入题干。"
        if original.kind == "blank" and "___" not in item.statement:
            return f"练习 {index} 缺少填空位置。"
        if original.kind == "choice":
            options = {"".join(option.split()) for option in item.options}
            if len(options) != len(item.options) or len(item.options) != len(original.options):
                return f"练习 {index} 的选项重复或数量不符。"
        elif item.options:
            return f"练习 {index} 的作答形式与原题不同。"
    return ""


def generate(question: Question, model: DeepSeek, plan: PracticePlan,
             progress: Callable[[str], None], *, state: dict | None = None,
             save: Callable[[dict], None] = lambda state: None,
             conversation: list[dict] | None = None,
             record_problems: list[Original | Exercise] | None = None) -> list[Exercise]:
    original = question.original
    if original.uncertainty:
        raise ValueError("原题需要核对：" + original.uncertainty)
    state = state or {}
    if state.get("review_version") == 2 and "accepted" in state:
        return [Exercise.model_validate(item) for item in state["accepted"]]
    feedback = state.get("feedback", "")
    prior = {problem_signature(item) for item in record_problems or []}
    for attempt in range(3):
        if "draft" not in state:
            _, name = model.practice_model(original)
            progress(f"{name} 正在出题 · {original.topic}")
            if conversation is not None:
                previous = [item.model_dump(include={"statement", "options"}) for item in (record_problems or [])[-8:]]
                exercises = model.practice(original, plan, feedback, conversation=conversation, previous=previous)
            else:
                exercises = model.practice(original, plan, feedback)
            state["draft"] = [item.model_dump() for item in exercises]
            save(state)
        else:
            exercises = [Exercise.model_validate(item) for item in state["draft"]]
        feedback = check_exercises(original, exercises, plan)
        if not feedback and any(problem_signature(item) in prior for item in exercises):
            feedback = "有练习与记录中的原题或已出练习重复，请换题。"
        if feedback:
            record("practice.rejected", reason=feedback)
            state = {"feedback": feedback}
            save(state)
            continue
        checks = [arithmetic_check(item) for item in exercises]
        remaining = {i: item.model_dump(exclude={"diagram"})
                     for i, (item, check) in enumerate(zip(exercises, checks), 1) if check is None}
        issues = [ExerciseIssue(number=i, kind=check[0], reason=check[1])
                  for i, check in enumerate(checks, 1) if check and check[0] != "pass"]
        _, name = model.practice_model(original)
        if remaining:
            progress(f"{name} 正在验算 · {original.topic}")
            review = model.review(original, [{"number": i, **item} for i, item in remaining.items()])
            reviewed = [issue.number for issue in review.issues]
            if sorted(review.checked) != sorted(remaining) or not set(reviewed) <= set(remaining) or len(set(reviewed)) != len(reviewed):
                raise ModelError("验算结果未与题号一一对应，已停止导出该组练习。")
            issues.extend(review.issues)
        if not issues:
            record("practice.accepted", count=len(exercises))
            save({"review_version": 2, "accepted": [item.model_dump() for item in exercises]})
            return exercises
        feedback = "；".join(f"第 {issue.number} 题：{issue.reason}" for issue in issues)
        record("practice.rejected", reason=feedback)
        if attempt == 2:
            break
        progress(f"{name} 正在修正练习 · {original.topic}")
        corrections = model.repair(original, [
            {"issue": issue.model_dump(), "exercise": exercises[issue.number - 1].model_dump(exclude={"diagram"})}
            for issue in issues
        ]).corrections
        fixed = {item.number: Exercise(**item.exercise.model_dump()) for item in corrections}
        if sorted(item.number for item in corrections) != sorted(issue.number for issue in issues):
            raise ModelError("修正结果未与题号一一对应，已停止导出该组练习。")
        for issue in issues:
            index = issue.number - 1
            old, new = exercises[index], fixed[issue.number]
            if issue.kind == "answer" and (old.statement != new.statement or old.options != new.options):
                raise ModelError(f"修正第 {issue.number} 题答案时改变了题干或选项。")
            if issue.kind == "question" and problem_signature(old) == problem_signature(new):
                raise ModelError(f"第 {issue.number} 题的题设问题尚未修正。")
            exercises[index] = new
        state = {"draft": [item.model_dump() for item in exercises], "feedback": feedback}
        save(state)
    raise ModelError("练习未通过复核。原因：" + feedback)


def finish_followup(store: Store, batch: Batch, job: GenerationJob, progress: Callable[[str], None]):
    questions = store.batch_questions(batch.id)
    members = [item for item in questions if not needs_figure(item)]
    if not members:
        raise ValueError("这份记录没有可辅导的文字题。")
    conversation = [
        {"role": "assistant", "content": RECORD_REVIEW_QUESTION},
        *store.record_messages(batch.id, limit=12),
    ]
    originals = [item.original for item in members]
    exercises = [exercise for item in members for exercise in item.exercises]
    followup = job.followup or Followup(mastered=store.mastered_categories(batch.id))
    mastered = followup.mastered
    eligible = sorted((item for item in members if not item.original.uncertainty),
                      key=lambda item: len(item.exercises))
    eligible_categories = {item.category for item in eligible}
    practice = followup.practice
    model = DeepSeek()
    for attempt in range(3):
        try:
            if job.action == "tutor" and practice is None:
                progress("AI 正在回复")
                recent = [item.model_dump(exclude={"diagram"}) for item in exercises[-8:]]
                categories = [
                    {"name": name, "resolved": name in mastered, "can_practice": name in eligible_categories}
                    for name in dict.fromkeys(item.category for item in members)
                ]
                reply = model.tutor(originals, conversation, recent, categories=categories, plan=job.plan)
                if reply.practice is None:
                    store.save_tutor_reply(job, reply.reply)
                    return
                store.promote_followup(job, followup.model_copy(update={"practice": reply.practice}))
                practice = reply.practice

            selected = eligible
            plan = job.plan
            if practice:
                if set(practice.categories) - eligible_categories:
                    store.save_tutor_reply(job, "所选题型不在本次可出题范围内。请选择本次记录中已核对的题型。")
                    return
                selected = [
                    item for item in eligible
                    if (not practice.categories or item.category in practice.categories)
                    and (not practice.unresolved_only or item.category not in mastered)
                ]
                plan = job.plan.model_copy(update=practice.model_dump(include={"count", "difficulty"}, exclude_none=True))
            if not selected:
                reply = ("所选范围内没有可出题的未解决题型。你可以指定其他题型，或先核对原题。"
                         if practice and practice.unresolved_only else "请先核对记录中的原题，再继续出题。")
                store.save_tutor_reply(job, reply)
                return

            representatives = {}
            for item in selected:
                representatives.setdefault(item.category, item)
            sources = list(representatives.values())
            sources.extend(item for item in selected if item.id != representatives[item.category].id)
            rounds, extra = divmod(plan.count, len(sources))
            updated = {}
            added = []
            for index, question in enumerate(sources):
                count = rounds + (index < extra)
                if not count:
                    break
                question_plan = plan.model_copy(update={"count": count})
                additions = generate(
                    question, model, question_plan, progress, state=store.checkpoint(job.id, question.id),
                    save=lambda state: store.save_checkpoint(job.id, question.id, state), conversation=conversation,
                    record_problems=[*originals, *exercises, *added],
                )
                added.extend(additions)
                updated[question.id] = replace(question, exercises=[*question.exercises, *additions])
            included = [updated.get(item.id, item) for item in members if item.exercises or not item.original.uncertainty]
            tex = render_batch(included, batch.plan, True).encode("utf-8")
            _, _, warnings = store.batch_result(batch.id)
            covered = "、".join(dict.fromkeys(item.category for item in updated.values()))
            store.save_batch(
                batch, batch.plan, [(item, item.exercises) for item in updated.values()], tex, warnings,
                followup=job, reply=f"已追加 {len(added)} 道同类练习（{covered}），并更新 TeX。可编译新版 PDF。",
            )
            return
        except ModelError as exc:
            if not exc.retryable or attempt == 2:
                raise
            delay = 5 * (2 ** attempt)
            progress(f"接口暂时不可用，{delay} 秒后继续")
            sleep(delay)


def finish_batch(store: Store, batch: Batch, plan: PracticePlan, model: DeepSeek | None = None,
                 progress: Callable[[str], None] = print, *, job: GenerationJob | None = None) -> list[str]:
    questions = store.batch_questions(batch.id)
    regenerate = plan != batch.plan
    previous_pdf, previous_tex, _ = store.batch_result(batch.id)
    record("batch.start", batch_id=batch.id, question_count=len(questions), regenerate=regenerate,
           plan=plan.model_dump())
    changes = []
    prepared = []
    warnings = []
    filtered = 0
    for question in questions:
        if needs_figure(question):
            filtered += 1
            if question.exercises:
                changes.append((question, []))
            continue
        if regenerate or not question.exercises:
            try:
                if question.original.uncertainty:
                    raise ValueError(question.original.uncertainty)
                model = model or DeepSeek()
                with debug_context(batch_id=batch.id, question_id=question.id, sources=question.original.sources):
                    for attempt in range(3):
                        try:
                            if job:
                                exercises = generate(
                                    question, model, plan, progress,
                                    state=store.checkpoint(job.id, question.id),
                                    save=lambda state: store.save_checkpoint(job.id, question.id, state),
                                )
                            else:
                                exercises = generate(question, model, plan, progress)
                            break
                        except ModelError as exc:
                            if not job or not exc.retryable or attempt == 2:
                                raise
                            delay = 5 * (2 ** attempt)
                            progress(f"接口暂时不可用，{delay} 秒后继续 · {question.original.topic}")
                            sleep(delay)
                changes.append((question, exercises))
                question = replace(question, exercises=exercises)
            except (ModelError, ValueError) as exc:
                record("batch.question_error", batch_id=batch.id, question_id=question.id, error=str(exc))
                warnings.append(f"{question.original.topic} · {exc}")
                if regenerate:
                    if not (previous_pdf or previous_tex):
                        changes.append((question, []))
                    question = replace(question, exercises=[])
        if question.exercises:
            prepared.append(question)
    if regenerate and (previous_pdf or previous_tex) and warnings:
        return ["这次调整未全部通过，已保留上一版。", *warnings]
    if not prepared and (previous_pdf or previous_tex):
        return ["本次没有可导出的新练习，已保留上一版。", *warnings]
    tex = previous_tex if not regenerate and not changes else None
    if not tex and prepared:
        completed = {question.id: question for question in prepared}
        included = [completed.get(question.id, question) for question in questions
                    if not needs_figure(question) and not question.original.uncertainty]
        tex = render_batch(included, plan, True).encode("utf-8")
    if filtered:
        warnings.append(f"本次未生成 {filtered} 道图形题。")
    store.save_batch(batch, plan, changes, tex, warnings)
    record("batch.saved", batch_id=batch.id, tex_bytes=len(tex or b""),
           pending=len(questions) - len(prepared))
    return warnings
