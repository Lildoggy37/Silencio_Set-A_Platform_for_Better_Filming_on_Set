"""Bounded document extraction, also executable as an isolated child process."""
from io import BytesIO
import json
import subprocess
import sys
from zipfile import ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.schemas import clean_text
from silencio_set.scripts.schemas import ExtractedDocument, ExtractedParagraph

MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 100_000
MAX_PAGES = 100


class ExtractionFailure(Exception):
    pass


def docx_text(container):
    for block in container.iter_inner_content():
        if isinstance(block, Paragraph):
            yield block.text
        elif isinstance(block, Table):
            seen = set()
            for row in block.rows:
                for cell in row.cells:
                    # Merged cells can occur multiple times in the same row.
                    if cell._tc in seen: continue
                    seen.add(cell._tc)
                    yield from docx_text(cell)


def extract_document(data: bytes, file_format: str):
    if not data: raise ExtractionFailure('DOCUMENT_EMPTY')
    if len(data) > MAX_BYTES: raise ExtractionFailure('DOCUMENT_TOO_LARGE')
    result = ExtractedDocument(paragraphs=[], page_count=None, notes=[])
    count = 0
    def append(text, page=None):
        nonlocal count
        for line in text.splitlines():
            if not line.strip(): continue
            if len(line.strip()) > MAX_TEXT: raise ExtractionFailure('DOCUMENT_TEXT_TOO_LARGE')
            text_line = clean_text(line, MAX_TEXT, multiline=True)
            count += len(text_line) + (1 if result.paragraphs else 0)
            if count > MAX_TEXT: raise ExtractionFailure('DOCUMENT_TEXT_TOO_LARGE')
            result.paragraphs.append(ExtractedParagraph(text=text_line, source_page=page))
    if file_format == 'pdf':
        if not data.startswith(b'%PDF-'): raise ExtractionFailure('DOCUMENT_FORMAT_MISMATCH')
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted: raise ExtractionFailure('DOCUMENT_ENCRYPTED')
        result.page_count = len(reader.pages)
        if result.page_count > MAX_PAGES: raise ExtractionFailure('DOCUMENT_PAGE_LIMIT')
        blank_pages = []
        for page_no, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            if not text.strip(): blank_pages.append(page_no)
            append(text, page_no)
        result.notes.append('PDF文字顺序和换行可能与版面不同，请对照原文件核对。')
        if blank_pages: result.notes.append('这些页面未提取到文字，可能为空白或图片页：' + '、'.join(map(str, blank_pages)))
    elif file_format == 'docx':
        if not data.startswith(b'PK\x03\x04'): raise ExtractionFailure('DOCUMENT_FORMAT_MISMATCH')
        with ZipFile(BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024:
                raise ExtractionFailure('DOCUMENT_EXPANSION_LIMIT')
            if any(name.lower().endswith('vbaproject.bin') for name in archive.namelist()):
                raise ExtractionFailure('DOCUMENT_FORMAT_MISMATCH')
        for text in docx_text(Document(BytesIO(data))): append(text)
        result.notes.append('已按正文与表格顺序提取；图片、文本框、页眉页脚和修订内容需另行核对。DOCX不标注来源页码。')
    elif file_format == 'txt':
        try: text = data.decode('utf-8-sig')
        except UnicodeDecodeError: raise ExtractionFailure('DOCUMENT_ENCODING') from None
        append(text)
    else: raise ExtractionFailure('DOCUMENT_TYPE_UNSUPPORTED')
    if not result.paragraphs: raise ExtractionFailure('DOCUMENT_NO_TEXT')
    return result


def parse_in_process(data, file_format):
    try:
        completed = subprocess.run([sys.executable, '-m', 'silencio_set.scripts.extractor', file_format], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        raise AuthError(422, 'DOCUMENT_PARSE_TIMEOUT', '解析超过时限，请简化文件或粘贴正文') from None
    if completed.returncode != 0:
        raise AuthError(422, 'DOCUMENT_INVALID', '文件无法解析，请检查文件或粘贴正文')
    payload = json.loads(completed.stdout)
    if 'error' in payload:
        code = payload['error']
        status = 413 if code in {'DOCUMENT_TOO_LARGE','DOCUMENT_TEXT_TOO_LARGE','DOCUMENT_PAGE_LIMIT','DOCUMENT_EXPANSION_LIMIT'} else 415 if code == 'DOCUMENT_TYPE_UNSUPPORTED' else 422
        raise AuthError(status, code, '无法导入该文档，请检查格式或粘贴正文')
    return ExtractedDocument.model_validate(payload)


if __name__ == '__main__':
    try:
        result = extract_document(sys.stdin.buffer.read(MAX_BYTES + 1), sys.argv[1]).model_dump(mode='json')
    except ExtractionFailure as failure:
        result = {'error': str(failure)}
    except Exception:
        # Uploaded documents must not put parser paths or document text in errors.
        result = {'error':'DOCUMENT_INVALID'}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
