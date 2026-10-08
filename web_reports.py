"""Generate printable, standalone HTML versions of school reports."""
from __future__ import annotations

import html
import base64
from pathlib import Path

from assessment import LEVEL_LABELS, overall_level, percentage_or_default
from database import get_connection
from exporter import ACTIVITY_LIST, COMPETENCY_NAMES, SUBJECT_LIST, TRAIT_NAMES, calc_grade, get_real_indicators, get_term_att


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def display_name(student) -> str:
    return f"{student[3] or ''}{student[4] or ''} {student[5] or ''}".strip()


def cell_table(headers, rows, css_class="", score_name_width=30) -> str:
    name_columns = [
        index for index, label in enumerate(headers)
        if "ชื่อ" in str(label) and index < 3
    ]
    if "student-summary" in css_class.split():
        name_style = ' style="white-space:normal;overflow-wrap:anywhere;word-break:normal"'
    else:
        name_style = ' style="white-space:nowrap;overflow-wrap:normal;word-break:keep-all"'
    head = "".join(
        (
            f'<th class="name-column"{name_style}>{esc(label)}</th>'
            if index in name_columns
            else f"<th>{esc(label)}</th>"
        )
        for index, label in enumerate(headers)
    )
    body = "".join(
        "<tr>" + "".join(
            (
                f'<td class="name-column"{name_style}>{esc(value)}</td>'
                if index in name_columns
                else f"<td>{esc(value)}</td>"
            )
            for index, value in enumerate(row)
        ) + "</tr>"
        for row in rows
    )
    table_classes = css_class.split()
    wide_name_table = (
        "small" in table_classes
        and bool(name_columns)
        and not {
            "student-scores", "student-summary", "attendance-report",
            "final-assessment-table",
        }.intersection(table_classes)
    )
    if wide_name_table:
        table_classes.append("wide-name-table")
    cls = f' class="{esc(" ".join(table_classes))}"' if table_classes else ""
    col_group = ""
    if "student-scores" in css_class.split():
        name_width = max(15, min(60, int(score_name_width)))
        other_width = (95 - name_width) / (len(headers) - 2)
        col_widths = ["5%", f"{name_width}%", *([f"{other_width:.2f}%"] * (len(headers) - 2))]
        col_group = "<colgroup>" + "".join(
            f'<col style="width:{width}">' for width in col_widths
        ) + "</colgroup>"
    elif "student-summary" in css_class.split():
        name_width = 23
        other_width = (96 - name_width) / (len(headers) - 2)
        col_widths = ["4%", f"{name_width}%", *([f"{other_width:.2f}%"] * (len(headers) - 2))]
        col_group = "<colgroup>" + "".join(
            f'<col style="width:{width}">' for width in col_widths
        ) + "</colgroup>"
    elif "attendance-report" in css_class.split():
        remaining_width = 66 / (len(headers) - 2)
        col_widths = ["4%", "30%", *([f"{remaining_width:.2f}%"] * (len(headers) - 2))]
        col_group = "<colgroup>" + "".join(
            f'<col style="width:{width}">' for width in col_widths
        ) + "</colgroup>"
    elif "final-assessment-table" in css_class.split():
        remaining_width = 78 / (len(headers) - 2)
        col_widths = ["4%", "18%", *([f"{remaining_width:.2f}%"] * (len(headers) - 2))]
        col_group = "<colgroup>" + "".join(
            f'<col style="width:{width}">' for width in col_widths
        ) + "</colgroup>"
    elif "grade-matrix" in css_class.split():
        indicator_count = max(1, len(headers) - 2)
        indicator_width = 76 / indicator_count
        col_widths = ["5%", "19%", *([f"{indicator_width:.4f}%"] * indicator_count)]
        col_group = "<colgroup>" + "".join(
            f'<col style="width:{width}">' for width in col_widths
        ) + "</colgroup>"
    elif "small" in css_class.split() and name_columns:
        col_widths = [
            "260px" if index in name_columns else "52px"
            for index in range(len(headers))
        ]
        col_group = "<colgroup>" + "".join(
            f'<col style="width:{width}">' for width in col_widths
        ) + "</colgroup>"
    return f"<div class=\"table-wrap\"><table{cls}>{col_group}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def section(
    title, school, grade, year, body, new_page=False, orientation="landscape",
    modifier="",
):
    page_class = f"sheet {orientation}" + (" new-page" if new_page else "")
    if modifier:
        page_class += f" {modifier}"
    return (
        f'<section class="{page_class}"><header><div class="kicker">สมุดบันทึกผลการพัฒนาคุณภาพผู้เรียน</div>'
        f'<h2>{esc(title)}</h2><p>{esc(school)} · {esc(grade)} · ปีการศึกษา {esc(year)}</p></header>{body}</section>'
    )


def signature_block(teacher, academic_head, director):
    signers = [
        ("ครูประจำชั้น", teacher),
        ("หัวหน้าวิชาการ", academic_head),
        ("ผู้อำนวยการ", director),
    ]
    items = "".join(
        '<div class="signature-item"><div class="signature-line"></div>'
        f'<div>({esc(name) if name else "........................................"})</div>'
        f'<div>{esc(role)}</div></div>'
        for role, name in signers
    )
    return f'<div class="signature-block">{items}</div>'


def document_html(title: str, content: str, score_name_width=30, show_score_adjuster=False) -> str:
    font_path = Path("Sarabun-Regular.ttf")
    font_data = base64.b64encode(font_path.read_bytes()).decode("ascii") if font_path.exists() else ""
    score_adjuster = (
        '<span class="score-adjuster">ลากเส้นขอบช่องชื่อในตารางคะแนนเพื่อปรับทุกวิชา '
        f'<output id="score-name-width-value">{max(15, min(60, int(score_name_width)))}%</output></span>'
        if show_score_adjuster else ""
    )
    return f'''<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
@font-face{{font-family:ThaiDoc;src:url(data:font/ttf;base64,__THAI_FONT__) format('truetype');font-weight:400}}
:root{{--ink:#20312d;--green:#176b5b;--line:#c9d6d1;--soft:#edf4f0;--score-name-width:{max(15, min(60, int(score_name_width)))}%;--score-other-width:{(95 - max(15, min(60, int(score_name_width)))) / 10:.2f}%;--summary-name-width:23%;--summary-other-width:{73 / 12:.2f}%}}
*{{box-sizing:border-box}}body{{width:100%;margin:0;background:#eef2f0;color:var(--ink);font-family:ThaiDoc,"Tahoma",sans-serif;font-size:14px;line-height:1.45}}
.toolbar{{position:sticky;top:0;z-index:5;display:flex;gap:12px;align-items:center;justify-content:center;padding:12px;background:#20312d;color:white;box-shadow:0 2px 8px #0002}}
.toolbar button{{border:0;border-radius:8px;padding:10px 18px;font:inherit;font-weight:700;cursor:pointer;background:#21816d;color:#fff}}
.toolbar span{{font-size:13px;color:#d7e5df}}
.score-adjuster{{display:flex;gap:8px;align-items:center;font-size:13px;color:#fff}}.score-adjuster output{{min-width:38px;font-weight:700}}
.sheet{{width:285mm;min-height:198mm;max-width:none;margin:16px auto;padding:26px 18px;background:white;box-shadow:0 3px 18px #1b33231c}}
.sheet.portrait{{width:198mm;min-height:285mm}}
header{{text-align:center;margin-bottom:18px;border-bottom:2px solid var(--green);padding-bottom:12px}}header h2{{margin:2px 0 4px;font-size:22px;color:#173f36}}header p{{margin:0;color:#5d7169}}.kicker{{font-size:12px;color:#668077}}
h3{{margin:18px 0 8px;color:#176b5b;font-size:17px}}.summary{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0}}.summary div{{background:var(--soft);border-radius:8px;padding:10px 14px}}.summary b{{display:block;font-size:18px;color:#176b5b}}
.signature-block{{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;margin:18px 12px 0;text-align:center;break-inside:avoid}}.signature-item{{line-height:1.8}}.signature-line{{height:1px;border-top:1px solid var(--ink);margin:0 8px 8px}}
.table-wrap{{width:100%;max-width:100%;overflow-x:auto;margin:8px 0 16px}}table{{width:100%;max-width:100%;table-layout:fixed;border-collapse:collapse;font-size:12px;break-inside:auto}}thead{{display:table-header-group}}tr{{break-inside:avoid;break-after:auto}}th,td{{border:1px solid var(--line);padding:5px 7px;text-align:center;vertical-align:top;overflow-wrap:anywhere}}th{{background:var(--soft);font-weight:700}}td.left,th.left{{text-align:left}}.small{{font-size:10px}}.grade-matrix{{font-size:11px}}.grade-matrix th:nth-child(1),.grade-matrix td:nth-child(1){{width:48px}}.grade-matrix th:nth-child(2),.grade-matrix td:nth-child(2){{width:190px;text-align:left}}.grade-matrix th:nth-child(n+3),.grade-matrix td:nth-child(n+3){{width:calc((100% - 238px)/10)}}.grade-matrix td:nth-child(2){{overflow-wrap:normal;word-break:normal}}.note{{color:#60746c;font-size:12px}}
.small .name-column,.grade-matrix .name-column{{font-size:13px}}
.wide-name-table{{width:max-content;min-width:100%;max-width:none}}.wide-name-table th,.wide-name-table td{{white-space:nowrap;overflow-wrap:normal;word-break:keep-all}}.wide-name-table .name-column{{position:relative}}
.matrix-print{{display:none}}.matrix-compact{{width:auto;max-width:none;table-layout:fixed;margin:0 auto;font-size:9px}}.matrix-compact th,.matrix-compact td{{padding:3px 2px;overflow-wrap:normal}}.matrix-compact th:nth-child(1),.matrix-compact td:nth-child(1){{width:28px}}.matrix-compact th:nth-child(2),.matrix-compact td:nth-child(2){{width:130px;text-align:left}}.matrix-compact th:nth-child(n+3),.matrix-compact td:nth-child(n+3){{width:25px;min-width:25px;max-width:25px}}
@media print{{@page{{size:A4 landscape;margin:6mm}}body{{background:white;font-size:7.5pt;line-height:1.15}}.toolbar{{display:none}}.sheet{{width:auto;max-width:none;margin:0;padding:0;box-shadow:none;break-after:auto;page-break-after:auto;break-before:auto;page-break-before:auto}}header{{break-after:avoid-page;margin-bottom:6px;padding-bottom:5px}}header h2{{font-size:15pt;margin:1px 0 2px}}header p{{font-size:7pt}}h3{{font-size:9pt;margin:6px 0 3px}}table{{font-size:7pt;line-height:1.1}}th,td{{padding:2px 3px}}.small{{font-size:6pt}}.grade-matrix{{font-size:7pt}}.summary{{gap:5px;margin:7px 0}}.summary div{{border:1px solid var(--line);padding:4px 8px}}.summary b{{font-size:9pt}}.table-wrap{{overflow:visible;margin:3px 0 6px}}.matrix-screen{{display:none!important}}.matrix-print{{display:block!important}}.matrix-compact{{font-size:5.5pt}}.matrix-compact th:nth-child(n+3){{height:88px;writing-mode:vertical-rl;transform:rotate(180deg);white-space:nowrap;vertical-align:bottom}}.matrix-compact td:nth-child(n+3){{height:15px}}}}
.indicator-list th:nth-child(1),.indicator-list td:nth-child(1){{width:6%}}.indicator-list th:nth-child(2),.indicator-list td:nth-child(2){{width:20%}}.indicator-list th:nth-child(3),.indicator-list td:nth-child(3){{width:56%;text-align:left}}.indicator-list th:nth-child(4),.indicator-list td:nth-child(4){{width:18%}}
.individual-indicators th:nth-child(1),.individual-indicators td:nth-child(1){{width:18%}}.individual-indicators th:nth-child(2),.individual-indicators td:nth-child(2){{width:48%;text-align:left}}.individual-indicators th:nth-child(3),.individual-indicators td:nth-child(3){{width:14%}}.individual-indicators th:nth-child(n+4),.individual-indicators td:nth-child(n+4){{width:7%}}
.attendance-report th:first-child,.attendance-report td:first-child{{width:4%}}.attendance-report th:nth-child(2),.attendance-report td:nth-child(2){{width:30%;white-space:nowrap;overflow-wrap:normal;word-break:keep-all}}.attendance-report th:nth-child(n+3),.attendance-report td:nth-child(n+3){{width:calc(66% / 13)}}
.student-scores th:first-child,.student-scores td:first-child{{width:5%}}.student-scores th:nth-child(2),.student-scores td:nth-child(2){{width:var(--score-name-width);text-align:left;white-space:nowrap!important;overflow-wrap:normal!important;word-break:keep-all!important}}.student-scores th:nth-child(n+3),.student-scores td:nth-child(n+3){{width:var(--score-other-width)}}
.student-scores th:nth-child(2),.student-scores td:nth-child(2){{position:relative}}.column-resize-handle{{position:absolute;top:0;right:-5px;width:12px;height:100%;cursor:col-resize;touch-action:none;z-index:3;border-right:2px solid #21816d;opacity:.28}}.column-resize-handle:hover,.column-resize-handle.dragging{{opacity:1;background:#21816d22}}
.student-summary th:nth-child(2),.student-summary td:nth-child(2){{width:var(--summary-name-width);text-align:left;white-space:normal;overflow-wrap:anywhere;word-break:normal;position:relative;padding-right:14px}}.student-summary th:first-child,.student-summary td:first-child{{width:4%}}.student-summary th:nth-child(n+3),.student-summary td:nth-child(n+3){{width:var(--summary-other-width)}}
.student-summary{{min-width:100%}}
@media print{{.student-summary{{min-width:0!important;width:100%}}}}
@media print{{.column-resize-handle{{display:none!important}}}}
@media print{{.sheet.new-page{{break-before:page!important;page-break-before:always!important}}}}
@media print{{@page portrait-sheet{{size:A4 portrait;margin:6mm}}@page landscape-sheet{{size:A4 landscape;margin:6mm}}@page cover-sheet{{size:A4 portrait;margin:0}}.sheet.portrait{{page:portrait-sheet}}.sheet.landscape{{page:landscape-sheet}}}}
@media print{{body{{font-size:10px!important;line-height:1.2!important}}.sheet{{padding:8px 10px!important}}header{{margin-bottom:8px!important;padding-bottom:6px!important}}header h2{{font-size:17px!important;margin:1px 0 3px!important}}header p{{font-size:10px!important}}h3{{font-size:13px!important;margin:8px 0 4px!important}}table{{font-size:9px!important;line-height:1.15!important}}.small{{font-size:7.5px!important}}.small .name-column,.grade-matrix .name-column{{font-size:9px!important}}.grade-matrix{{font-size:8px!important}}th,td{{padding:2px 3px!important}}.table-wrap{{margin:4px 0 8px!important}}.signature-block{{margin-top:10px!important;font-size:10px!important}}}}
@media print{{.small .name-column,.grade-matrix .name-column{{font-size:11px!important}}.attendance-report col:first-child{{width:4%!important}}.attendance-report col:nth-child(2){{width:25%!important}}.attendance-report col:nth-child(n+3){{width:calc(71% / 13)!important}}.student-scores col:first-child{{width:5%!important}}.student-scores col:nth-child(2){{width:25%!important}}.student-scores col:nth-child(n+3){{width:calc(70% / 10)!important}}.student-summary col:first-child{{width:4%!important}}.student-summary col:nth-child(2){{width:20%!important}}.student-summary col:nth-child(n+3){{width:calc(76% / 12)!important}}}}
@media print{{
  @page{{margin:6mm}}
  body{{width:auto!important;margin:0!important;background:#fff!important}}
  .sheet,.sheet.landscape{{width:100%!important;min-height:198mm!important;max-width:none!important;margin:0!important;padding:26px 18px!important;box-sizing:border-box!important;box-shadow:none!important}}
  .sheet.portrait{{width:100%!important;min-height:285mm!important}}
  .sheet .table-wrap{{width:100%!important;max-width:none!important;overflow:visible!important}}
  .sheet .table-wrap>table:not(.matrix-compact){{width:100%!important;max-width:100%!important}}
}}
.cover-page{{page:cover-sheet;width:210mm;min-height:297mm;margin:16px auto;padding:5mm;background:#fff;box-shadow:0 3px 18px #1b33231c;break-after:page;page-break-after:always}}
.cover-frame{{position:relative;display:flex;flex-direction:column;justify-content:space-between;width:100%;height:287mm;border:1.5px solid #176b5b;padding:5mm 5mm 4mm;text-align:center;color:#20312d;box-shadow:inset 0 0 0 1px #e2c77d}}
.cover-frame::before{{position:absolute;top:0;left:0;right:0;height:2mm;background:linear-gradient(90deg,#176b5b 0 78%,#c89b42 78%);content:""}}
.cover-heading{{display:grid;justify-items:center;gap:.5mm;width:100%}}
.school-logo{{display:block;width:22mm;height:22mm;object-fit:contain;margin:0 auto 1mm;padding:1mm;border:1px solid #e2c77d;border-radius:50%;background:#f5f8f6}}
.cover-frame h1{{font-size:20px;line-height:1.3;margin:0;color:#145b4b}}
.cover-frame h2{{font-size:18px;line-height:1.25;margin:0;color:#20312d}}
.cover-frame h3{{font-size:14px;line-height:1.3;margin:0 0 1mm;color:#60746c}}
.cover-results{{display:grid;gap:1mm;width:100%}}
.cover-result-group{{display:grid;gap:.5mm;width:100%}}
.cover-section-title{{background:#edf4f0;border:0;border-left:1.2mm solid #c89b42;border-radius:1mm;margin:0!important;padding:1.5mm 2.5mm;font-size:13px!important;text-align:left;color:#145b4b!important}}
.cover-note{{font-size:10px;margin:0;color:#60746c}}
.cover-meta,.cover-table{{margin:0 auto!important;font-size:11px!important;border:1px solid #c9d6d1;border-radius:1.5mm;overflow:hidden}}
.cover-meta th,.cover-table th{{background:#edf4f0;color:#145b4b}}
.cover-table thead th{{background:#176b5b;color:#fff}}
.cover-meta td,.cover-meta th,.cover-table td,.cover-table th{{border:1px solid #c9d6d1;padding:2px 5px!important;vertical-align:middle}}
.cover-meta th{{text-align:left}}
.cover-table tbody tr:nth-child(even) td{{background:#f7faf8}}
.student-count{{margin-bottom:1mm!important}}
.cover-meta th:first-child,.cover-meta td:first-child{{width:19%;font-weight:700}}
.cover-meta th:nth-child(2),.cover-meta td:nth-child(2){{width:31%;text-align:left}}
.cover-meta th:nth-child(3),.cover-meta td:nth-child(3){{width:19%;font-weight:700}}
.cover-meta th:nth-child(4),.cover-meta td:nth-child(4){{width:31%;text-align:left}}
.cover-table{{table-layout:fixed}}
.cover-subject-table{{font-size:8px!important;line-height:1.1}}
.cover-subject-table th:first-child,.cover-subject-table td:first-child{{width:32%;text-align:left}}
.cover-subject-table td:first-child{{font-size:12px!important}}
.cover-subject-table th:not(:first-child),.cover-subject-table td:not(:first-child){{width:7.55%}}
.cover-student-table th:first-child,.cover-student-table td:first-child{{width:70%}}
.cover-student-table th:last-child,.cover-student-table td:last-child{{width:30%}}
.cover-activity-table th:first-child,.cover-activity-table td:first-child{{width:60%;text-align:left}}
.cover-activity-table th:not(:first-child),.cover-activity-table td:not(:first-child){{width:20%}}
.cover-assessment-table th:first-child,.cover-assessment-table td:first-child{{width:25%;text-align:left}}
.cover-assessment-table th:not(:first-child),.cover-assessment-table td:not(:first-child){{width:18.75%}}
.cover-approval{{display:grid;gap:1mm;justify-items:center;width:100%;padding-top:2mm;border-top:1px solid #d6e0dc}}
.approval{{font-size:12px;margin:0;color:#20312d}}
.cover-approval>.approval:first-child{{margin-bottom:8mm}}
.cover-frame .signature-block{{gap:8px;margin:0;font-size:10px;line-height:1.4;color:#52655d}}
.cover-frame .signature-line{{margin-bottom:4px;border-color:#176b5b}}
.sheet:not(.cover-page){{border:1px solid #d8e3de;border-top:3px solid #176b5b;border-radius:3mm}}
.sheet:not(.cover-page) header{{text-align:left;margin:0 0 5mm;padding:4mm 5mm;background:#f2f7f4;border:0;border-left:1.2mm solid #c89b42;border-radius:1.5mm}}
.sheet:not(.cover-page) .kicker{{display:inline-block;margin-bottom:1mm;padding:.5mm 1.5mm;border-radius:1mm;background:#e4efe9;color:#176b5b;font-size:11px;font-weight:700}}
.sheet:not(.cover-page) header h2{{margin:0 0 1mm;color:#145b4b;font-size:21px;line-height:1.3}}
.sheet:not(.cover-page) header p{{color:#60746c;font-size:12px}}
.sheet:not(.cover-page) h3{{margin:5mm 0 2mm;padding:1.5mm 2.5mm;border-left:1.2mm solid #c89b42;border-radius:1mm;background:#edf4f0;color:#145b4b;font-size:15px}}
.sheet:not(.cover-page) .table-wrap{{margin:2mm 0 4mm;border:1px solid #c9d6d1;border-radius:1.5mm;background:#fff}}
.sheet:not(.cover-page) th{{background:#176b5b;color:#fff;border-color:#176b5b;font-weight:700}}
.sheet:not(.cover-page) td{{border-color:#c9d6d1;vertical-align:middle}}
.sheet:not(.cover-page) tbody tr:nth-child(even) td{{background:#f4f8f6}}
.sheet:not(.cover-page) .note{{margin:2mm 0;padding:2mm 3mm;border-left:1mm solid #c89b42;border-radius:1mm;background:#edf4f0;color:#52655d}}
.sheet.final-assessment .table-wrap{{overflow-x:auto}}
.sheet.final-assessment .final-assessment-table{{width:100%;min-width:0;max-width:100%;table-layout:fixed;font-size:12px}}
.sheet.final-assessment .final-assessment-table th,.sheet.final-assessment .final-assessment-table td{{padding:4px 2px;overflow-wrap:anywhere;word-break:normal;vertical-align:middle}}
.sheet.final-assessment .final-assessment-table .name-column{{text-align:left;white-space:normal;overflow-wrap:anywhere;word-break:normal}}
@media print{{.sheet.final-assessment .table-wrap{{overflow:visible!important}}.sheet.final-assessment .final-assessment-table{{width:100%!important;min-width:0!important;max-width:100%!important;table-layout:fixed!important;font-size:9pt!important;line-height:1.1!important}}.sheet.final-assessment .final-assessment-table th,.sheet.final-assessment .final-assessment-table td{{padding:2px 1px!important;overflow-wrap:anywhere!important;word-break:normal!important}}.sheet.final-assessment .final-assessment-table .name-column{{font-size:9.5pt!important;white-space:normal!important}}.sheet.final-assessment.secondary-assessment .final-assessment-table{{font-size:10pt!important}}.sheet.final-assessment.secondary-assessment .final-assessment-table .name-column{{font-size:10.5pt!important}}}}
@media print{{.sheet:not(.cover-page){{border:1px solid #d8e3de!important;border-top:3px solid #176b5b!important;border-radius:2mm!important;print-color-adjust:exact;-webkit-print-color-adjust:exact}}.sheet:not(.cover-page) header{{margin:0 0 5mm!important;padding:4mm 5mm!important;background:#f2f7f4!important;border:0!important;border-left:1.2mm solid #c89b42!important;border-radius:1.5mm!important;break-after:avoid-page}}.sheet:not(.cover-page) .kicker{{background:#e4efe9!important;color:#176b5b!important}}.sheet:not(.cover-page) header h2{{font-size:21px!important;color:#145b4b!important}}.sheet:not(.cover-page) header p{{font-size:12px!important;color:#60746c!important}}.sheet:not(.cover-page) h3{{margin:5mm 0 2mm!important;padding:1.5mm 2.5mm!important;border-left:1.2mm solid #c89b42!important;background:#edf4f0!important;color:#145b4b!important;font-size:15px!important}}.sheet:not(.cover-page) .table-wrap{{margin:2mm 0 4mm!important;border:1px solid #c9d6d1!important;border-radius:1.5mm!important;print-color-adjust:exact;-webkit-print-color-adjust:exact}}.sheet:not(.cover-page) th{{background:#176b5b!important;color:#fff!important;border-color:#176b5b!important}}.sheet:not(.cover-page) td{{border-color:#c9d6d1!important}}.sheet:not(.cover-page) tbody tr:nth-child(even) td{{background:#f4f8f6!important}}.sheet:not(.cover-page) .note{{margin:2mm 0!important;padding:2mm 3mm!important;border-left:1mm solid #c89b42!important;background:#edf4f0!important;color:#52655d!important}}}}
/* Chrome fits named portrait pages in this mixed-orientation report at about 2/3 scale. */
@media print{{.cover-page{{width:100%!important;height:297mm!important;min-height:297mm!important;margin:0!important;padding:5mm!important;box-shadow:none!important;break-after:page!important;page-break-after:always!important;color:#20312d!important}}.cover-frame{{display:flex!important;flex-direction:column!important;justify-content:space-between!important;width:100%!important;height:287mm!important;min-height:287mm!important;padding:3mm 4mm!important;print-color-adjust:exact;-webkit-print-color-adjust:exact}}.cover-frame h1,.cover-section-title,.cover-meta th{{color:#145b4b!important}}.cover-frame h2,.approval{{color:#20312d!important}}.cover-frame h3,.cover-note,.cover-frame .signature-block{{color:#60746c!important}}.cover-table thead th{{color:#fff!important}}.cover-frame h1{{font-size:22px!important}}.cover-frame h2{{font-size:19px!important}}.cover-frame h3{{font-size:14px!important;margin-bottom:1mm!important}}.cover-section-title{{font-size:14px!important;margin:0!important;padding:1mm 2mm!important}}.cover-results{{gap:.75mm!important}}.cover-result-group{{gap:.5mm!important}}.cover-meta,.cover-table{{font-size:11px!important;margin:0 auto!important}}.cover-meta td,.cover-meta th,.cover-table td,.cover-table th{{padding:2px 5px!important}}.cover-note{{font-size:11px!important}}.approval{{font-size:12px!important;margin:0!important}}.cover-approval{{gap:1mm!important;margin:0!important;padding-top:1.5mm!important}}.cover-approval>.approval:first-child{{margin-bottom:8mm!important}}.cover-frame .signature-block{{font-size:11px!important;margin:0!important;gap:8px!important}}.school-logo{{width:22mm;height:22mm;margin-bottom:1mm}}}}
</style></head><body>
<div class="toolbar"><button type="button" onclick="window.print()">พิมพ์ / บันทึกเป็น PDF</button><span>ลากขอบสีเขียวข้างช่องชื่อเพื่อปรับความกว้าง</span><span class="name-adjuster">ความกว้างช่องชื่อ <output id="wide-name-width-value">260px</output></span>{score_adjuster}</div>
<script>
document.addEventListener('DOMContentLoaded',()=>{{
const scoreTables=document.querySelectorAll('table.student-scores');
const summaryTables=document.querySelectorAll('table.student-summary');
const wideNameTables=document.querySelectorAll('table.wide-name-table');
let scoreNameWidth={max(15, min(60, int(score_name_width)))};
let summaryNameWidth=23;
let wideNameWidth=260;
function applyWideNameWidth(width){{wideNameWidth=Math.max(120,Math.min(500,width));const status=document.getElementById('wide-name-width-value');if(status)status.value=Math.round(wideNameWidth)+'px';wideNameTables.forEach(table=>{{table.querySelectorAll('colgroup col').forEach((col,index)=>{{if(table.rows[0]?.cells[index]?.classList.contains('name-column'))col.style.width=wideNameWidth+'px';}});table.querySelectorAll('tr').forEach(row=>{{Array.from(row.children).forEach(cell=>{{if(cell.classList.contains('name-column'))cell.style.width=wideNameWidth+'px';}});}});}});}}
wideNameTables.forEach(table=>{{const header=table.querySelector('thead .name-column');if(!header)return;const handle=document.createElement('span');handle.className='column-resize-handle';handle.title='ลากเพื่อปรับความกว้างช่องชื่อในตาราง';header.appendChild(handle);handle.addEventListener('pointerdown',event=>{{event.preventDefault();handle.classList.add('dragging');const startX=event.clientX;const startWidth=header.getBoundingClientRect().width;const move=moveEvent=>applyWideNameWidth(startWidth+moveEvent.clientX-startX);const stop=()=>{{handle.classList.remove('dragging');window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',stop);}};window.addEventListener('pointermove',move);window.addEventListener('pointerup',stop,{{once:true}});}});}});
function applySummaryNameWidth(table,width){{summaryNameWidth=Math.max(12,Math.min(45,width));const otherWidth=(96-summaryNameWidth)/(table.rows[0].cells.length-2);document.documentElement.style.setProperty('--summary-name-width',summaryNameWidth+'%');document.documentElement.style.setProperty('--summary-other-width',otherWidth.toFixed(2)+'%');table.querySelectorAll('tr').forEach(row=>{{const cells=row.children;if(cells.length>1){{cells[0].style.width='4%';cells[1].style.width=summaryNameWidth+'%';for(let i=2;i<cells.length;i++)cells[i].style.width=otherWidth.toFixed(2)+'%';}}}});table.querySelectorAll('colgroup col').forEach((col,index)=>{{if(index===0)col.style.width='4%';else if(index===1)col.style.width=summaryNameWidth+'%';else col.style.width=otherWidth.toFixed(2)+'%';}});}}
function applyScoreNameWidth(width){{scoreNameWidth=Math.max(15,Math.min(60,width));const otherWidth=(95-scoreNameWidth)/10;document.documentElement.style.setProperty('--score-name-width',scoreNameWidth+'%');document.documentElement.style.setProperty('--score-other-width',otherWidth.toFixed(2)+'%');const status=document.getElementById('score-name-width-value');if(status)status.value=scoreNameWidth+'%';scoreTables.forEach(table=>{{table.querySelectorAll('tr').forEach(row=>{{const cells=row.children;if(cells.length>1){{cells[0].style.width='5%';cells[1].style.width=scoreNameWidth+'%';for(let i=2;i<cells.length;i++)cells[i].style.width=otherWidth.toFixed(2)+'%';}}}});table.querySelectorAll('colgroup col').forEach((col,index)=>{{if(index===0)col.style.width='5%';else if(index===1)col.style.width=scoreNameWidth+'%';else col.style.width=otherWidth.toFixed(2)+'%';}});}});}}
scoreTables.forEach(table=>{{table.querySelectorAll('tr').forEach(row=>{{const nameCell=row.children[1];if(!nameCell)return;const handle=document.createElement('span');handle.className='column-resize-handle';handle.title='ลากเพื่อปรับความกว้างช่องชื่อทุกวิชา';nameCell.appendChild(handle);handle.addEventListener('pointerdown',event=>{{event.preventDefault();handle.classList.add('dragging');const startX=event.clientX;const startWidth=scoreNameWidth;const tableWidth=table.getBoundingClientRect().width;const move=moveEvent=>applyScoreNameWidth(startWidth+(moveEvent.clientX-startX)/tableWidth*100);const stop=()=>{{handle.classList.remove('dragging');window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',stop);}};window.addEventListener('pointermove',move);window.addEventListener('pointerup',stop,{{once:true}});}});}});}});
summaryTables.forEach(table=>{{table.querySelectorAll('tr').forEach(row=>{{const nameCell=row.children[1];if(!nameCell)return;const handle=document.createElement('span');handle.className='column-resize-handle';handle.title='ลากเพื่อปรับความกว้างช่องชื่อในหน้าสรุป';nameCell.appendChild(handle);handle.addEventListener('pointerdown',event=>{{event.preventDefault();handle.classList.add('dragging');const startX=event.clientX;const startWidth=summaryNameWidth;const tableWidth=table.getBoundingClientRect().width;const move=moveEvent=>applySummaryNameWidth(table,startWidth+(moveEvent.clientX-startX)/tableWidth*100);const stop=()=>{{handle.classList.remove('dragging');window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',stop);}};window.addEventListener('pointermove',move);window.addEventListener('pointerup',stop,{{once:true}});}});}});}});
}});
</script>
{content}
</body></html>'''.replace("__THAI_FONT__", font_data)


def _school_context(conn):
    cfg = dict(conn.execute("SELECT key, val FROM config").fetchall())
    grade = cfg.get("grade", "")
    grade_code = grade
    prefix = "ชั้นประถมศึกษาปีที่ "
    if grade.startswith(prefix):
        grade_code = f"ป.{grade[len(prefix):].strip()}"
    profile_ids = dict(conn.execute(
        "SELECT student_id, citizen_id FROM student_profiles WHERE grade=?",
        (grade_code,),
    ).fetchall())
    students = conn.execute(
        "SELECT seat_no, citizen_id, student_id, title, first_name, last_name "
        "FROM students ORDER BY seat_no ASC"
    ).fetchall()
    # Older roster imports left citizen_id empty. Fill it from the full student
    # profile by student ID so the printed register shows the card number.
    students = [
        (seat_no, citizen_id or profile_ids.get(str(student_id), ""), student_id, title, first_name, last_name)
        for seat_no, citizen_id, student_id, title, first_name, last_name in students
    ]
    return (
        cfg.get("school_name", "โรงเรียน"),
        cfg.get("address", ""),
        grade,
        cfg.get("year", ""),
        cfg.get("teacher_1", ""),
        cfg.get("advisor", cfg.get("teacher_1", "")),
        cfg.get("academic_head", ""),
        cfg.get("director", ""),
        students,
    )


def _score(conn, seat_no, subject_key, term):
    return conn.execute(
        "SELECT formative, exam FROM subject_scores WHERE seat_no=? AND subject_key=? AND term=?",
        (seat_no, subject_key, term),
    ).fetchone() or (None, None)


def _annual_score(conn, seat_no, subject_key, term1_weight):
    term1 = _score(conn, seat_no, subject_key, 1)
    term2 = _score(conn, seat_no, subject_key, 2)
    if not all(value is not None for value in (*term1, *term2)):
        return None
    term1_total, term2_total = sum(term1), sum(term2)
    return round((term1_total * term1_weight + term2_total * (100 - term1_weight)) / 100, 1)


def build_class_report(score_name_width=30) -> str:
    conn = get_connection()
    try:
        school, address, grade, year, teacher, advisor, academic_head, director, students = _school_context(conn)
        indicator_levels = {}
        for seat_no, subject_key, indicator_code, level, checked in conn.execute(
            "SELECT seat_no, subject_key, indicator_code, level, checked FROM indicator_assessments"
        ):
            indicator_levels[(seat_no, subject_key, indicator_code)] = (
                level if level is not None else (3 if checked else 0)
            )
        saved_term_weight = conn.execute("SELECT val FROM config WHERE key='grade_term1_weight'").fetchone()
        term1_weight = percentage_or_default(saved_term_weight[0]) if saved_term_weight else 50
        grade_names = ["4", "3.5", "3", "2.5", "2", "1.5", "1", "0", "ร", "มส"]
        overall_grade_counts = dict.fromkeys(grade_names, 0)
        for student in students:
            subject_averages = []
            for subject_key, _, _ in SUBJECT_LIST:
                term_1 = _score(conn, student[0], subject_key, 1)
                term_2 = _score(conn, student[0], subject_key, 2)
                annual_score = _annual_score(conn, student[0], subject_key, term1_weight)
                if annual_score is not None:
                    subject_averages.append(annual_score)
            if len(subject_averages) == len(SUBJECT_LIST):
                overall_average = round(sum(subject_averages) / len(subject_averages), 1)
                overall_grade_counts[calc_grade(overall_average)] += 1

        pages = [""]

        roster = [
            [s[0], s[1] or "", s[2] or "", s[3] or "", s[4] or "", s[5] or ""]
            for s in students
        ]
        pages.append(section(
            "ทะเบียนประวัติและข้อมูลนักเรียน", school, grade, year,
            cell_table(["เลขที่", "เลขประจำตัวประชาชน", "รหัสนักเรียน", "คำนำหน้า", "ชื่อ", "นามสกุล"], roster),
            new_page=True, orientation="portrait",
        ))

        attendance_rows = []
        attendance_cursor = conn.cursor()
        for student in students:
            t1 = get_term_att(attendance_cursor, student[0], 1)
            t2 = get_term_att(attendance_cursor, student[0], 2)
            total_open, present = t1[0] + t2[0], t1[1] + t2[1]
            pct = round(present / total_open * 100, 1) if total_open else None
            absent_leave = t1[2] + t1[3] + t1[4] + t2[2] + t2[3] + t2[4]
            attendance_rows.append([
                student[0], display_name(student), t1[0], t1[1], t1[2] + t1[3], t1[4],
                t2[0], t2[1], t2[2] + t2[3], t2[4], total_open, present,
                absent_leave, f"{pct}%" if pct is not None else "—",
                ("ผ่านเกณฑ์" if pct >= 80 else "ไม่ผ่าน") if pct is not None else "ยังไม่บันทึก",
            ])
        pages.append(section(
            "บันทึกเวลาเรียนและสรุปวันมาเรียน", school, grade, year,
            '<p class="note">เกณฑ์เวลาเรียนไม่น้อยกว่าร้อยละ 80 · อัตรานี้คำนวณจากวันที่มีบันทึกสถานะ กรุณาตรวจสอบข้อมูลให้ครบตามวันเรียนของสถานศึกษาก่อนสรุปผล</p>' + cell_table(
                ["เลขที่", "ชื่อ-นามสกุล", "ภาค 1 เปิด", "มา", "ลา/ป่วย", "ขาด", "ภาค 2 เปิด", "มา", "ลา/ป่วย", "ขาด", "เปิดรวม", "มาเรียน", "ลา/ขาด", "ร้อยละ", "ผล"],
                attendance_rows, "small attendance-report",
            ), new_page=True,
        ))

        for subject_key, subject_name, subject_type in SUBJECT_LIST:
            indicators = get_real_indicators(subject_key, grade)
            indicator_rows = [[i, code, desc, kind] for i, (code, desc, kind) in enumerate(indicators, 1)]
            pages.append(section(
                f"มาตรฐานและตัวชี้วัด · {subject_name}", school, grade, year,
                f'<p>รายวิชา {esc(subject_name)} · {esc(subject_type)} · ตัวชี้วัด {len(indicators)} ข้อ</p>'
                + cell_table(["ลำดับ", "รหัสตัวชี้วัด", "รายละเอียด", "ประเภท"], indicator_rows, "indicator-list"),
                new_page=True, orientation="portrait",
            ))

            codes = [item[0] for item in indicators]
            # Limit each matrix to ten indicator columns so student names and
            # indicator labels remain legible both on screen and on paper.
            for start in range(0, len(codes), 10):
                code_group = codes[start:start + 10]
                matrix_rows = [
                    [s[0], display_name(s), *[
                        indicator_levels.get((s[0], subject_key, code), "")
                        for code in code_group
                    ]]
                    for s in students
                ]
                pages.append(section(
                    f"ตารางสรุปผลตัวชี้วัดทั้งห้อง · {subject_name}", school, grade, year,
                    f'<h3>ตัวชี้วัด {start + 1}–{start + len(code_group)} · คะแนน 0–3</h3>'
                    + cell_table(["เลขที่", "ชื่อนักเรียน", *code_group], matrix_rows, "grade-matrix"),
                    new_page=True,
                ))

            score_rows = []
            for student in students:
                t1, t2 = _score(conn, student[0], subject_key, 1), _score(conn, student[0], subject_key, 2)
                complete = all(value is not None for value in (*t1, *t2))
                if complete:
                    net = _annual_score(conn, student[0], subject_key, term1_weight)
                    grade_value = calc_grade(net)
                    total_1, total_2 = sum(t1), sum(t2)
                    outcome = "ผ่านเกณฑ์" if float(grade_value) >= 1 else "ไม่ผ่าน"
                else:
                    net = grade_value = outcome = "รอประเมิน"
                    total_1 = sum(t1) if all(value is not None for value in t1) else "—"
                    total_2 = sum(t2) if all(value is not None for value in t2) else "—"
                score_rows.append([
                    student[0], display_name(student), *[v if v is not None else "—" for v in t1], total_1,
                    *[v if v is not None else "—" for v in t2], total_2,
                    (total_1 + total_2) if isinstance(total_1, (int, float)) and isinstance(total_2, (int, float)) else "—",
                    net, grade_value, outcome,
                ])
            pages.append(section(
                f"แบบบันทึกคะแนน · {subject_name}", school, grade, year,
                cell_table(
                    ["เลขที่", "ชื่อ-นามสกุล", "ภาค 1 เก็บ", "ภาค 1 สอบ", "รวม", "ภาค 2 เก็บ", "ภาค 2 สอบ", "รวม", "รวม 200", "สุทธิ 100", "เกรด", "ผล"],
                    score_rows, "small student-scores", score_name_width,
                ), new_page=True,
            ))

        all_subject_rows = []
        for student in students:
            results = []
            for key, _, _ in SUBJECT_LIST:
                t1, t2 = _score(conn, student[0], key, 1), _score(conn, student[0], key, 2)
                annual_score = _annual_score(conn, student[0], key, term1_weight)
                results.append(calc_grade(annual_score) if annual_score is not None else "รอประเมิน")
            decision = "ตรวจเกณฑ์เลื่อนชั้น" if all(value != "รอประเมิน" for value in results) else "ข้อมูลไม่ครบ"
            all_subject_rows.append([student[0], display_name(student), *results, decision])
        pages.append(section(
            "สรุปผลการเรียนทุกกลุ่มสาระ", school, grade, year,
            cell_table(["เลขที่", "ชื่อ-นามสกุล", *[x[1] for x in SUBJECT_LIST], "ผลการตัดสิน"], all_subject_rows, "small student-summary"),
            new_page=True,
        ))

        final_rows = []
        trait_names = TRAIT_NAMES
        activity_summary = {key: {"ผ": 0, "มผ": 0, "ยังไม่บันทึก": 0} for key, _ in ACTIVITY_LIST}
        trait_summary = {name: {"ไม่ผ่าน": 0, "ผ่าน": 0, "ดี": 0, "ดีเยี่ยม": 0, "ยังไม่บันทึก": 0} for name in ["คุณลักษณะอันพึงประสงค์", "การอ่าน คิดวิเคราะห์ และเขียน"]}
        competency_summary = {name: {"ไม่ผ่าน": 0, "ผ่าน": 0, "ดี": 0, "ดีเยี่ยม": 0, "ยังไม่บันทึก": 0} for name in COMPETENCY_NAMES}
        for student in students:
            activity_results = {
                (activity_key, term): result
                for activity_key, term, result in conn.execute(
                    "SELECT activity_key, term, result FROM activities WHERE seat_no=? AND term IN (1, 2)",
                    (student[0],),
                ).fetchall()
            }
            acts = []
            for activity_key, _ in ACTIVITY_LIST:
                term_results = [
                    activity_results.get((activity_key, term))
                    for term in (1, 2)
                ]
                if all(result in ("ผ", "มผ") for result in term_results):
                    result = "มผ" if "มผ" in term_results else "ผ"
                else:
                    result = "ยังไม่บันทึก"
                acts.append(result)
                activity_summary[activity_key][result] += 1
            trait_row = conn.execute(
                "SELECT l1,l2,l3,l4,l5,l6,l7,l8 FROM desired_traits WHERE seat_no=? AND term=1",
                (student[0],),
            ).fetchone()
            reading_row = conn.execute(
                "SELECT a1,a2,a3 FROM reading_analysis WHERE seat_no=? AND term=1",
                (student[0],),
            ).fetchone()
            competency_row = conn.execute(
                "SELECT c1,c2,c3,c4,c5 FROM competency_assessments WHERE seat_no=? AND term=2",
                (student[0],),
            ).fetchone() or ("ยังไม่บันทึก",) * len(COMPETENCY_NAMES)
            for competency_name, competency_result in zip(COMPETENCY_NAMES, competency_row):
                competency_summary[competency_name][competency_result if competency_result in ("ไม่ผ่าน", "ผ่าน", "ดี", "ดีเยี่ยม") else "ยังไม่บันทึก"] += 1
            competency_levels = [{"ดีเยี่ยม": 3, "ดี": 2, "ผ่าน": 1, "ไม่ผ่าน": 0}.get(value) for value in competency_row]
            competency_overall = LEVEL_LABELS.get(overall_level(competency_levels), "ยังไม่ประเมิน")
            traits = trait_row or (None,) * 8
            reading = reading_row or (None,) * 3
            trait_result = LEVEL_LABELS.get(overall_level(traits), "ยังไม่ประเมิน")
            reading_result = LEVEL_LABELS.get(overall_level(reading), "ยังไม่ประเมิน")
            trait_summary["คุณลักษณะอันพึงประสงค์"]["ยังไม่บันทึก" if trait_result == "ยังไม่ประเมิน" else trait_result] += 1
            trait_summary["การอ่าน คิดวิเคราะห์ และเขียน"]["ยังไม่บันทึก" if reading_result == "ยังไม่ประเมิน" else reading_result] += 1
            activity_result = "มผ" if "มผ" in acts else "ผ" if all(value == "ผ" for value in acts) else "ยังไม่บันทึก"
            final_rows.append([student[0], display_name(student), *acts, activity_result,
                               *[LEVEL_LABELS.get(value, "ยังไม่ประเมิน") for value in traits], trait_result,
                               *[LEVEL_LABELS.get(value, "ยังไม่ประเมิน") for value in reading], reading_result,
                               *competency_row, competency_overall])
        development_rows = [
            [name, values["ผ"], values["มผ"]]
            for key, name in ACTIVITY_LIST for values in [activity_summary[key]]
        ]
        assessment_rows = [
            [name, values["ไม่ผ่าน"], values["ผ่าน"], values["ดี"], values["ดีเยี่ยม"]]
            for name, values in trait_summary.items()
        ]
        assessment_rows.extend(
            [name, values["ไม่ผ่าน"], values["ผ่าน"], values["ดี"], values["ดีเยี่ยม"]]
            for name, values in competency_summary.items()
        )
        competency_overall_counts = dict.fromkeys(("ไม่ผ่าน", "ผ่าน", "ดี", "ดีเยี่ยม", "ยังไม่ประเมิน"), 0)
        for row in final_rows:
            competency_overall_counts[row[-1]] += 1
        assessment_rows.append(["สมรรถนะสำคัญ (ภาพรวม)", competency_overall_counts["ไม่ผ่าน"],
                                competency_overall_counts["ผ่าน"], competency_overall_counts["ดี"],
                                competency_overall_counts["ดีเยี่ยม"]])
        subject_grade_levels = ("4", "3.5", "3", "2.5", "2", "1.5", "1", "0")
        subject_grade_rows = []
        for subject_index, (_, subject_name, _) in enumerate(SUBJECT_LIST):
            grade_counts = dict.fromkeys(subject_grade_levels, 0)
            for student_row in all_subject_rows:
                grade_value = str(student_row[2 + subject_index])
                if grade_value in grade_counts:
                    grade_counts[grade_value] += 1
            subject_grade_rows.append([
                subject_name, *[grade_counts[level] for level in subject_grade_levels], len(students)
            ])
        logo_path = Path(__file__).parent / "assets" / "school_logo.jpg"
        logo = f'<img class="school-logo" src="data:image/jpeg;base64,{base64.b64encode(logo_path.read_bytes()).decode("ascii")}" alt="ตราสัญลักษณ์สถานศึกษา">' if logo_path.exists() else ""
        metadata_rows = [
            ["ชั้น", grade, "ภาคเรียนที่", "1 และ 2"], ["ปีการศึกษา", year, "รายวิชา", "สรุปรวมทุกวิชา"],
            ["ครูผู้สอน/ครูประจำชั้น", teacher or "ยังไม่ได้ระบุ", "ครูที่ปรึกษา", advisor or "ยังไม่ได้ระบุ"],
        ]
        metadata_table = '<div class="table-wrap"><table class="cover-meta"><tbody>' + "".join(
            "<tr>" + "".join(
                f'<th>{esc(value)}</th>' if index in (0, 2) else f'<td>{esc(value)}</td>'
                for index, value in enumerate(row)
            ) + "</tr>" for row in metadata_rows
        ) + "</tbody></table></div>"
        student_count_table = (
            '<table class="cover-table cover-student-table student-count"><thead><tr><th>ข้อมูลนักเรียน</th><th>รวม (คน)</th></tr></thead>'
            f'<tbody><tr><td>จำนวนนักเรียนทั้งหมด</td><td>{len(students)}</td></tr></tbody></table>'
        )
        cover = (
            f'<section class="cover-page"><div class="cover-frame"><div class="cover-heading">{logo}'
            '<h1>สมุดบันทึกการพัฒนาคุณภาพผู้เรียน (ปพ.5)</h1>'
            f'<h2>{esc(school)}</h2><h3>{esc(address or "")}</h3></div>'
            + metadata_table
            + '<div class="cover-results">'
            + '<div class="cover-result-group"><h3 class="cover-section-title">ผลการเรียนรายวิชา · จำนวนผู้เรียนแยกตามระดับผลการเรียน</h3>'
            + cell_table(["รายวิชา", *subject_grade_levels, "รวม"], subject_grade_rows, "cover-table cover-subject-table") + '</div>'
            + student_count_table
            + '<div class="cover-result-group"><h3 class="cover-section-title">สรุปกิจกรรมพัฒนาผู้เรียน · ตลอดปีการศึกษา</h3>'
            + cell_table(["รายการกิจกรรม", "ผ่าน (คน)", "ไม่ผ่าน (คน)"], development_rows, "cover-table cover-activity-table") + '</div>'
            + '<div class="cover-result-group"><h3 class="cover-section-title">สรุปผลการประเมิน</h3>'
            + cell_table(["ด้านที่ประเมิน", "ไม่ผ่าน", "ผ่าน", "ดี", "ดีเยี่ยม"], assessment_rows, "cover-table cover-assessment-table") + '</div></div>'
            + '<div class="cover-approval"><div class="approval">□ อนุมัติ　 □ ไม่อนุมัติ　 วันที่ ........../........../..........</div>'
            + signature_block(teacher, academic_head, director) + '</div>'
            + '</div></section>'
        )
        pages[0] = cover
        final_headers = [
            "เลขที่", "ชื่อ-นามสกุล", *[x[1] for x in ACTIVITY_LIST], "กิจกรรม",
            *trait_names, "คุณลักษณะ", "การอ่าน", "คิดวิเคราะห์", "เขียน",
            "อ่านคิดฯ", *COMPETENCY_NAMES, "สมรรถนะรวม",
        ]
        first_group_end = 2 + len(ACTIVITY_LIST) + 1 + len(trait_names) + 1
        pages.append(section(
            "กิจกรรมพัฒนาผู้เรียนและคุณลักษณะ", school, grade, year,
            cell_table(
                final_headers[:first_group_end],
                [row[:first_group_end] for row in final_rows],
                "small final-assessment-table",
            ), new_page=True, modifier="final-assessment",
        ))
        pages.append(section(
            "การอ่าน คิดวิเคราะห์ และสมรรถนะสำคัญ", school, grade, year,
            cell_table(
                final_headers[:2] + final_headers[first_group_end:],
                [row[:2] + row[first_group_end:] for row in final_rows],
                "small final-assessment-table",
            ), new_page=True, modifier="final-assessment secondary-assessment",
        ))
        return document_html("รายงาน ปพ.5 · เอกสารเว็บ", "".join(pages), score_name_width, show_score_adjuster=True)
    finally:
        conn.close()


def build_individual_report(seat_no: int) -> str:
    conn = get_connection()
    try:
        school, address, grade, year, teacher, advisor, academic_head, director, students = _school_context(conn)
        saved_term_weight = conn.execute("SELECT val FROM config WHERE key='grade_term1_weight'").fetchone()
        term1_weight = percentage_or_default(saved_term_weight[0]) if saved_term_weight else 50
        student = next((s for s in students if s[0] == seat_no), None)
        if student is None:
            raise ValueError("ไม่พบรายชื่อนักเรียน")
        name = display_name(student)
        intro = (
            f'<div class="summary"><div>ชื่อ-นามสกุล<b>{esc(name)}</b></div>'
            f'<div>เลขที่<b>{student[0]}</b></div><div>รหัสนักเรียน<b>{esc(student[2] or "-")}</b></div></div>'
        )
        pages = [section("รายงานผลการประเมินตัวชี้วัดรายบุคคล (ปพ.6)", school, grade, year, intro)]
        grade_rows = []
        subject_data = []
        for subject_key, subject_name, subject_type in SUBJECT_LIST:
            t1, t2 = _score(conn, seat_no, subject_key, 1), _score(conn, seat_no, subject_key, 2)
            complete = all(value is not None for value in (*t1, *t2))
            if complete:
                net = _annual_score(conn, seat_no, subject_key, term1_weight)
                grade_value = calc_grade(net)
                passed = "ผ่าน" if float(grade_value) >= 1 else "ไม่ผ่าน"
            else:
                net = grade_value = passed = "รอประเมิน"
            grade_rows.append([subject_name, subject_type,
                               *[value if value is not None else "—" for value in t1],
                               *[value if value is not None else "—" for value in t2],
                               net, grade_value, passed])
            indicators = get_real_indicators(subject_key, grade)
            indicator_scores = {
                row[0]: (row[1] if row[1] is not None else (3 if row[2] else 0))
                for row in conn.execute(
                    "SELECT indicator_code, level, checked FROM indicator_assessments WHERE seat_no=? AND subject_key=?",
                    (seat_no, subject_key),
                )
            }
            indicator_rows = [
                [code, desc, kind, "", "", indicator_scores.get(code, "ยังไม่บันทึก")]
                for code, desc, kind in indicators
            ]
            subject_data.append((subject_name, subject_type, grade_value, passed, indicator_rows))
        pages[0] = section(
            "รายงานผลการประเมินตัวชี้วัดรายบุคคล (ปพ.6)", school, grade, year,
            intro + '<h3>สรุปผลการเรียน</h3>' + cell_table(
                ["รายวิชา", "ประเภท", "ภาค 1 เก็บ", "ภาค 1 สอบ", "ภาค 2 เก็บ", "ภาค 2 สอบ", "คะแนนสุทธิ", "เกรด", "ผล"], grade_rows, "small",
            ) + signature_block(teacher, academic_head, director),
            orientation="portrait",
        )
        for subject_name, subject_type, grade_value, passed, indicator_rows in subject_data:
            pages.append(section(
                f"ตัวชี้วัดรายวิชา · {subject_name}", school, grade, year,
                f'<p>รายวิชา {esc(subject_name)} · {esc(subject_type)} · ผลการเรียน {esc(grade_value)} ({esc(passed)})</p>'
                + cell_table(["รหัสตัวชี้วัด", "รายละเอียด", "ประเภท", "คะแนนเต็ม", "คะแนนที่ได้", "คะแนน (0–3)"], indicator_rows, "individual-indicators"),
                new_page=True, orientation="portrait",
            ))
        return document_html(f"ปพ.6 · {name}", "".join(pages))
    finally:
        conn.close()
