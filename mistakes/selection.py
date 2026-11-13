from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class Target:
    page: int | None
    number: int

    def __str__(self) -> str:
        return f"{self.page}:{self.number}" if self.page is not None else str(self.number)


def read_targets(selection: str, whole_file: bool) -> list[Target]:
    if whole_file:
        if selection.strip():
            raise ValueError("请选择整份收录，或填写指定错题题号。")
        return []
    if not selection.strip():
        raise ValueError("请填写错题题号，或勾选整份收录。")
    targets = []
    normalized = unicodedata.normalize("NFKC", selection).strip().replace("、", ",").replace("；", ",")
    for part in re.split(r"\s*[,;]\s*|\s+", normalized):
        match = re.fullmatch(r"(?:(\d{1,5}):)?(\d{1,5})(?:-(\d{1,5}))?", part)
        if not match:
            raise ValueError("题号示例为 2,5-7。重复题号请加页码，例如 3:9 表示第 3 页第 9 题。")
        page, start, end = match.groups()
        first, last = int(start), int(end or start)
        if not 1 <= first <= last or (page and int(page) < 1) or last - first >= 100:
            raise ValueError("题号和页码应为正数，单次最多指定 100 道题。")
        targets.extend(Target(int(page) if page else None, number) for number in range(first, last + 1))
        if len(targets) > 100:
            raise ValueError("单次最多指定 100 道题。")
    return list(dict.fromkeys(targets))


def printed_number(value: str) -> int | None:
    normalized = unicodedata.normalize("NFKC", value).strip()
    match = re.fullmatch(r"(?:第\s*)?[(\[【]?\s*(\d{1,5})\s*[.)\]】、。]?\s*(?:题)?", normalized)
    return int(match[1]) if match else None
