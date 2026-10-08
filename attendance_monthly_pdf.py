"""Generate a monthly class attendance grid as an A4 landscape PDF."""

from __future__ import annotations

import calendar
from datetime import date
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from database import get_connection


ROOT = Path(__file__).resolve().parent
FONT_PATH = ROOT / "Sarabun-Regular.ttf"
FONT = "MonthlyAttendanceSarabun"
if FONT_PATH.is_file():
    pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT, italic=FONT, boldItalic=FONT)
else:
    FONT = "Helvetica"

GRID = colors.HexColor("#5f6b66")
PALE = colors.HexColor("#edf4f0")
WEEKEND = colors.HexColor("#f1eafa")
HOLIDAY = colors.HexColor("#fce8e8")
OFFTERM = colors.HexColor("#fff7d6")
GREEN = colors.HexColor("#145b4b")
TEXT = colors.HexColor("#202a26")
MONTHS = {
    1: "มกราคม", 2: "กุมภาพันธ์", 3: "มีนาคม", 4: "เมษายน",
    5: "พฤษภาคม", 6: "มิถุนายน", 7: "กรกฎาคม", 8: "สิงหาคม",
    9: "กันยายน", 10: "ตุลาคม", 11: "พฤศจิกายน", 12: "ธันวาคม",
}
HOLIDAYS_2569 = {
    (2026, 5, 4): "วันฉัตรมงคล",
    (2026, 5, 13): "วันพืชมงคล",
    (2026, 6, 1): "ชดเชยวันวิสาขบูชา",
    (2026, 6, 3): "วันเฉลิมพระชนมพรรษาสมเด็จพระราชินี",
    (2026, 7, 28): "วันเฉลิมพระชนมพรรษาพระบาทสมเด็จพระเจ้าอยู่หัว",
    (2026, 7, 29): "วันอาสาฬหบูชา",
    (2026, 8, 12): "วันแม่แห่งชาติ",
    (2026, 10, 13): "วันนวมินทรมหาราช",
    (2026, 10, 23): "วันปิยมหาราช",
    (2026, 12, 7): "ชดเชยวันพ่อแห่งชาติ",
    (2026, 12, 10): "วันรัฐธรรมนูญ",
    (2026, 12, 31): "วันสิ้นปี",
    (2027, 1, 1): "วันขึ้นปีใหม่",
    (2027, 2, 22): "ชดเชยวันมาฆบูชา",
}


def _p(value, size=8, align=TA_LEFT, color=TEXT, leading=None):
    safe = str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    style = ParagraphStyle(
        f"monthly-{size}-{align}", fontName=FONT, fontSize=size,
        leading=leading or size + 2, alignment=align, textColor=color,
        wordWrap="CJK", splitLongWords=1,
    )
    return Paragraph(safe, style)


def _term_months(term: int) -> tuple[int, ...]:
    return (5, 6, 7, 8, 9, 10) if term == 1 else (11, 12, 1, 2, 3)


def build_monthly_attendance_pdf(term: int, month: int) -> bytes:
    if term not in (1, 2) or month not in _term_months(term):
        raise ValueError("เลือกเดือนให้ตรงกับภาคเรียน")

    conn = get_connection()
    try:
        config = dict(conn.execute("SELECT key, val FROM config").fetchall())
        students = conn.execute(
            "SELECT seat_no, student_id, title, first_name, last_name FROM students ORDER BY seat_no"
        ).fetchall()
        saved = {
            (seat_no, day): status or ""
            for seat_no, day, status in conn.execute(
                "SELECT seat_no, day, status FROM attendance_daily WHERE term=? AND month_no=?",
                (term, month),
            ).fetchall()
        }
    finally:
        conn.close()

    academic_year = config.get("year", "2569")
    try:
        academic_year_int = int(academic_year)
    except (TypeError, ValueError):
        academic_year_int = 2569
    buddhist_year = academic_year_int + (1 if month < 4 else 0)
    gregorian_year = buddhist_year - 543
    days_in_month = calendar.monthrange(gregorian_year, month)[1]
    holiday_map = {
        day: label for (year, holiday_month, day), label in HOLIDAYS_2569.items()
        if year == gregorian_year and holiday_month == month
    } if academic_year_int == 2569 else {}

    school, address, grade, teacher = (
        config.get("school_name", ""),
        config.get("address", ""),
        config.get("grade", ""),
        config.get("teacher_1", ""),
    )
    room = config.get("room", "1")
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=8 * mm, rightMargin=8 * mm,
        topMargin=7 * mm, bottomMargin=8 * mm,
        title=f"บันทึกเวลาเรียน {MONTHS[month]} {buddhist_year}",
    )
    story = []
    logo_path = ROOT / "assets" / "school_logo.jpg"
    title_line = f"แบบบันทึกเวลาเรียน ประจำเดือน {MONTHS[month]} พ.ศ. {buddhist_year}"
    info_line = f"ระดับชั้น: {grade} ห้อง {room}　|　ปีการศึกษา {academic_year}　ภาคเรียนที่ {term}"
    teacher_line = f"ครูประจำชั้น/ผู้บันทึก: {teacher or '................................'}"
    if logo_path.is_file():
        logo = Image(str(logo_path), width=16 * mm, height=16 * mm)
        logo.hAlign = "LEFT"
        head = Table([
            [logo, [_p(school, 12, TA_LEFT, GREEN, 14), _p(title_line, 11, TA_LEFT, TEXT, 13),
                    _p(f"{info_line}　|　{teacher_line}", 8, TA_LEFT)]]
        ], colWidths=[20 * mm, 260 * mm])
        head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
        story.append(head)
    else:
        story.extend([_p(school, 12, TA_LEFT, GREEN), _p(title_line, 11, TA_LEFT), _p(info_line + "　|　" + teacher_line, 8)])
    if address:
        story.append(_p(address, 7, TA_LEFT, colors.HexColor("#60746c")))
    story.append(Spacer(1, 2 * mm))

    summary_labels = ("มา", "ป่วย", "ลา", "ขาด", "หยุด", "รวม", "ร้อยละ")
    day_headers = [str(day) for day in range(1, days_in_month + 1)]
    rows = [["ที่", "รหัส", "ชื่อ-สกุล", f"วันที่ของเดือน {MONTHS[month]} พ.ศ. {buddhist_year}", *([""] * (days_in_month - 1)), "สรุปผล", *([""] * (len(summary_labels) - 1))]]
    rows.append(["", "", "", *day_headers, *summary_labels])
    status_codes = ("ม", "ป", "ล", "ข", "หยุด")
    for student in students:
        seat_no = student[0]
        def is_off_day(day):
            weekday = date(gregorian_year, month, day).weekday()
            outside_term = (
                (term == 1 and ((month == 5 and day < 16) or (month == 10 and day > 10)))
            )
            return weekday >= 5 or day in holiday_map or outside_term

        statuses = [
            saved.get((seat_no, day), "") or ("หยุด" if is_off_day(day) else "")
            for day in range(1, days_in_month + 1)
        ]
        counts = {status: statuses.count(status) for status in status_codes}
        recorded = sum(counts[status] for status in ("ม", "ป", "ล", "ข"))
        percent = counts["ม"] / recorded * 100 if recorded else 0.0
        student_code = student[1] or "—"
        full_name = f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip()
        rows.append([
            str(seat_no), str(student_code), _p(full_name, 6.6),
            *statuses,
            str(counts["ม"]), str(counts["ป"]), str(counts["ล"]), str(counts["ข"]),
            str(counts["หยุด"]), str(recorded), f"{percent:.2f}",
        ])

    widths = [7 * mm, 13 * mm, 37 * mm] + [4.8 * mm] * days_in_month + [7.2 * mm] * 6 + [11 * mm]
    table = Table(rows, colWidths=widths, repeatRows=2, hAlign="CENTER")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.38, GRID),
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 6.4),
        ("LEADING", (0, 0), (-1, -1), 7),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.3),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("BACKGROUND", (0, 0), (-1, 1), PALE),
        ("TEXTCOLOR", (0, 0), (-1, 1), GREEN),
        ("SPAN", (3, 0), (2 + days_in_month, 0)),
        ("SPAN", (3 + days_in_month, 0), (2 + days_in_month + len(summary_labels), 0)),
        ("SPAN", (0, 0), (0, 1)),
        ("SPAN", (1, 0), (1, 1)),
        ("SPAN", (2, 0), (2, 1)),
        ("ALIGN", (2, 2), (2, -1), "LEFT"),
    ]
    for day in range(1, days_in_month + 1):
        weekday = date(gregorian_year, month, day).weekday()
        day_column = 2 + day
        if day in holiday_map:
            commands.append(("BACKGROUND", (day_column, 0), (day_column, -1), HOLIDAY))
        elif weekday >= 5:
            commands.append(("BACKGROUND", (day_column, 0), (day_column, -1), WEEKEND))
        elif term == 1 and ((month == 5 and day < 16) or (month == 10 and day > 10)):
            commands.append(("BACKGROUND", (day_column, 0), (day_column, -1), OFFTERM))
    table.setStyle(TableStyle(commands))
    story.append(table)
    story.append(Spacer(1, 1.5 * mm))
    story.append(_p("หมายเหตุ: ม = มา　ป = ป่วย　ล = ลา　ข = ขาด　หยุด = วันหยุด/วันที่อยู่นอกภาคเรียน", 7))
    story.append(_p("สีม่วง = เสาร์–อาทิตย์　·　สีชมพู = วันหยุดราชการ　·　สีเหลือง = นอกภาคเรียน", 7, TA_LEFT, colors.HexColor("#60746c")))

    holiday_notes = [f"วันที่ {day}: {label}" for day, label in sorted(holiday_map.items())]
    if holiday_notes:
        story.append(_p("วันหยุดราชการ: " + "　·　".join(holiday_notes), 7))
    story.append(Spacer(1, 5 * mm))
    signer_names = (teacher, config.get("academic_head", ""), config.get("director", ""))
    signer_roles = ("ครูประจำชั้น/ครูผู้สอน", "หัวหน้าฝ่ายวิชาการ", "ผู้อำนวยการโรงเรียน")
    signature_cells = []
    for signer, role in zip(signer_names, signer_roles):
        signature_cells.append([
            _p(f"ลงชื่อ ........................................................", 8, TA_CENTER),
            _p(f"({signer or '........................................'})", 8, TA_CENTER),
            _p(role, 8, TA_CENTER),
        ])
    signatures = Table([[signature_cells[0], signature_cells[1], signature_cells[2]]], colWidths=[90 * mm, 90 * mm, 90 * mm])
    signatures.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(signatures)
    doc.build(story)
    return buffer.getvalue()
