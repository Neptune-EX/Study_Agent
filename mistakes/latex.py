import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

from .debug import record
from .store import Question
from .models import Diagram, PracticePlan

TEMPLATES = Path(__file__).resolve().parents[1] / "latex_board"
ORIGINALS_MARKER = b"% worksheet: originals included"
SPECIAL = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$",
    "&": r"\&", "#": r"\#", "%": r"\%", "_": r"\_", "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}", "\n": r"\newline{}", "\t": " ",
}
TEXT_SYMBOLS = {
    "×": r"\(\times\)", "÷": r"\(\div\)", "−": r"\(-\)", "≤": r"\(\leq\)",
    "≥": r"\(\geq\)", "≠": r"\(\neq\)", "≈": r"\(\approx\)", "√": r"\(\sqrt{}\)",
    "π": r"\(\pi\)", "°": r"\({}^\circ\)", "²": r"\({}^2\)", "³": r"\({}^3\)",
}
MATH_SYMBOLS = {
    "×": r"\times ", "÷": r"\div ", "−": "-", "≤": r"\leq ", "≥": r"\geq ",
    "≠": r"\neq ", "≈": r"\approx ", "°": r"^\circ ", "π": r"\pi ",
}
MATH_COMMANDS = frozenset(
    "frac dfrac tfrac sqrt times div cdot pm mp le leq ge geq neq approx pi theta alpha beta "
    "gamma angle circ perp parallel ldots dots text mathrm overline triangle sin cos tan quad".split()
)
MATH_TOKEN = re.compile(r"\\[a-zA-Z]+|[a-zA-Z0-9 \t{}+\-*/=().,:;<>!|^_'\[\]]")
INLINE = re.compile(r"\$([^$\n]+)\$|_{3,}")


class CompileError(RuntimeError):
    pass


def escape(text: str) -> str:
    return "".join(
        SPECIAL.get(char, TEXT_SYMBOLS.get(char, char))
        for char in text if ord(char) >= 32 or char in "\n\t"
    )


def inline_math(source: str) -> str | None:
    source = "".join(MATH_SYMBOLS.get(char, char) for char in source)
    if "^^" in source:
        return None
    depth = 0
    position = 0
    for match in MATH_TOKEN.finditer(source):
        token = match.group()
        if match.start() != position or (token.startswith("\\") and token[1:] not in MATH_COMMANDS):
            return None
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth < 0:
                return None
        position = match.end()
    return source if position == len(source) and depth == 0 else None


def typeset(text: str, math: bool) -> str:
    chunks = []
    position = 0
    for match in INLINE.finditer(text):
        chunks.append(escape(text[position:match.start()]))
        expression = inline_math(match[1]) if match[1] and math else None
        if expression is not None:
            chunks.append(r"\(" + expression + r"\)")
        elif match[1]:
            chunks.append(escape(match[0]))
        else:
            chunks.append(r"\underline{\hspace{1.8cm}}")
        position = match.end()
    chunks.append(escape(text[position:]))
    return "".join(chunks)


def draw_diagram(diagram: Diagram, math: bool) -> str:
    points = [point for path in diagram.paths for point in path.points]
    for circle in diagram.circles:
        x, y = circle.center
        points.extend([(x - circle.radius, y - circle.radius), (x + circle.radius, y + circle.radius)])
    xs, ys = zip(*points)
    scale = min(0.28, 8.6 / max(1, max(xs) - min(xs), max(ys) - min(ys)))
    lines = [
        r"\begin{center}",
        rf"\begin{{tikzpicture}}[x={scale:.4f}cm,y={scale:.4f}cm,>=Stealth,thick]",
    ]
    for path in diagram.paths:
        arrow = {"none": "", "end": "->", "both": "<->"}[path.arrows]
        options = ",".join(filter(None, (arrow, "dashed" if path.dashed else "")))
        points = " -- ".join(f"({x:.3f},{y:.3f})" for x, y in path.points)
        lines.append(rf"\draw[{options}] {points}" + (" -- cycle;" if path.closed else ";"))
    for circle in diagram.circles:
        x, y = circle.center
        options = "dashed" if circle.dashed else ""
        lines.append(rf"\draw[{options}] ({x:.3f},{y:.3f}) circle[radius={circle.radius:.3f}];")
    occupied = []
    for label in diagram.labels:
        x, y = label.position
        anchor = "" if label.anchor == "center" else label.anchor
        dx, dy = {"above": (0, 1), "below": (0, -1), "left": (-1, 0), "right": (1, 0),
                  "center": (0, 1)}[label.anchor]
        width = min(180, 5 + sum(9 if ord(char) > 127 else 5 for char in label.text))
        px, py = x * scale * 28.45, y * scale * 28.45
        center_x = px + dx * (width / 2 + 3) if anchor else px
        center_y = py + dy * 8 if anchor else py
        shift = 0
        while True:
            cx, cy = center_x + dx * shift, center_y + dy * shift
            box = (cx - width / 2, cy - 6, cx + width / 2, cy + 6)
            if not any(box[0] < right and box[2] > left and box[1] < top and box[3] > bottom
                       for left, bottom, right, top in occupied):
                break
            shift += 4
        occupied.append(box)
        node = f"label{len(occupied)}"
        options = ",".join(filter(None, (anchor, r"font=\small", f"xshift={dx * shift}pt", f"yshift={dy * shift}pt")))
        lines.append(rf"\node[{options}] ({node}) at ({x:.3f},{y:.3f}) "
                     + "{" + typeset(label.text, math) + "};")
        if shift >= 8:
            lines.append(rf"\draw[gray,thin] ({x:.3f},{y:.3f}) -- ({node});")
    return "\n".join([*lines, r"\end{tikzpicture}", r"\end{center}"])


def render_batch(questions: list[Question], plan: PracticePlan, math: bool) -> str:
    has_legacy_diagrams = plan.allow_diagrams and any(
        question.original.kind == "geometry" and any(item.diagram for item in question.exercises)
        for question in questions
    )
    template = "图形题错题模板.tex" if has_legacy_diagrams else "应用题错题模板.tex"
    source = (TEMPLATES / template).read_text(encoding="utf-8-sig")
    preamble, body = source.split(r"\begin{document}", 1)
    before, _, examples = body.partition(r"\begin{problem}")
    last_problem = list(re.finditer(r"\\end\{problem(?:fig)?\}", examples))[-1]
    after = examples[last_problem.end():].lstrip("\n\t ")
    # Keep the template's own document setup, problem spacing and page breaks.
    lines = [ORIGINALS_MARKER.decode("ascii"), preamble + "\\usepackage{amsmath}\n" + r"\begin{document}" + before.rstrip()]
    answers = []
    number = 0
    for question in questions:
        original = question.original
        knowledge = typeset(" ".join((original.knowledge or original.topic).split()), math)
        for index, item in enumerate([original, *question.exercises]):
            number += 1
            title = knowledge if index else f"{knowledge}（原题）"
            lines.extend([rf"\begin{{problem}}{{{number:02d}}}{{{title}}}", typeset(item.statement, math)])
            for option_index, option in enumerate(item.options):
                lines.append(r"\par\smallskip " + f"{chr(65 + option_index)}. " + typeset(option, math))
            if original.kind == "geometry" and plan.allow_diagrams and item.diagram:
                lines.append(draw_diagram(item.diagram, math))
            elif not index and original.kind == "geometry" and question.figure_description:
                lines.append(r"\par " + typeset(question.figure_description, math))
            lines.append(r"\end{problem}")
            if item.answer:
                answers.extend([rf"\subsection*{{第 {number} 题}}", typeset(item.answer, math) + r"\par",
                                typeset(item.solution, math) + r"\par"])
    return "\n".join([*lines, r"\clearpage", r"\section*{参考答案与解析}", *answers, after])


def compile_batch(questions: list[Question], plan: PracticePlan) -> tuple[bytes, bytes]:
    return compile_tex(lambda math: render_batch(questions, plan, math))


def compile_tex(render) -> tuple[bytes, bytes]:
    for math in (True, False):
        tex = render(math).encode("utf-8")
        try:
            return compile_source(tex), tex
        except CompileError as exc:
            if not math or "worksheet.tex:" not in str(exc):
                raise
    raise AssertionError("no compilation attempted")


def compile_source(tex: bytes) -> bytes:
    executable = shutil.which(os.getenv("XELATEX", "xelatex"))
    if not executable:
        record("tex.error", reason="找不到 XeLaTeX")
        raise CompileError("找不到 XeLaTeX。请安装 TeX Live 或在 .env 中设置 XELATEX。")
    with TemporaryDirectory(prefix="mistake-tex-") as temporary:
        directory = Path(temporary)
        pdf_path = directory / "worksheet.pdf"
        (directory / "worksheet.tex").write_bytes(tex)
        try:
            result = subprocess.run(
                [executable, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
                 "-file-line-error", "worksheet.tex"],
                cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=90, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            record("tex.error", reason="编译超时")
            raise CompileError("XeLaTeX 编译超时，请检查本机 TeX 安装。") from exc
        if result.returncode == 0 and pdf_path.is_file():
            pdf = pdf_path.read_bytes()
            record("tex.compiled", pdf_bytes=len(pdf))
            return pdf
        output = result.stdout.decode("utf-8", errors="replace").splitlines()
        record("tex.error", returncode=result.returncode, output_tail=output[-25:])
        raise CompileError("XeLaTeX 编译失败。\n" + "\n".join(output[-14:]))
