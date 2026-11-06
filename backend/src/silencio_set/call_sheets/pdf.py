"""Printable version-bound call sheets, rendered from the same frozen web DTO."""
from functools import cache
from io import BytesIO
from pathlib import Path
import subprocess
import sys
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from silencio_set.call_sheets.domain import parsed_time
from silencio_set.call_sheets.schemas import ExportSnapshot
from silencio_set.accounts.errors import AuthError

FONT = 'SilencioSansSC'
FONT_PATH = Path(__file__).resolve().parents[1] / 'assets' / 'fonts' / 'SilencioSansSC-Regular.ttf'
RED = colors.HexColor('#f22c20')
INK = colors.HexColor('#111111')
MUTED = colors.HexColor('#555555')
LINE = colors.HexColor('#d1ceca')
SHANGHAI = ZoneInfo('Asia/Shanghai')


class PdfTextUnsupported(Exception):
    pass


@cache
def register_font():
    font = TTFont(FONT, str(FONT_PATH))
    pdfmetrics.registerFont(font)
    return frozenset(font.face.charToGlyph)


def status_label(version):
    if version.status != 'published':
        return {'draft':'草稿 - 未发布', 'pending_review':'待审核 - 未发布', 'approved':'已审核 - 未发布'}[version.status], False
    if version.validity_status == 'withdrawn': return '已撤回 - 不作为拍摄安排', False
    if version.validity_status == 'superseded' or version.call_sheet.current_published_version_id != version.id:
        return '已被新版替代 - 历史记录', False
    if version.validity_status == 'review_required' or not version.source_current:
        return '来源待复核 - 请核对最新安排', False
    return '当前有效 - 已发布', True


def display(value):
    if value is None: return '未填写'
    stamp = parsed_time(value) if isinstance(value, str) else value
    return stamp.astimezone(SHANGHAI).strftime('%Y-%m-%d %H:%M:%S')


def render_call_sheet_pdf(snapshot):
    version = snapshot.version
    source = version.arrangement_snapshot
    label, effective = status_label(version)
    supported = register_font()
    # Fail explicitly rather than silently rendering unsupported user text as boxes.
    strings = []
    def collect(value):
        if isinstance(value, str): strings.append(value)
        elif isinstance(value, dict):
            for item in value.values(): collect(item)
        elif isinstance(value, list):
            for item in value: collect(item)
    collect(version.model_dump(mode='json'))
    if any(ord(char) not in supported for text in strings for char in text if not char.isspace()):
        raise PdfTextUnsupported()
    body_style = ParagraphStyle('body', fontName=FONT, fontSize=9, leading=14, wordWrap='CJK', textColor=INK,
        spaceAfter=0, alignment=TA_LEFT, splitLongWords=True)
    small_style = ParagraphStyle('small', parent=body_style, fontSize=8, leading=12, textColor=MUTED)
    section_style = ParagraphStyle('section', parent=body_style, fontSize=12, leading=18, spaceBefore=12, spaceAfter=6, keepWithNext=True)
    title_style = ParagraphStyle('title', parent=body_style, fontSize=20, leading=27, spaceAfter=8)
    date_style = ParagraphStyle('date', parent=body_style, fontSize=28, leading=35)
    def paragraph(text, style=body_style):
        return Paragraph(escape(str(text)).replace('\t','    ').replace('\n','<br/>'), style)
    def heading(text): return paragraph(text, section_style)
    def lines(*items): return '<br/>'.join(escape(str(item)).replace('\t','    ').replace('\n','<br/>') for item in items)
    width, height = A4
    left = right = 18*mm
    available = width-left-right
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=left, rightMargin=right,
        topMargin=27*mm, bottomMargin=22*mm, title=f'Silencio Set - {source["shooting_date"]} - v{version.version_no}',
        author='Silencio Set', pageCompression=1)

    def page_frame(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(RED); canvas.rect(left,height-17*mm,8*mm,8*mm,fill=1,stroke=0)
        canvas.setFillColor(INK); canvas.setFont('Helvetica-Bold',10)
        canvas.drawCentredString(left+4*mm,height-14.4*mm,'SS')
        canvas.setFont('Helvetica',8.5); canvas.drawString(left+11*mm,height-14*mm,'SILENCIO SET / DAILY CALL SHEET')
        canvas.setFont(FONT,8); canvas.drawRightString(width-right,height-14*mm,label)
        canvas.setStrokeColor(INK); canvas.setLineWidth(.7); canvas.line(left,height-20*mm,width-right,height-20*mm)
        canvas.setStrokeColor(LINE); canvas.line(left,17*mm,width-right,17*mm)
        canvas.setFillColor(MUTED); canvas.setFont(FONT,7)
        canvas.drawString(left,12.5*mm,f'通告 v{version.version_no} - {str(version.id)[:8]} | 导出 {display(snapshot.exported_at)}（北京时间）')
        canvas.drawRightString(width-right,12.5*mm,f'第 {doc.page} 页')
        canvas.drawString(left,8*mm,'离线文件不会自动更新，请核对平台当前安排。')
        canvas.restoreState()

    day = source['shooting_date']
    date_tile = [paragraph('拍摄日'), Spacer(1,7*mm), paragraph(day[5:].replace('-','.'),date_style),
        paragraph(day[:4]), Spacer(1,4*mm), paragraph('北京时间')]
    overview = [paragraph(source['project_name'],title_style),paragraph('每日拍摄通告'),Spacer(1,3*mm),
        paragraph(f'通告第 {version.version_no} 版 | {source["plan_name"]}第 {source["plan_version_no"]} 版'),
        Spacer(1,3*mm), paragraph(f'集合时间：{display(version.gathering_at)}'),
        paragraph(f'集合地点：{version.gathering_location or "未填写"}')]
    header = Table([[date_tile,overview]],colWidths=[available*.29,available*.71],hAlign='LEFT')
    header.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),RED),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(0,0),12),('RIGHTPADDING',(0,0),(0,0),12),('TOPPADDING',(0,0),(-1,-1),14),
        ('BOTTOMPADDING',(0,0),(-1,-1),14),('LEFTPADDING',(1,0),(1,0),18),('RIGHTPADDING',(1,0),(1,0),0)]))
    status_text = label + ('。状态以导出时刻为准。' if effective else '。仅供筹备核对或历史查阅，不作为当前有效拍摄安排。')
    if not version.source_current: status_text += ' 来源排期已更新或待复核。'
    banner = Table([[paragraph(status_text)]],colWidths=[available],hAlign='LEFT')
    banner.setStyle(TableStyle([('BOX',(0,0),(-1,-1),1,INK if effective else RED),('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#f6f4f0')),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9)]))
    schedule_heading = heading('拍摄安排 / 北京时间')
    # LongTable splits itself; keeping its full height with a heading would
    # push an oversized first row onto an otherwise empty following page.
    schedule_heading.keepWithNext = 0
    story = [header,Spacer(1,5*mm),banner,schedule_heading]
    rows = [[paragraph('起止时间'),paragraph('场次 / 场地'),paragraph('人员 / 资源')]]
    for item in source['sessions']:
        scene = [item['scene_label'],item.get('location_name') or '未填写场地']
        scene.append(f'转场预留：{item["transition_minutes"]} 分钟')
        if item.get('note'): scene.append(item['note'])
        people = '、'.join(person['display_name'] for person in item['members']) or '未列出人员'
        resources = [f'{resource["name"]} {resource["quantity"]}{resource["unit"]}' for resource in item['resources']]
        rows.append([Paragraph(lines(display(item['starts_at']),'- '+display(item['ends_at'])),body_style),
            Paragraph(lines(*scene),body_style),Paragraph(lines(people,*resources),body_style)])
    schedule = LongTable(rows,colWidths=[available*.30,available*.35,available*.35],repeatRows=1,splitByRow=1,splitInRow=1,hAlign='LEFT')
    schedule.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#efede8')),
        ('LINEBELOW',(0,0),(-1,0),.8,INK),('LINEBELOW',(0,1),(-1,-1),.4,LINE),
        ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
    story.append(schedule)
    if source['overnight_continuations']:
        story.append(heading('前一日延续拍摄'))
        for item in source['overnight_continuations']:
            story.append(paragraph(f'{item["scene_label"]}：{display(item["starts_at"])} - {display(item["ends_at"])}；请核对休息与集合安排。'))
    story.extend([heading('联络与注意事项'),paragraph('联络说明：'+(version.contact_note or '未填写')),Spacer(1,3*mm),paragraph('注意事项：'+(version.notes or '未填写'))])
    story.extend([heading(f'接收范围 / {len(version.recipients)} 人'),paragraph('、'.join(person.display_name+('（必选）' if person.required else '') for person in version.recipients) or '尚未选择')])
    story.append(KeepTogether([heading('来源与版本'),paragraph(f'通告版本：{version.id}；内容修订号：{version.lock_version}',small_style),
        paragraph(f'来源排期版本：{version.source_plan_version_id}',small_style),
        paragraph('发布时间：'+(display(version.published_at) if version.published_at else '未发布'),small_style)]))
    document.build(story,onFirstPage=page_frame,onLaterPages=page_frame)
    return output.getvalue()


def render_in_process(snapshot):
    try:
        completed = subprocess.run([sys.executable, '-m', 'silencio_set.call_sheets.pdf'],
            input=snapshot.model_dump_json().encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
    except subprocess.TimeoutExpired:
        raise AuthError(503, 'PDF_EXPORT_FAILED', 'PDF生成超时，请保留网页版并稍后重试') from None
    if completed.returncode == 2: raise AuthError(422, 'PDF_TEXT_UNSUPPORTED', '部分字符无法排版，请使用网页版查看')
    if completed.returncode != 0: raise AuthError(503, 'PDF_EXPORT_FAILED', 'PDF生成失败，请保留网页版并稍后重试')
    return completed.stdout


if __name__ == '__main__':
    try:
        result = render_call_sheet_pdf(ExportSnapshot.model_validate_json(sys.stdin.buffer.read()))
    except PdfTextUnsupported:
        sys.exit(2)
    except Exception:
        sys.exit(1)
    sys.stdout.buffer.write(result)
