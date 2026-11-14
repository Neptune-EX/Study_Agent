"""Exact substitution for a narrow set of arithmetic fill-in questions."""
import ast
from fractions import Fraction
import operator
import re
import unicodedata

from .models import Exercise

_OPERATIONS = {ast.Add: operator.add, ast.Sub: operator.sub}
_NUMBER = r"[+-]?\d{1,12}(?:\.\d{1,8})?(?:/[+-]?\d{1,12})?"
_BLANK = re.compile(r"_{3,}")


def _linear(node: ast.AST, source: str) -> tuple[Fraction, Fraction]:
    """Return the coefficient of the blank and the constant term."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Fraction(0), Fraction(ast.get_source_segment(source, node))
    if isinstance(node, ast.Name) and node.id == "x":
        return Fraction(1), Fraction(0)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        a, b = _linear(node.operand, source)
        return (-a, -b) if isinstance(node.op, ast.USub) else (a, b)
    if isinstance(node, ast.BinOp):
        a, b = _linear(node.left, source)
        c, d = _linear(node.right, source)
        if type(node.op) in _OPERATIONS:
            op = _OPERATIONS[type(node.op)]
            return op(a, c), op(b, d)
        if isinstance(node.op, ast.Mult) and not (a and c):
            return a * d + c * b, b * d
        if isinstance(node.op, ast.Div) and not c:
            return a / d, b / d
    raise ValueError("unsupported arithmetic")


def arithmetic_check(exercise: Exercise) -> tuple[str, str] | None:
    """Return ('pass', ''), an issue, or None for semantic checking."""
    if exercise.options:
        return None
    statement = unicodedata.normalize("NFKC", exercise.statement).strip().rstrip("。.")
    statement = statement.translate(str.maketrans("×÷−", "*/-"))
    answer = unicodedata.normalize("NFKC", exercise.answer).strip()
    if (len(statement) > 100 or not re.fullmatch(r"[0-9+*/(). _=\-]+", statement)
            or len(_BLANK.findall(statement)) != 1 or statement.count("=") != 1):
        return None
    left, right = _BLANK.sub("(x)", statement).split("=")
    try:
        left, right = left.strip(), right.strip()
        a, b = _linear(ast.parse(left, mode="eval").body, left)
        c, d = _linear(ast.parse(right, mode="eval").body, right)
        if a == c:
            return "question", "等式没有唯一答案。"
        if not re.fullmatch(_NUMBER, answer):
            return None
        correct = (d - b) / (a - c)
        try:
            given = Fraction(answer)
        except (ValueError, ZeroDivisionError):
            return "answer", f"答案不是有效数值，正确结果为 {correct}。"
        if given != correct:
            return "answer", f"答案代入不成立，正确结果为 {correct}。"
        return "pass", ""
    except ZeroDivisionError:
        return "question", "等式含有除以零。"
    except (SyntaxError, ValueError):
        return None
