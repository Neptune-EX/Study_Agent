import json
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from mistakes.deepseek import DeepSeek, ModelError, ModelOutputError
from mistakes.models import ClassifiedOriginal, Intake, TextPageReading
from mistakes.workflow import analyze_upload


def fragment(number, text, **fields):
    return {"kind": "blank", "number": number, "text": text, **fields}


def organized(sources, statement):
    return ClassifiedOriginal(
        sources=sources, kind="blank", topic="除法", statement=statement, difficulty="basic",
    )


class MarkedIntakeTests(unittest.TestCase):
    def analyze(self, model, page_fragments, *, marked_only=True):
        model.recognize.side_effect = [TextPageReading(fragments=page) for page in page_fragments]
        pages = [(number, b"image") for number in range(1, len(page_fragments) + 1)]
        with patch("mistakes.workflow.read_pages", return_value=iter(pages)):
            return analyze_upload(model, "paper.pdf", b"pdf", "", "三年级", [], lambda _: None,
                                  marked_only=marked_only)

    def test_filters_before_organizing_and_retains_a_marked_continuation(self):
        model = Mock()
        model.intake.return_value = Intake(questions=[
            organized(["1-3", "2-1"], "42 ÷ 6 = ______"),
        ])
        records, warnings = self.analyze(model, [
            [
                fragment("1", "21 ÷ 3 = ______", has_marks=False),
                fragment("2", "27 ÷ 3 = ______"),
                fragment("3", "42 ÷", has_marks=False),
            ],
            [fragment("3", "6 = ______", has_marks=True, continuation=True)],
        ])
        groups = model.intake.call_args.args[0]
        self.assertEqual([[item["id"] for item in group] for group in groups], [["1-3", "2-1"]])
        self.assertEqual([record[0].number for record in records], ["3"])
        self.assertTrue(any("第 1 页第 2 题" in warning and "无法确认" in warning for warning in warnings))

    def test_no_confirmed_marks_skips_organizing_and_keeps_a_warning(self):
        model = Mock()
        records, warnings = self.analyze(model, [
            [fragment("5", "49 ÷ 7 = ______", has_marks=False), fragment("6", "8 ÷ 4 = ______", has_marks=None)],
        ])
        model.intake.assert_not_called()
        self.assertEqual(records, [])
        self.assertTrue(any("未识别到" in warning for warning in warnings))
        self.assertTrue(any("第 1 页第 6 题" in warning and "无法确认" in warning for warning in warnings))

    def test_retries_only_unmatched_questions_then_keeps_raw_text_in_order(self):
        model = Mock()
        model.intake.side_effect = [
            Intake(questions=[
                organized(["1-1", "1-2"], "这段题干混入了另一题"),
                organized(["1-2"], "24 ÷ 6 = ______ 分钟"),
            ]),
            ModelOutputError("Flash 整理原题未返回完整、有效的 JSON"),
        ]
        records, warnings = self.analyze(model, [[
            fragment("4", "42 ÷ 7 = ______", has_marks=True),
            fragment("5", "24 ÷ 6 = ______ 分钟", has_marks=True),
        ]])
        self.assertEqual(model.intake.call_count, 2)
        self.assertEqual([[item["id"] for item in group] for group in model.intake.call_args.args[0]], [["1-1"]])
        fallback, matched = [record[0] for record in records]
        self.assertEqual((fallback.number, fallback.sources, fallback.statement), ("4", ["1-1"], "42 ÷ 7 = ______"))
        self.assertIn("核对", fallback.uncertainty)
        self.assertEqual((matched.number, matched.statement), ("5", "24 ÷ 6 = ______ 分钟"))
        self.assertTrue(any("保留识图原文" in warning for warning in warnings))

    def test_service_errors_are_not_mistaken_for_invalid_model_output(self):
        model = Mock()
        model.intake.side_effect = ModelError("连接失败", retryable=True)
        with self.assertRaisesRegex(ModelError, "连接失败"):
            self.analyze(model, [[fragment("4", "42 ÷ 7 = ______", has_marks=True)]])

    def test_check_across_working_is_excluded_but_circled_number_is_retained(self):
        model = DeepSeek.__new__(DeepSeek)
        model.flash = Mock()
        model.flash.invoke.return_value = AIMessage(content=json.dumps({"fragments": [
            fragment("", "(352−289)÷7", student_work="=63÷7\n=9", teacher_mark="check"),
            fragment("2", "24 ÷ 6 = ______", teacher_mark="circled_number"),
            fragment("3", "56 ÷ 7 = ______", teacher_mark="uncertain"),
        ]}))
        with patch("mistakes.workflow.read_pages", return_value=iter([(1, b"image")])), \
                patch.object(model, "intake", return_value=Intake(questions=[
                    organized(["1-2"], "24 ÷ 6 = ______"),
                ])) as organize:
            records, warnings = analyze_upload(model, "paper.png", b"image", "", "三年级", [],
                                               lambda _: None, marked_only=True)

        self.assertEqual([item["id"] for group in organize.call_args.args[0] for item in group], ["1-2"])
        self.assertEqual([record[0].number for record in records], ["2"])
        self.assertTrue(any("第 1 页第 3 题" in warning and "无法确认" in warning for warning in warnings))
