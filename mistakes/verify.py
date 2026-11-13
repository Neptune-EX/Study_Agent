"""Exact substitution for a narrow set of arithmetic fill-in questions."""
import ast
from fractions import Fraction
import operator
import re
import unicodedata

from .models import Exercise

_OPERATIONS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_NUMBER = r"[+-]?\d{1,12}(?:\.\d{1,8})?(?:/[+-]?\d{1,12})?"
_BLANK = re.compile(r"_{3,}")


def _value(node: ast.AST, source: str) -> Fraction:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Fraction(ast.get_source_segment(source, node))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _value(node.operand, source)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATIONS:
        return _OPERATIONS[type(node.op)](_value(node.left, source), _value(node.right, source))
    raise ValueError("unsupported arithmetic")


def substitution(exercise: Exercise) -> bool | None:
    """Return None when the statement needs semantic checking."""
    if exercise.options:
        return None
    statement = unicodedata.normalize("NFKC", exercise.statement).strip().rstrip("。.")
    statement = statement.translate(str.maketrans("×÷−", "*/-"))
    answer = unicodedata.normalize("NFKC", exercise.answer).strip()
    if (len(statement) > 100 or not re.fullmatch(r"[0-9+*/(). _=\-]+", statement)
            or len(_BLANK.findall(statement)) != 1 or statement.count("=") != 1
            or not re.fullmatch(_NUMBER, answer)):
        return None
    left, right = _BLANK.sub(f"({answer})", statement).split("=")
    try:
        left, right = left.strip(), right.strip()
        return _value(ast.parse(left, mode="eval").body, left) == _value(ast.parse(right, mode="eval").body, right)
    except (SyntaxError, ValueError, ZeroDivisionError):
        return None
