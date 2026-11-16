from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pymupdf
from streamlit.testing.v1 import AppTest

from mistakes import intake_ui
from mistakes.models import Intake, IntakeReply, TextPageReading
from mistakes.store import Store


class FakeModel:
    def __init__(self):
        self.recognized = []
        self.corrected = []

    def recognize(self, page, image, selected):
        self.recognized.append(page)
        return TextPageReading(fragments=[
            {"text": "500 + 250 = ______", "number": "5", "kind": "blank"},
            {"text": "100 + 50 = ______", "number": "6", "kind": "blank"},
        ] if page == 2 else [])

    def intake(self, groups, *args):
        return Intake(questions=[
            {"kind": "blank", "topic": "加法", "statement": group[0]["text"],
             "difficulty": "basic", "sources": [group[0]["id"]]}
            for group in groups
        ])

    def correct_intake(self, page, image, grade, questions, history, message):
        self.corrected.append((page, image, questions, message))
        return IntakeReply.model_validate({
            "reply": "已根据原页修正第 5 题并补录第 8 题。",
            "corrections": [
                {"question_id": questions[0]["question_id"], "question": {
                    "number": "5", "statement": "500 + 375 = ______", "knowledge": "加法", "kind": "blank"}},
                {"question": {
                    "number": "8", "statement": "48 ÷ 6 = ______", "knowledge": "除法", "kind": "blank"}},
            ],
        })


class IntakeDraftTest(unittest.TestCase):
    @staticmethod
    def widget(elements, label):
        return next(element for element in elements if element.label == label)

    def render(self, app):
        app.run(timeout=15)
        self.assertFalse(app.exception, [error.message for error in app.exception])
        return app

    def click(self, app, label, index=0):
        [button for button in app.button if button.label == label][index].click()
        self.render(app)

    def test_resume_edit_and_submit_after_image_correction(self):
        with pymupdf.open() as pdf:
            pdf.new_page()
            pdf.new_page().insert_text((50, 70), "500 + 375 = ____ ; 48 / 6 = ____")
            source = pdf.tobytes()
        fake = FakeModel()
        with TemporaryDirectory() as root, patch.object(intake_ui, "start_worker"), \
                patch.object(intake_ui, "DeepSeek", return_value=fake):
            path = Path(root) / "mistakes.sqlite3"
            store = Store(path)
            script = (
                "from pathlib import Path\nfrom mistakes.store import Store\n"
                "from mistakes.intake_ui import upload\n"
                f"upload(Store(Path({str(path)!r})))\n"
            )
            app = self.render(AppTest.from_string(script))
            app.file_uploader[0].set_value(("exercise.pdf", source, "application/pdf"))
            self.render(app)
            self.widget(app.radio, "错题范围").set_value("整份都是错题")
            self.render(app)
            self.widget(app.selectbox, "年级").set_value("三年级")
            self.click(app, "识别题目与知识点")
            draft_id = store.draft_summaries()[0]["id"]
            self.assertEqual(store.draft_source(draft_id), source)

            self.widget(app.text_input, "记录名称").set_value("补录后的练习")
            self.widget(app.text_area, "题干").set_value("500 + 325 = ______")
            self.click(app, "返回录入首页")
            app = self.render(AppTest.from_string(script))
            self.click(app, "继续核对")
            self.click(app, "调整识别范围")
            self.click(app, "返回核对草稿")
            self.assertEqual(self.widget(app.text_input, "记录名称").value, "补录后的练习")
            self.assertEqual(self.widget(app.text_area, "题干").value, "500 + 325 = ______")
            self.click(app, "删除这道题", index=1)
            self.click(app, "添加一道题")
            self.assertEqual(len(store.get_draft(draft_id).state.questions), 2)
            self.click(app, "确认并生成练习")
            self.assertTrue(app.error)
            self.click(app, "删除这道题", index=1)

            self.widget(app.number_input, "原图页码").set_value(2)
            self.widget(app.checkbox, "查看原页").set_value(True)
            self.render(app)
            app.chat_input[0].set_value("第 5 题的加数应为 375，此外漏了第 8 题。")
            self.render(app)
            self.assertEqual(len(store.get_draft(draft_id).state.corrections), 2)
            self.assertEqual(self.widget(app.text_area, "题干").value, "500 + 325 = ______")
            page, image, questions, _ = fake.corrected[0]
            self.assertEqual(page, 2)
            self.assertTrue(image.startswith(b"\x89PNG"))
            self.assertEqual([q["statement"] for q in questions], ["500 + 325 = ______"])

            self.click(app, "返回录入首页")
            app = self.render(AppTest.from_string(script))
            self.click(app, "继续核对")
            self.assertEqual(len(app.chat_message), 2)
            self.click(app, "应用建议")
            expected = ["500 + 375 = ______", "48 ÷ 6 = ______"]
            self.assertEqual([area.value for area in app.text_area if area.label == "题干"], expected)
            self.click(app, "确认并生成练习")
            self.assertFalse(store.draft_summaries())
            batch, = store.batches()
            self.assertEqual(batch.title, "补录后的练习")
            self.assertEqual([q.original.statement for q in store.batch_questions(batch.id)], expected)
            self.assertEqual(store.latest_job(batch.id).state, "queued")
            self.assertEqual(fake.recognized, [1, 2])
            self.assertEqual(len(fake.corrected), 1)
