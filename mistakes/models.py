from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MistakeKind = Literal["choice", "blank", "word", "geometry"]
Kind = Literal["choice", "blank", "word", "geometry", "vertical"]
QuestionDifficulty = Literal["basic", "advanced"]
DIFFICULTY_LABELS = {"basic": "基础 · Flash", "advanced": "进阶 · Pro"}
MISTAKE_LABELS = {"choice": "选择题", "blank": "填空题", "word": "文字应用题", "geometry": "图形应用题"}
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Statement = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Coordinate = Annotated[float, Field(ge=0, le=100)]
Point = tuple[Coordinate, Coordinate]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TextFragment(Model):
    text: Statement
    number: str = Field(default="", max_length=35)
    kind: MistakeKind | Literal["vertical", "other"]
    knowledge: str = Field(default="", max_length=120)
    continuation: bool = False
    student_work: str = Field(default="", max_length=2000)
    requires_figure: bool = False
    uncertainty: str = Field(default="", max_length=1500)


class TextPageReading(Model):
    fragments: list[TextFragment] = Field(max_length=60)


class PathShape(Model):
    points: list[Point] = Field(min_length=2, max_length=50)
    closed: bool = False
    dashed: bool = False
    arrows: Literal["none", "end", "both"] = "none"

    @model_validator(mode="after")
    def check_points(self):
        if len(set(self.points)) < (3 if self.closed else 2):
            raise ValueError("封闭轮廓至少要有三个不同的点，线段至少要有两个不同的点")
        return self


class Circle(Model):
    center: Point
    radius: float = Field(gt=0, le=50)
    dashed: bool = False


class DiagramLabel(Model):
    position: Point
    text: Title
    anchor: Literal["above", "below", "left", "right", "center"] = "center"


class Diagram(Model):
    paths: list[PathShape] = Field(default_factory=list, max_length=30)
    circles: list[Circle] = Field(default_factory=list, max_length=15)
    labels: list[DiagramLabel] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def check_shapes(self):
        if not (self.paths or self.circles):
            raise ValueError("示意图至少需要一条线或一个圆")
        return self


class TextExercise(Model):
    statement: Statement
    answer: Statement
    solution: Statement
    options: list[Title] = Field(default_factory=list, max_length=8)


class Exercise(TextExercise):
    diagram: Diagram | None = None


class ExerciseSet(Model):
    exercises: list[TextExercise]


class PracticePlan(Model):
    count: int = Field(default=3, ge=1, le=6)
    difficulty: Literal["巩固基础", "与原题相当", "适度提高"] = "与原题相当"
    instructions: str = Field(default="", max_length=1500)
    allow_diagrams: bool = True


class TextOriginal(Model):
    kind: Kind
    topic: Title
    statement: Statement
    answer: str = Field(default="", max_length=4000)
    solution: str = Field(default="", max_length=4000)
    student_work: str = Field(default="", max_length=2500)
    uncertainty: str = Field(default="", max_length=2000)
    number: str = Field(default="", max_length=35)
    grade: str = Field(default="", max_length=30)
    knowledge: str = Field(default="", max_length=120)
    subtype: str = Field(default="", max_length=120)
    difficulty: QuestionDifficulty | None = None
    options: list[Title] = Field(default_factory=list, max_length=8)
    sources: list[str] = Field(min_length=1, max_length=30)


class Original(TextOriginal):
    diagram: Diagram | None = None


class ClassifiedOriginal(TextOriginal):
    difficulty: QuestionDifficulty


class Intake(Model):
    questions: list[ClassifiedOriginal] = Field(max_length=100)


class Review(Model):
    passed: bool
    issues: list[Statement] = Field(max_length=20)

    @property
    def accepted(self) -> bool:
        return self.passed and not self.issues


class TutorReply(Model):
    reply: Statement


