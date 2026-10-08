"""Additional school reports for the new PDF print center."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from assessment import percentage_or_default
from database import get_connection
from exporter import SUBJECT_LIST, calc_grade


ROOT = Path(__file__).resolve().parent
FONT_PATH = ROOT / "Sarabun-Regular.ttf"
FONT = "AdditionalReportSarabun"
if FONT_PATH.is_file():
    pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT, italic=FONT, boldItalic=FONT)
else:
    FONT = "Helvetica"

GREEN = colors.HexColor("#145b4b")
PALE_GREEN = colors.HexColor("#edf4f0")
GRID = colors.HexColor("#8a9892")
TEXT = colors.HexColor("#20312d")


def _paragraph(value, *, size=9, align=TA_LEFT, color=TEXT, leading=None):
    safe = str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    style = ParagraphStyle(
        f"p-{size}-{align}", fontName=FONT, fontSize=size,
        leading=leading or size + 2, alignment=align, textColor=color,
        wordWrap="CJK", splitLongWords=1,
    )
    return Paragraph(safe, style)


def _base_table(rows, widths, repeat_rows=1, font_size=8):
    table = Table(rows, colWidths=widths, repeatRows=repeat_rows, hAlign="CENTER")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.45, GRID),
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("LEADING", (0, 0), (-1, -1), font_size + 1),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("BACKGROUND", (0, 0), (-1, repeat_rows - 1), PALE_GREEN),
        ("TEXTCOLOR", (0, 0), (-1, repeat_rows - 1), GREEN),
    ]
    table.setStyle(TableStyle(commands))
    return table


def _document(title: str, school: str, grade: str, year: str, term_label: str):
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=12 * mm,
        title=title,
    )
    heading = _paragraph(title, size=17, align=TA_CENTER, color=GREEN, leading=20)
    subheading = _paragraph(
        f"{school}　ระดับชั้น {grade}　ปีการศึกษา {year}　{term_label}",
        size=10, align=TA_CENTER,
    )
    return buffer, document, [heading, subheading, Spacer(1, 4 * mm)]


def _signatures(story, teacher: str, director: str):
    story.extend([
        Spacer(1, 7 * mm),
        _paragraph("ลงชื่อ ........................................................ ครูผู้บันทึก　　 ลงชื่อ ........................................................ ผู้อนุมัติ", size=9, align=TA_CENTER),
        _paragraph(f"({teacher or '................................................'})　　　　　　　　　({director or '................................................'})", size=9, align=TA_CENTER),
    ])


def _school_data():
    conn = get_connection()
    config = dict(conn.execute("SELECT key, val FROM config").fetchall())
    students = conn.execute(
        "SELECT seat_no, student_id, title, first_name, last_name FROM students ORDER BY seat_no"
    ).fetchall()
    return conn, config, students


def build_attendance_summary_pdf(period: str) -> bytes:
    if period not in {"1", "2", "ทั้งปี"}:
        raise ValueError("เลือกภาคเรียนที่ถูกต้อง")
    conn, cfg, students = _school_data()
    try:
        school = cfg.get("school_name", "")
        grade = cfg.get("grade", "")
        year = cfg.get("year", "")
        teacher = cfg.get("teacher_1", "")
        director = cfg.get("director", "")
        terms = (1, 2) if period == "ทั้งปี" else (int(period),)
        term_label = "สรุปตลอดปีการศึกษา" if period == "ทั้งปี" else f"สรุปภาคเรียนที่ {period}"
        buffer, document, story = _document("สรุปเวลาเรียนประจำชั้น", school, grade, year, term_label)

        def stats(seat_no, term):
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM attendance_daily WHERE seat_no=? AND term=? AND status<>'' GROUP BY status",
                (seat_no, term),
            ).fetchall()
            values = dict(rows)
            present = values.get("ม", 0)
            sick = values.get("ป", 0)
            leave = values.get("ล", 0)
            absent = values.get("ข", 0)
            opened = present + sick + leave + absent
            return opened, present, sick, leave, absent

        data = []
        if period == "ทั้งปี":
            data.append([
                "เลขที่", "ชื่อ-นามสกุล", "ภาคเรียนที่ 1", "", "", "", "",
                "ภาคเรียนที่ 2", "", "", "", "",
                "รวมทั้งปี", "", "", "", "", "",
            ])
            data.append([
                "", "", "วันเปิด", "มา", "ป่วย", "ลา", "ขาด",
                "วันเปิด", "มา", "ป่วย", "ลา", "ขาด",
                "วันเปิด", "มา", "ป่วย/ลา", "ขาด", "ร้อยละ", "ผล",
            ])
            for student in students:
                t1 = stats(student[0], 1)
                t2 = stats(student[0], 2)
                opened = t1[0] + t2[0]
                present = t1[1] + t2[1]
                sick_leave = t1[2] + t1[3] + t2[2] + t2[3]
                absent = t1[4] + t2[4]
                pct = present / opened * 100 if opened else None
                result = "ผ่านเกณฑ์" if pct is not None and pct >= 80 else "ไม่ผ่าน" if pct is not None else "ยังไม่บันทึก"
                name = f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip()
                data.append([
                    str(student[0]), _paragraph(name, size=8), *map(str, t1), *map(str, t2),
                    str(opened), str(present), str(sick_leave), str(absent),
                    f"{pct:.1f}%" if pct is not None else "—", result,
                ])
            widths = [10 * mm, 47 * mm] + [12 * mm] * 14 + [14 * mm, 22 * mm]
            table = _base_table(data, widths, repeat_rows=2, font_size=7.2)
            for start, end in ((2, 6), (7, 11), (12, 17)):
                table.setStyle(TableStyle([("SPAN", (start, 0), (end, 0))]))
            for col in (0, 1):
                table.setStyle(TableStyle([("SPAN", (col, 0), (col, 1))]))
            table.setStyle(TableStyle([("ALIGN", (1, 2), (1, -1), "LEFT")]))
        else:
            data.append(["เลขที่", "ชื่อ-นามสกุล", "วันเปิด", "มา", "ป่วย", "ลา", "ขาด", "ร้อยละ", "ผลเวลาเรียน (80%)"])
            for student in students:
                opened, present, sick, leave, absent = stats(student[0], terms[0])
                pct = present / opened * 100 if opened else None
                result = "ผ่านเกณฑ์" if pct is not None and pct >= 80 else "ไม่ผ่าน" if pct is not None else "ยังไม่บันทึก"
                name = f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip()
                data.append([
                    str(student[0]), _paragraph(name, size=9), str(opened), str(present),
                    str(sick), str(leave), str(absent), f"{pct:.1f}%" if pct is not None else "—", result,
                ])
            widths = [16 * mm, 78 * mm, 22 * mm, 22 * mm, 20 * mm, 20 * mm, 20 * mm, 24 * mm, 35 * mm]
            table = _base_table(data, widths, font_size=8.5)
            table.setStyle(TableStyle([("ALIGN", (1, 1), (1, -1), "LEFT")]))

        story.extend([
            table,
            Spacer(1, 2 * mm),
            _paragraph("หมายเหตุ: คำนวณร้อยละจากวันที่มีการบันทึกสถานะ โดยนับเฉพาะสถานะ มา ป่วย ลา และขาด", size=8),
        ])
        _signatures(story, teacher, director)
        document.build(story)
        return buffer.getvalue()
    finally:
        conn.close()


def build_class_score_pdf(subject_key: str, period: str) -> bytes:
    subjects = {key: (name, kind) for key, name, kind in SUBJECT_LIST}
    if subject_key not in subjects:
        raise ValueError("เลือกรายวิชาที่ถูกต้อง")
    if period not in {"1", "2", "ทั้งปี"}:
        raise ValueError("เลือกภาคเรียนที่ถูกต้อง")

    conn, cfg, students = _school_data()
    try:
        school = cfg.get("school_name", "")
        grade = cfg.get("grade", "")
        year = cfg.get("year", "")
        teacher = cfg.get("teacher_1", "")
        director = cfg.get("director", "")
        subject_name, subject_type = subjects[subject_key]
        saved_weight = conn.execute("SELECT val FROM config WHERE key='grade_term1_weight'").fetchone()
        term1_weight = percentage_or_default(saved_weight[0]) if saved_weight else 50
        term_label = "ตลอดปีการศึกษา" if period == "ทั้งปี" else f"ภาคเรียนที่ {period}"
        title = f"แบบบันทึกคะแนนประจำชั้น · {subject_name}"
        buffer, document, story = _document(title, school, grade, year, term_label)
        story.insert(1, _paragraph(f"รายวิชา {subject_name} ({subject_type})　รหัส {subject_key}", size=11, align=TA_CENTER))
        story.insert(2, Spacer(1, 2 * mm))

        if period == "ทั้งปี":
            rows = [[
                "เลขที่", "ชื่อ-นามสกุล", "ภาคเรียนที่ 1", "", "", "ภาคเรียนที่ 2", "", "",
                "ตลอดปี", "", "",
            ], [
                "", "", "คะแนนเก็บ", "คะแนนสอบ", "รวม", "คะแนนเก็บ", "คะแนนสอบ", "รวม",
                "คะแนนเฉลี่ย", "ระดับผล", "สถานะ",
            ]]
            for student in students:
                terms = []
                for term in (1, 2):
                    score = conn.execute(
                        "SELECT formative, exam FROM subject_scores WHERE seat_no=? AND subject_key=? AND term=?",
                        (student[0], subject_key, term),
                    ).fetchone()
                    terms.append(score)
                complete = all(score is not None and all(value is not None for value in score) for score in terms)
                total1 = sum(terms[0]) if terms[0] and all(value is not None for value in terms[0]) else None
                total2 = sum(terms[1]) if terms[1] and all(value is not None for value in terms[1]) else None
                annual = round((total1 * term1_weight + total2 * (100 - term1_weight)) / 100, 1) if complete else None
                result = calc_grade(annual) if annual is not None else "รอประเมิน"
                name = f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip()
                show = lambda value: "—" if value is None else f"{value:g}"
                rows.append([
                    str(student[0]), _paragraph(name, size=8.5),
                    show(terms[0][0] if terms[0] else None), show(terms[0][1] if terms[0] else None), show(total1),
                    show(terms[1][0] if terms[1] else None), show(terms[1][1] if terms[1] else None), show(total2),
                    show(annual), result, "ครบ" if complete else "รอข้อมูล",
                ])
            widths = [12 * mm, 48 * mm] + [23 * mm] * 9
            table = _base_table(rows, widths, repeat_rows=2, font_size=8)
            for start, end in ((2, 4), (5, 7), (8, 10)):
                table.setStyle(TableStyle([("SPAN", (start, 0), (end, 0))]))
            for col in (0, 1):
                table.setStyle(TableStyle([("SPAN", (col, 0), (col, 1))]))
            table.setStyle(TableStyle([("ALIGN", (1, 2), (1, -1), "LEFT")]))
            story.append(_paragraph(f"น้ำหนักตลอดปี: ภาคเรียนที่ 1 {term1_weight:g}% · ภาคเรียนที่ 2 {100 - term1_weight:g}%", size=9))
        else:
            rows = [["เลขที่", "ชื่อ-นามสกุล", "คะแนนเก็บ", "คะแนนสอบ", "รวมคะแนน", "ระดับผลการเรียน", "สถานะข้อมูล"]]
            term = int(period)
            for student in students:
                score = conn.execute(
                    "SELECT formative, exam FROM subject_scores WHERE seat_no=? AND subject_key=? AND term=?",
                    (student[0], subject_key, term),
                ).fetchone()
                complete = score is not None and all(value is not None for value in score)
                total_score = sum(score) if complete else None
                result = calc_grade(total_score) if complete else "รอประเมิน"
                name = f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip()
                show = lambda value: "—" if value is None else f"{value:g}"
                rows.append([
                    str(student[0]), _paragraph(name, size=9),
                    show(score[0] if score else None), show(score[1] if score else None),
                    show(total_score), result, "ครบ" if complete else "รอข้อมูล",
                ])
            widths = [15 * mm, 78 * mm, 32 * mm, 32 * mm, 32 * mm, 35 * mm, 28 * mm]
            table = _base_table(rows, widths, font_size=8.5)
            table.setStyle(TableStyle([("ALIGN", (1, 1), (1, -1), "LEFT")]))

        story.extend([table, Spacer(1, 2 * mm), _paragraph("ผลการเรียนคำนวณจากคะแนนที่บันทึกในระบบตามเกณฑ์ระดับผลการเรียนของระบบ", size=8)])
        _signatures(story, teacher, director)
        document.build(story)
        return buffer.getvalue()
    finally:
        conn.close()
