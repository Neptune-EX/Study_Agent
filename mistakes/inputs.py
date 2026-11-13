from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
import re

from PIL import Image, ImageOps, UnidentifiedImageError
import pymupdf

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_UPLOAD_MB = 32
MAX_PAGES = 12
MAX_IMAGE_EDGE = 2800


def pdf_pages(selection: str, total: int, whole_file: bool = False) -> list[int]:
    if not selection.strip():
        if not whole_file and total > MAX_PAGES:
            raise ValueError(f"PDF 有 {total} 页。请填写页码，每次最多处理 {MAX_PAGES} 页。")
        return list(range(1, total + 1))

    pages = set()
    for part in selection.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if not match:
            raise ValueError("页码请用半角数字、逗号和连字符填写，例如 1,3-5。")
        start = int(match[1])
        end = int(match[2] or match[1])
        if not 1 <= start <= end <= total:
            raise ValueError(f"页码必须在 1 至 {total} 之间，起始页不能大于结束页。")
        if end - start + 1 > MAX_PAGES:
            raise ValueError(f"一次最多处理 {MAX_PAGES} 页。")
        pages.update(range(start, end + 1))
    if len(pages) > MAX_PAGES:
        raise ValueError(f"一次最多处理 {MAX_PAGES} 页。")
    return sorted(pages)


def png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def read_pages(filename: str, data: bytes, selection: str, whole_file: bool = False) -> Iterator[tuple[int, bytes]]:
    if not data or len(data) > MAX_UPLOAD_MB * 1024**2:
        raise ValueError(f"请选择有效文件，文件大小上限为 {MAX_UPLOAD_MB} MB。")
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except (pymupdf.FileDataError, RuntimeError) as exc:
            raise ValueError("无法打开这个 PDF 文件。") from exc
        with document:
            if document.needs_pass:
                raise ValueError("请上传已解密的 PDF。")
            for number in pdf_pages(selection, len(document), whole_file):
                page = document[number - 1]
                scale = min(2.8, MAX_IMAGE_EDGE / max(page.rect.width, page.rect.height))
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB)
                yield number, pixmap.tobytes("png")
        return

    if suffix not in IMAGE_TYPES:
        raise ValueError("请选择 PDF、PNG、JPEG、WebP 或 GIF 文件。")
    try:
        with Image.open(BytesIO(data)) as original:
            if original.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                raise ValueError("文件内容必须是 PNG、JPEG、WebP 或 GIF 图像。")
            if original.width * original.height > 45_000_000:
                raise ValueError("图片像素过多，请缩小后重新上传。")
            image = ImageOps.exif_transpose(original).convert("RGBA")
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            yield 1, png_bytes(background)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取这张图片。") from exc


