"""统一文档解析：文本 / Markdown / PDF / Word / Excel / CSV / 图片。

所有解析器都是惰性 import，缺少可选依赖时给出可读的降级提示而不是 500。
多模态输入（表格、图片）统一转换为「文本 + 页码定位」结构，供知识库与工作流复用。
"""
import csv
import io
import os

from config import Config
from text_utils import clip, normalize

SUPPORTED_TEXT = {'.txt', '.md', '.markdown'}
SUPPORTED_DOC = {'.pdf', '.docx'}
SUPPORTED_TABLE = {'.xlsx', '.xlsm', '.csv', '.tsv'}
SUPPORTED_IMAGE = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}
SUPPORTED_ALL = SUPPORTED_TEXT | SUPPORTED_DOC | SUPPORTED_TABLE | SUPPORTED_IMAGE
LEGACY_OFFICE = {'.doc', '.ppt'}


class ParsedDocument:
    def __init__(self, text='', pages=None, kind='text', meta=None, warnings=None):
        self.text = normalize(text)
        self.pages = pages or []
        self.kind = kind
        self.meta = meta or {}
        self.warnings = warnings or []

    def to_dict(self):
        return {
            'text': self.text,
            'pages': self.pages,
            'kind': self.kind,
            'meta': self.meta,
            'warnings': self.warnings,
            'chars': len(self.text),
        }


def _read_text_file(path):
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            with open(path, 'r', encoding=encoding) as handle:
                return handle.read()
        except (UnicodeDecodeError, LookupError):
            continue
    with open(path, 'rb') as handle:
        return handle.read().decode('utf-8', errors='replace')


def _parse_pdf(path, limit):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError('服务器缺少 PDF 解析依赖，请执行 pip install pypdf')
    reader = PdfReader(path)
    pages, collected = [], []
    total = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ''
        except Exception:
            page_text = ''
        if not page_text.strip():
            continue
        pages.append({'page': page_number, 'text': page_text})
        collected.append(f'【第{page_number}页】\n{page_text}')
        total += len(page_text)
        if total >= limit:
            break
    return '\n\n'.join(collected), pages


def _parse_docx(path, limit):
    try:
        from docx import Document
    except ImportError:
        raise ValueError('服务器缺少 Word 解析依赖，请执行 pip install python-docx')
    document = Document(path)
    blocks, total = [], 0
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        blocks.append(text)
        total += len(text)
        if total >= limit:
            break
    for table_index, table in enumerate(document.tables, start=1):
        if total >= limit:
            break
        blocks.append(f'\n表 {table_index}：')
        for row in table.rows[:30]:
            cells = [cell.text.strip() for cell in row.cells]
            blocks.append('| ' + ' | '.join(cells) + ' |')
            total += sum(len(cell) for cell in cells)
    return '\n'.join(blocks), []


def _parse_xlsx(path, limit, max_rows=200, max_cols=30):
    try:
        import openpyxl
    except ImportError:
        raise ValueError('服务器缺少 Excel 解析依赖，请执行 pip install openpyxl')
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    blocks, total = [], 0
    for sheet in workbook.worksheets:
        blocks.append(f'## 工作表：{sheet.title}')
        for row_index, row in enumerate(sheet.iter_rows(max_col=max_cols, values_only=True), start=1):
            if row_index > max_rows:
                blocks.append(f'（已截断，仅显示前 {max_rows} 行）')
                break
            cells = ['' if cell is None else str(cell) for cell in row]
            if not any(cell.strip() for cell in cells):
                continue
            blocks.append('| ' + ' | '.join(cells) + ' |')
            total += sum(len(cell) for cell in cells)
            if total >= limit:
                break
        if total >= limit:
            break
    workbook.close()
    return '\n'.join(blocks), []


def _parse_csv(path, limit, max_rows=200):
    with open(path, 'r', encoding='utf-8-sig', errors='replace', newline='') as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(handle, dialect)
        blocks, total = [], 0
        for row_index, row in enumerate(reader, start=1):
            if row_index > max_rows:
                blocks.append(f'（已截断，仅显示前 {max_rows} 行）')
                break
            blocks.append('| ' + ' | '.join(cell.strip() for cell in row) + ' |')
            total += sum(len(cell) for cell in row)
            if total >= limit:
                break
    return '\n'.join(blocks), []


def parse_file(path, limit=None):
    """解析任意受支持文件，返回 ParsedDocument；不支持的类型抛 ValueError。"""
    if not path or not os.path.isfile(path):
        raise ValueError('文件不存在')
    limit = limit or Config.MAX_TEXT_CHARS
    extension = os.path.splitext(path)[1].lower()
    filename = os.path.basename(path)

    if extension in SUPPORTED_TEXT:
        text = _read_text_file(path)
        return ParsedDocument(text[:limit], kind='text', meta={'filename': filename})
    if extension == '.pdf':
        text, pages = _parse_pdf(path, limit)
        return ParsedDocument(text[:limit], pages=pages, kind='pdf', meta={'filename': filename, 'pages': len(pages)})
    if extension == '.docx':
        text, _ = _parse_docx(path, limit)
        return ParsedDocument(text[:limit], kind='docx', meta={'filename': filename})
    if extension in ('.xlsx', '.xlsm'):
        text, _ = _parse_xlsx(path, limit)
        return ParsedDocument(text[:limit], kind='table', meta={'filename': filename})
    if extension in ('.csv', '.tsv'):
        text, _ = _parse_csv(path, limit)
        return ParsedDocument(text[:limit], kind='table', meta={'filename': filename})
    if extension in SUPPORTED_IMAGE:
        # 图片走多模态通道：有视觉模型（本机 Ollama / OpenAI 兼容接口）时识别成文字，
        # 识别结果会像普通文档一样进入知识库与问答，可被引用；没有模型时只登记元数据。
        meta = {'filename': filename, 'size': os.path.getsize(path)}
        try:
            from PIL import Image
            with Image.open(path) as image:
                meta.update({'width': image.width, 'height': image.height, 'mode': image.mode})
        except Exception:
            pass

        description, info = '', {}
        try:
            from vision_service import get_vision
            vision = get_vision()
            if vision.available:
                description, info = vision.describe(path)
        except Exception as error:  # 视觉链路异常不应该影响上传
            info = {'error': f'{type(error).__name__}: {error}'}

        if description:
            meta['vision'] = info
            meta['recognized'] = True
            return ParsedDocument(f'【图片内容识别 · {info.get("model", "VLM")}】\n\n{description}',
                                  kind='image', meta=meta)

        meta['recognized'] = False
        if info.get('error'):
            reason = f'视觉模型识别失败：{info["error"]}'
        else:
            reason = '当前未配置视觉模型（VLM），仅登记元数据、不做像素级识别'
        return ParsedDocument('', kind='image', meta=meta,
                              warnings=[f'图片已上传。{reason}。'
                                        '配置 VISION_PROVIDER=ollama 并准备视觉模型后，图片内容即可参与问答。'])
    if extension in LEGACY_OFFICE:
        raise ValueError(f'暂不支持读取旧版 {extension} 文件，请另存为 .docx / .pptx 后上传')
    raise ValueError(f'不支持的文件类型：{extension}')


def table_to_markdown(rows, max_cols=12, max_rows=60):
    if not rows:
        return ''
    header = rows[0]
    lines = ['| ' + ' | '.join(str(cell)[:40] for cell in header[:max_cols]) + ' |']
    lines.append('| ' + ' | '.join(['---'] * min(len(header), max_cols)) + ' |')
    for row in rows[1:max_rows]:
        lines.append('| ' + ' | '.join(str(cell)[:40] for cell in row[:max_cols]) + ' |')
    return '\n'.join(lines)


def parse_tabular(path, max_rows=200):
    """把表格解析成 (header, rows)，供科研数据分析工作流做统计。

    返回 dict：{'header': [...], 'rows': [[...]], 'sheet': str|None}
    """
    extension = os.path.splitext(path)[1].lower()
    if extension in ('.xlsx', '.xlsm'):
        import openpyxl
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
        sheet = workbook.worksheets[0]
        rows = []
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if index >= max_rows:
                break
            rows.append(['' if cell is None else cell for cell in row])
        sheet_name = sheet.title
        workbook.close()
        return {'header': rows[0] if rows else [], 'rows': rows[1:], 'sheet': sheet_name}
    if extension in ('.csv', '.tsv'):
        with open(path, 'r', encoding='utf-8-sig', errors='replace', newline='') as handle:
            delimiter = '\t' if extension == '.tsv' else ','
            reader = csv.reader(handle, delimiter=delimiter)
            rows = [row for _, row in zip(range(max_rows), reader)]
        return {'header': rows[0] if rows else [], 'rows': rows[1:], 'sheet': None}
    raise ValueError('仅支持 xlsx / csv / tsv 表格数据分析')


def numeric_summary(values):
    """纯标准库描述统计。"""
    cleaned = []
    for value in values:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            continue
        if number == number and abs(number) != float('inf'):
            cleaned.append(number)
    if not cleaned:
        return {'count': 0}
    cleaned.sort()
    count = len(cleaned)
    mean = sum(cleaned) / count
    variance = sum((item - mean) ** 2 for item in cleaned) / count
    mid = count // 2
    median = cleaned[mid] if count % 2 else (cleaned[mid - 1] + cleaned[mid]) / 2
    return {
        'count': count,
        'mean': round(mean, 4),
        'std': round(variance ** 0.5, 4),
        'min': round(cleaned[0], 4),
        'max': round(cleaned[-1], 4),
        'median': round(median, 4),
        'p25': round(cleaned[max(0, int(count * 0.25) - 1)], 4),
        'p75': round(cleaned[min(count - 1, int(count * 0.75))], 4),
    }