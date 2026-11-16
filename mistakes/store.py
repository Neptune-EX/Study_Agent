from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
import unicodedata
from uuid import uuid4

from .models import Exercise, Followup, FollowupPractice, IntakeDraftState, Original, PracticePlan


def category_name(original: Original) -> str:
    return " ".join(unicodedata.normalize("NFKC", original.knowledge or original.topic).split())


@dataclass
class Question:
    id: str
    filename: str
    created_at: str
    original: Original
    figure_description: str
    exercise_count: int
    exercises: list[Exercise]
    original_json: str = ""
    category: str = ""


@dataclass
class Batch:
    id: str
    filename: str
    created_at: str
    grade: str
    curriculum: str
    plan: PracticePlan
    revision: int
    title: str = ""


@dataclass
class GenerationJob:
    id: str
    batch_id: str
    plan: PracticePlan
    state: str
    message: str
    debug_id: str | None
    action: str
    question_id: str | None = None
    followup: Followup | None = None


@dataclass
class IntakeDraft:
    id: str
    filename: str
    created_at: str
    revision: int
    state: IntakeDraftState


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS questions (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    original TEXT NOT NULL,
                    figure_description TEXT NOT NULL,
                    exercise_count INTEGER NOT NULL,
                    exercises TEXT,
                    pdf BLOB,
                    tex_zip BLOB,
                    mastered INTEGER NOT NULL DEFAULT 0,
                    category TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS figures (
                    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    png BLOB NOT NULL,
                    PRIMARY KEY (question_id, position)
                );
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    grade TEXT NOT NULL,
                    curriculum TEXT NOT NULL,
                    plan TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    pdf BLOB,
                    tex BLOB,
                    warnings TEXT NOT NULL DEFAULT '[]',
                    title TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS batch_questions (
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    question_id TEXT NOT NULL UNIQUE REFERENCES questions(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (batch_id, position)
                );
                CREATE TABLE IF NOT EXISTS generation_jobs (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    plan TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued',
                    message TEXT NOT NULL DEFAULT '等待后台处理',
                    debug_id TEXT,
                    action TEXT NOT NULL DEFAULT 'generate',
                    question_id TEXT REFERENCES questions(id) ON DELETE CASCADE,
                    followup TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_generation
                ON generation_jobs(batch_id) WHERE state IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS generation_checkpoints (
                    job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
                    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    state TEXT NOT NULL,
                    PRIMARY KEY (job_id, question_id)
                );
                CREATE TABLE IF NOT EXISTS question_messages (
                    id INTEGER PRIMARY KEY,
                    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
                    UNIQUE(job_id, role)
                );
                CREATE TABLE IF NOT EXISTS category_progress (
                    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    mastered INTEGER NOT NULL,
                    PRIMARY KEY(batch_id, category)
                );
                CREATE INDEX IF NOT EXISTS question_message_order ON question_messages(question_id, id);
                CREATE TABLE IF NOT EXISTS record_messages (
                    job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    PRIMARY KEY(job_id, role)
                );
                CREATE TABLE IF NOT EXISTS intake_drafts (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    revision INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    source BLOB NOT NULL
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(batches)")}
            if "tex" not in columns:
                db.execute("ALTER TABLE batches ADD COLUMN tex BLOB")
            if "warnings" not in columns:
                db.execute("ALTER TABLE batches ADD COLUMN warnings TEXT NOT NULL DEFAULT '[]'")
            if "title" not in columns:
                db.execute("ALTER TABLE batches ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            job_columns = {row["name"] for row in db.execute("PRAGMA table_info(generation_jobs)")}
            if "action" not in job_columns:
                db.execute("ALTER TABLE generation_jobs ADD COLUMN action TEXT NOT NULL DEFAULT 'generate'")
            if "question_id" not in job_columns:
                db.execute("ALTER TABLE generation_jobs ADD COLUMN question_id TEXT REFERENCES questions(id) ON DELETE CASCADE")
            if "followup" not in job_columns:
                db.execute("ALTER TABLE generation_jobs ADD COLUMN followup TEXT")
            question_columns = {row["name"] for row in db.execute("PRAGMA table_info(questions)")}
            if "mastered" not in question_columns:
                db.execute("ALTER TABLE questions ADD COLUMN mastered INTEGER NOT NULL DEFAULT 0")
            if "category" not in question_columns:
                db.execute("ALTER TABLE questions ADD COLUMN category TEXT NOT NULL DEFAULT ''")
                rows = db.execute("SELECT id, original FROM questions").fetchall()
                db.executemany("UPDATE questions SET category = ? WHERE id = ?", [
                    (category_name(Original.model_validate_json(row["original"])), row["id"]) for row in rows
                ])
            db.execute("""
                INSERT INTO batches (id, filename, created_at, grade, curriculum, plan)
                SELECT q.id, q.filename, q.created_at, coalesce(json_extract(q.original, '$.grade'), ''),
                       '', json_object('count', q.exercise_count)
                FROM questions q
                WHERE json_extract(q.original, '$.kind') <> 'vertical'
                  AND NOT EXISTS (SELECT 1 FROM batch_questions b WHERE b.question_id = q.id)
            """)
            db.execute("""
                INSERT OR IGNORE INTO batch_questions (batch_id, question_id, position)
                SELECT b.id, q.id, 0 FROM batches b JOIN questions q ON q.id = b.id
            """)
            if "category" not in question_columns:
                db.execute("""
                    INSERT OR IGNORE INTO category_progress (batch_id, category, mastered)
                    SELECT link.batch_id, q.category, min(q.mastered)
                    FROM batch_questions link JOIN questions q ON q.id = link.question_id
                    GROUP BY link.batch_id, q.category
                """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_draft(self, filename: str, source: bytes, state: IntakeDraftState) -> str:
        draft_id = uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO intake_drafts (id, filename, state, source) VALUES (?, ?, ?, ?)",
                       (draft_id, filename, state.model_dump_json(), source))
        return draft_id

    def draft_summaries(self):
        with self.connect() as db:
            return db.execute("""
                SELECT id, filename, created_at, json_extract(state, '$.title') AS title,
                       json_array_length(state, '$.questions') AS count
                FROM intake_drafts ORDER BY created_at DESC, rowid DESC
            """).fetchall()

    def get_draft(self, draft_id: str) -> IntakeDraft | None:
        with self.connect() as db:
            row = db.execute("SELECT id, filename, created_at, revision, state FROM intake_drafts WHERE id = ?",
                             (draft_id,)).fetchone()
        if row is None:
            return None
        return IntakeDraft(row["id"], row["filename"], row["created_at"], row["revision"],
                           IntakeDraftState.model_validate_json(row["state"]))

    def draft_source(self, draft_id: str) -> bytes:
        with self.connect() as db:
            row = db.execute("SELECT source FROM intake_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise ValueError("找不到这条录入草稿。")
        return row["source"]

    def update_draft(self, draft: IntakeDraft):
        with self.connect() as db:
            result = db.execute("UPDATE intake_drafts SET state = ?, revision = revision + 1 "
                                "WHERE id = ? AND revision = ?",
                                (draft.state.model_dump_json(), draft.id, draft.revision))
            if result.rowcount != 1:
                raise ValueError("草稿已在其他窗口更新或删除，请返回录入首页重新打开。")

    @staticmethod
    def _remove_draft(db: sqlite3.Connection, draft: IntakeDraft):
        result = db.execute("DELETE FROM intake_drafts WHERE id = ? AND revision = ?", (draft.id, draft.revision))
        if result.rowcount != 1:
            raise ValueError("草稿已更改，请返回录入首页重新打开。")

    def delete_draft(self, draft: IntakeDraft):
        with self.connect() as db:
            self._remove_draft(db, draft)

    def save_intake(self, filename: str, records: list[tuple[Original, str, list[bytes], list[bytes]]],
                    grade: str, curriculum: str, plan: PracticePlan, *, uploaded_at: str | None = None,
                    queue_generation: bool = False, title: str = "", draft: IntakeDraft | None = None) -> str:
        batch_id = uuid4().hex
        with self.connect() as db:
            if draft is not None:
                self._remove_draft(db, draft)
            db.execute(
                "INSERT INTO batches (id, filename, grade, curriculum, plan, created_at, title)"
                " VALUES (?, ?, ?, ?, ?, coalesce(?, CURRENT_TIMESTAMP), ?)",
                (batch_id, filename, grade, curriculum, plan.model_dump_json(), uploaded_at, title.strip()),
            )
            for position, (original, description, figures, review_figures) in enumerate(records):
                question_id = uuid4().hex
                db.execute(
                    "INSERT INTO questions (id, filename, original, figure_description, exercise_count, category)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (question_id, filename, original.model_dump_json(), description, plan.count, category_name(original)),
                )
                db.executemany(
                    "INSERT INTO figures VALUES (?, ?, ?)",
                    [(question_id, i, png) for i, png in enumerate(figures)] +
                    [(question_id, -i, png) for i, png in enumerate(review_figures, 1)],
                )
                db.execute("INSERT INTO batch_questions VALUES (?, ?, ?)", (batch_id, question_id, position))
            if queue_generation:
                self.insert_job(db, batch_id, plan)
        return batch_id

    @staticmethod
    def insert_job(db: sqlite3.Connection, batch_id: str, plan: PracticePlan, action: str = "generate",
                   question_id: str | None = None, *, followup: Followup | None = None):
        job_id = uuid4().hex
        db.execute(
            "INSERT INTO generation_jobs (id, batch_id, plan, action, question_id, followup) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, batch_id, plan.model_dump_json(), action, question_id,
             followup.model_dump_json() if followup else None),
        )
        return job_id

    @staticmethod
    def read_mastered(db: sqlite3.Connection, batch_id: str) -> set[str]:
        return {row["category"] for row in db.execute(
            "SELECT category FROM category_progress WHERE batch_id = ? AND mastered = 1", (batch_id,)
        )}

    def mastered_categories(self, batch_id: str) -> set[str]:
        with self.connect() as db:
            return self.read_mastered(db, batch_id)

    def set_category_mastered(self, batch_id: str, category: str, mastered: bool):
        with self.connect() as db:
            db.execute("""
                INSERT INTO category_progress (batch_id, category, mastered) VALUES (?, ?, ?)
                ON CONFLICT(batch_id, category) DO UPDATE SET mastered = excluded.mastered
            """, (batch_id, category, mastered))

    def queue_followup(self, batch: Batch, message: str, *, plan: PracticePlan | None = None,
                       practice: FollowupPractice | None = None):
        message = message.strip()
        if not message:
            raise ValueError("请先输入你的问题或解题过程。")
        if len(message) > 1700:
            raise ValueError("消息太长，请缩短后重试。")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.ensure_idle(db, batch.id)
            current = db.execute("SELECT revision FROM batches WHERE id = ?", (batch.id,)).fetchone()
            if current is None or current["revision"] != batch.revision:
                raise ValueError("这份记录已更新，请刷新后重试。")
            followup = Followup(mastered=self.read_mastered(db, batch.id), practice=practice)
            action = "append" if practice or plan else "tutor"
            job_id = self.insert_job(db, batch.id, plan or batch.plan, action, followup=followup)
            db.execute("INSERT INTO record_messages VALUES (?, 'user', ?)", (job_id, message))

    def promote_followup(self, job: GenerationJob, followup: Followup):
        with self.connect() as db:
            result = db.execute("""
                UPDATE generation_jobs SET action = 'append', followup = ?
                WHERE id = ? AND action = 'tutor' AND state = 'running'
            """, (followup.model_dump_json(), job.id))
            if result.rowcount != 1:
                raise ValueError("这项对话任务已更新。")

    def record_messages(self, batch_id: str, *, limit: int | None = None) -> list[dict[str, str]]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT m.role, m.content FROM generation_jobs j JOIN (
                    SELECT job_id, role, content FROM record_messages
                    UNION ALL
                    SELECT job_id, role, content FROM question_messages
                ) m ON m.job_id = j.id
                WHERE j.batch_id = ? ORDER BY j.rowid DESC, m.role ASC LIMIT ?
            """, (batch_id, -1 if limit is None else limit)).fetchall()
        return [dict(row) for row in reversed(rows)]

    @staticmethod
    def complete_followup(db: sqlite3.Connection, job: GenerationJob, reply: str, message: str):
        db.execute("INSERT INTO record_messages VALUES (?, 'assistant', ?)", (job.id, reply))
        result = db.execute("UPDATE generation_jobs SET state = 'done', message = ? WHERE id = ? AND state = 'running'",
                            (message, job.id))
        if result.rowcount != 1:
            raise ValueError("这项练习任务已更新。")

    def save_tutor_reply(self, job: GenerationJob, reply: str):
        with self.connect() as db:
            self.complete_followup(db, job, reply, "AI 已回复")

    def rename_batch(self, batch_id: str, title: str):
        with self.connect() as db:
            result = db.execute("UPDATE batches SET title = ? WHERE id = ?", (title.strip(), batch_id))
            if result.rowcount != 1:
                raise ValueError("这份记录已删除，请刷新。")

    @staticmethod
    def ensure_idle(db: sqlite3.Connection, batch_id: str):
        if db.execute("""
            SELECT 1 FROM generation_jobs WHERE batch_id = ? AND state IN ('queued', 'running')
        """, (batch_id,)).fetchone():
            raise ValueError("这份记录正在后台处理，请等当前任务完成。")

    def delete_batch(self, batch_id: str):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.ensure_idle(db, batch_id)
            db.execute("""
                DELETE FROM questions WHERE id IN (SELECT question_id FROM batch_questions WHERE batch_id = ?)
            """, (batch_id,))
            db.execute("DELETE FROM batches WHERE id = ?", (batch_id,))

    def queue_pdf(self, batch: Batch):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.ensure_idle(db, batch.id)
            row = db.execute("SELECT tex IS NOT NULL AS has_tex, pdf IS NOT NULL AS has_pdf FROM batches WHERE id = ?",
                             (batch.id,)).fetchone()
            if row is None or not row["has_tex"]:
                raise ValueError("这份记录还没有 TeX 源码。")
            if not row["has_pdf"]:
                self.insert_job(db, batch.id, batch.plan, "compile")

    def queue_edit(self, batch: Batch, plan: PracticePlan,
                   corrections: list[tuple[Question, Original, str]], *, title: str | None = None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.ensure_idle(db, batch.id)
            row = db.execute("SELECT revision FROM batches WHERE id = ?", (batch.id,)).fetchone()
            if row is None or row["revision"] != batch.revision:
                raise ValueError("这份记录已更新，请刷新后重试。")
            if title is not None:
                db.execute("UPDATE batches SET title = ? WHERE id = ?", (title.strip(), batch.id))
            for question, original, description in corrections:
                self.correct(db, question, original, description)
            job_id = self.insert_job(db, batch.id, plan)
            previous = db.execute("""
                SELECT id, plan FROM generation_jobs WHERE batch_id = ? AND id <> ? AND action = 'generate'
                ORDER BY rowid DESC LIMIT 1
            """, (batch.id, job_id)).fetchone()
            if previous and PracticePlan.model_validate_json(previous["plan"]) == plan:
                db.execute("""
                    INSERT INTO generation_checkpoints (job_id, question_id, state)
                    SELECT ?, question_id, state FROM generation_checkpoints WHERE job_id = ?
                """, (job_id, previous["id"]))
                db.executemany("DELETE FROM generation_checkpoints WHERE job_id = ? AND question_id = ?",
                               [(job_id, question.id) for question, _, _ in corrections])
            return job_id

    @staticmethod
    def decode_job(row: sqlite3.Row) -> GenerationJob:
        followup = row["followup"]
        return GenerationJob(row["id"], row["batch_id"], PracticePlan.model_validate_json(row["plan"]),
                             row["state"], row["message"], row["debug_id"], row["action"], row["question_id"],
                             Followup.model_validate_json(followup) if followup else None)

    def latest_job(self, batch_id: str) -> GenerationJob | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM generation_jobs WHERE batch_id = ? ORDER BY rowid DESC LIMIT 1",
                             (batch_id,)).fetchone()
        return self.decode_job(row) if row else None

    def active_jobs(self) -> list[GenerationJob]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM generation_jobs WHERE state IN ('queued', 'running')").fetchall()
        return [self.decode_job(row) for row in rows]

    def claim_job(self, job_id: str) -> GenerationJob | None:
        with self.connect() as db:
            row = db.execute("""
                UPDATE generation_jobs SET state = 'running', message = '正在恢复或开始生成'
                WHERE id = ? AND state IN ('queued', 'running') RETURNING *
            """, (job_id,)).fetchone()
        return self.decode_job(row) if row else None

    def update_job(self, job_id: str, message: str, *, state: str = "running", debug_id: str | None = None):
        with self.connect() as db:
            db.execute("""
                UPDATE generation_jobs SET state = ?, message = ?, debug_id = coalesce(?, debug_id)
                WHERE id = ? AND state = 'running'
            """, (state, message, debug_id, job_id))

    def checkpoint(self, job_id: str, question_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT state FROM generation_checkpoints WHERE job_id = ? AND question_id = ?",
                             (job_id, question_id)).fetchone()
        return json.loads(row["state"]) if row else {}

    def save_checkpoint(self, job_id: str, question_id: str, state: dict):
        with self.connect() as db:
            db.execute("""
                INSERT INTO generation_checkpoints VALUES (?, ?, ?)
                ON CONFLICT(job_id, question_id) DO UPDATE SET state = excluded.state
            """, (job_id, question_id, json.dumps(state, ensure_ascii=False)))

    def batches(self) -> list[Batch]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT id, filename, created_at, grade, curriculum, plan, revision, title
                FROM batches ORDER BY created_at DESC, rowid DESC
            """).fetchall()
        return [
            Batch(row["id"], row["filename"], row["created_at"], row["grade"], row["curriculum"],
                  PracticePlan.model_validate_json(row["plan"]), row["revision"], row["title"])
            for row in rows
        ]

    def search_batches(self, keyword: str) -> set[str]:
        with self.connect() as db:
            db.create_function("casefold", 1, lambda value: (value or "").casefold())
            rows = db.execute("""
                SELECT id FROM batches WHERE instr(casefold(title || ' ' || filename), ?)
                UNION
                SELECT b.batch_id FROM batch_questions b JOIN questions q ON q.id = b.question_id
                WHERE instr(casefold(coalesce(json_extract(q.original, '$.topic'), '') || ' ' ||
                                     coalesce(json_extract(q.original, '$.knowledge'), '') || ' ' ||
                                     coalesce(json_extract(q.original, '$.statement'), '')), ?)
            """, (keyword, keyword)).fetchall()
        return {row[0] for row in rows}

    def batch_questions(self, batch_id: str) -> list[Question]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT q.id, q.filename, q.created_at, q.original, q.figure_description, q.exercise_count, q.exercises,
                       q.category
                FROM batch_questions b JOIN questions q ON q.id = b.question_id
                WHERE b.batch_id = ? ORDER BY b.position
            """, (batch_id,)).fetchall()
        return [self.decode(row) for row in rows]

    def batch_result(self, batch_id: str) -> tuple[bytes | None, bytes | None, list[str]]:
        with self.connect() as db:
            row = db.execute("SELECT pdf, tex, warnings FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if row is None:
            raise ValueError("这份记录已删除，请刷新。")
        return row["pdf"], row["tex"], json.loads(row["warnings"])

    def get_batch(self, batch_id: str) -> Batch:
        for batch in self.batches():
            if batch.id == batch_id:
                return batch
        raise ValueError("这份记录已删除，请刷新。")

    def save_batch(self, batch: Batch, plan: PracticePlan, changes: list[tuple[Question, list[Exercise]]],
                   tex: bytes | None, warnings: list[str], *, followup: GenerationJob | None = None,
                   reply: str = ""):
        with self.connect() as db:
            updated = db.execute("""
                UPDATE batches SET plan = ?, pdf = CASE WHEN tex = ? THEN pdf END,
                    tex = ?, warnings = ?, revision = revision + 1
                WHERE id = ? AND revision = ?
            """, (plan.model_dump_json(), tex, tex, json.dumps(warnings, ensure_ascii=False),
                  batch.id, batch.revision))
            if updated.rowcount != 1:
                raise ValueError("这份记录已更新，请刷新后重试。")
            for question, exercises in changes:
                db.execute("UPDATE questions SET exercises = ?, exercise_count = ? WHERE id = ?",
                           (json.dumps([item.model_dump() for item in exercises], ensure_ascii=False),
                            plan.count, question.id))
            if followup:
                self.complete_followup(db, followup, reply, "已追加同类练习，可编译新版 PDF")

    def save_pdf(self, batch: Batch, tex: bytes, pdf: bytes):
        with self.connect() as db:
            result = db.execute("UPDATE batches SET pdf = ? WHERE id = ? AND revision = ? AND tex = ?",
                                (pdf, batch.id, batch.revision, tex))
            if result.rowcount != 1:
                raise ValueError("TeX 已更新，请重新编译。")

    def restore_tex(self, batch: Batch, tex: bytes, *, replace_existing: bool = False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.ensure_idle(db, batch.id)
            result = db.execute("""
                UPDATE batches SET tex = ?, pdf = NULL, revision = revision + 1
                WHERE id = ? AND revision = ? AND (tex IS NULL OR ?)
            """, (tex, batch.id, batch.revision, replace_existing))
            if result.rowcount != 1:
                raise ValueError("这份记录已更新，请刷新。")

    def questions(self) -> list[Question]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT id, filename, created_at, original, figure_description, exercise_count, exercises, category
                FROM questions ORDER BY created_at DESC, rowid DESC
            """).fetchall()
        return [self.decode(row) for row in rows]

    def get(self, question_id: str) -> Question:
        with self.connect() as db:
            row = db.execute("SELECT id, filename, created_at, original, figure_description,"
                             " exercise_count, exercises, category FROM questions WHERE id = ?",
                             (question_id,)).fetchone()
        if row is None:
            raise ValueError("这条记录已删除，请刷新列表。")
        return self.decode(row)

    @staticmethod
    def decode(row: sqlite3.Row) -> Question:
        return Question(
            id=row["id"], filename=row["filename"], created_at=row["created_at"],
            original=Original.model_validate_json(row["original"]),
            figure_description=row["figure_description"], exercise_count=row["exercise_count"],
            exercises=[Exercise.model_validate(item) for item in json.loads(row["exercises"] or "[]")],
            original_json=row["original"],
            category=row["category"],
        )

    def figures(self, question_id: str, *, for_review: bool = False) -> list[bytes]:
        with self.connect() as db:
            return [row["png"] for row in db.execute(
                """SELECT png FROM figures
                   WHERE question_id = ? AND (position >= 0 OR ?)
                   ORDER BY position < 0, abs(position)""",
                (question_id, for_review),
            )]

    @staticmethod
    def correct(db: sqlite3.Connection, question: Question, original: Original, figure_description: str):
        result = db.execute(
            "UPDATE questions SET original = ?, figure_description = ?, exercises = NULL"
            " WHERE id = ? AND original = ? AND figure_description = ?",
            (original.model_dump_json(), figure_description, question.id, question.original_json,
             question.figure_description),
        )
        if result.rowcount != 1:
            raise ValueError("记录已变更，请刷新后查看。")
        db.execute("""
            UPDATE batches SET revision = revision + 1 WHERE id = (
                SELECT batch_id FROM batch_questions WHERE question_id = ?
            )
        """, (question.id,))
        if figure_description != question.figure_description:
            db.execute(
                "UPDATE figures SET position = -position - 1 - COALESCE("
                "(SELECT max(abs(position)) FROM figures WHERE question_id = ?), 0)"
                " WHERE question_id = ? AND position >= 0",
                (question.id, question.id),
            )

    def save_exercises(self, question: Question, original: Original, exercises: list[Exercise]):
        payload = json.dumps([exercise.model_dump() for exercise in exercises], ensure_ascii=False)
        with self.connect() as db:
            result = db.execute(
                "UPDATE questions SET original = ?, exercises = ?"
                " WHERE id = ? AND exercises IS NULL AND original = ? AND figure_description = ?",
                (original.model_dump_json(), payload, question.id, question.original_json,
                 question.figure_description),
            )
            if result.rowcount != 1:
                raise ValueError("记录已变更，请刷新后查看。")

    def save_export(self, question_id: str, pdf: bytes, tex_zip: bytes):
        with self.connect() as db:
            result = db.execute(
                "UPDATE questions SET pdf = ?, tex_zip = ? WHERE id = ?", (pdf, tex_zip, question_id)
            )
            if result.rowcount != 1:
                raise ValueError("这条记录已删除，请刷新列表。")

    def export(self, question_id: str) -> tuple[bytes, bytes] | None:
        with self.connect() as db:
            row = db.execute("SELECT pdf, tex_zip FROM questions WHERE id = ?", (question_id,)).fetchone()
        if row and row["pdf"] is not None:
            return row["pdf"], row["tex_zip"]
        return None

    def delete(self, question_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM questions WHERE id = ?", (question_id,))
