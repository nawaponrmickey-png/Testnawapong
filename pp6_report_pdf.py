"""Create a one-page, individual PP6 learner progress report PDF."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from assessment import LEVEL_LABELS, overall_level
from database import get_connection
from exporter import ACTIVITY_LIST, COMPETENCY_NAMES, SUBJECT_LIST, calc_grade


ROOT = Path(__file__).resolve().parent
FONT_PATH = ROOT / "Sarabun-Regular.ttf"
FONT = "PP6Sarabun"
if FONT_PATH.is_file():
    pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT, italic=FONT, boldItalic=FONT)
else:
    FONT = "Helvetica"

GREEN = colors.HexColor("#145b4b")
PALE = colors.HexColor("#edf4f0")
TEXT = colors.HexColor("#20312d")
GRID = colors.HexColor("#707c77")


def _p(value, size=8, align=TA_LEFT, color=TEXT, leading=None):
    safe = str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    style = ParagraphStyle(
        f"pp6-{size}-{align}", fontName=FONT, fontSize=size,
        leading=leading or size + 2, alignment=align, textColor=color,
        wordWrap="CJK", splitLongWords=1,
    )
    return Paragraph(safe, style)


def _table(rows, widths, *, font_size=7.5, header_rows=1, h_align="CENTER"):
    table = Table(rows, colWidths=widths, repeatRows=header_rows, hAlign=h_align)
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.45, GRID),
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("LEADING", (0, 0), (-1, -1), font_size + 1),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    if header_rows:
        commands.extend([
            ("BACKGROUND", (0, 0), (-1, header_rows - 1), PALE),
            ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), GREEN),
        ])
    table.setStyle(TableStyle(commands))
    return table


def _grade_code(grade):
    prefix = "ชั้นประถมศึกษาปีที่ "
    return f"ป.{grade[len(prefix):].strip()}" if grade.startswith(prefix) else grade


def _label(levels):
    result = overall_level(levels)
    return LEVEL_LABELS.get(result, "ยังไม่ประเมิน")


def build_student_pp6_pdf(seat_no: int, term: int) -> bytes:
    if term not in (1, 2):
        raise ValueError("เลือกภาคเรียนที่ 1 หรือ 2")

    conn = get_connection()
    try:
        cfg = dict(conn.execute("SELECT key, val FROM config").fetchall())
        roster = conn.execute(
            "SELECT seat_no, citizen_id, student_id, title, first_name, last_name FROM students ORDER BY seat_no"
        ).fetchall()
        student = next((row for row in roster if row[0] == seat_no), None)
        if student is None:
            raise ValueError("ไม่พบรายชื่อนักเรียน")

        school = cfg.get("school_name", "")
        address = cfg.get("address", "")
        grade = cfg.get("grade", "")
        year = cfg.get("year", "")
        teacher = cfg.get("teacher_1", "")
        academic_head = cfg.get("academic_head", "")
        director = cfg.get("director", "")
        name = f"{student[3] or ''}{student[4] or ''} {student[5] or ''}".strip()
        student_id = student[2] or "-"

        weight_record = conn.execute("SELECT val FROM config WHERE key='grade_term1_weight'").fetchone()
        course_results = []
        for key, subject_name, subject_type in SUBJECT_LIST:
            scores = {}
            class_scores = []
            for class_student in roster:
                row = conn.execute(
                    "SELECT formative, exam FROM subject_scores WHERE seat_no=? AND subject_key=? AND term=?",
                    (class_student[0], key, term),
                ).fetchone()
                scores[class_student[0]] = row
                if row is not None and all(value is not None for value in row):
                    class_scores.append(sum(row))
            own = scores.get(seat_no)
            complete = own is not None and all(value is not None for value in own)
            own_score = round(sum(own), 1) if complete else None
            result = calc_grade(own_score) if complete else "รอประเมิน"
            average = round(sum(class_scores) / len(class_scores), 2) if class_scores else None
            course_code, _, short_name = subject_name.partition(" ")
            course_results.append({
                "key": key,
                "code": course_code,
                "name": short_name or subject_name,
                "type": subject_type,
                "class_average": average,
                "score": own_score,
                "grade": result,
                "complete": complete,
            })

        rankings = []
        for class_student in roster:
            student_values = []
            for key, _, _ in SUBJECT_LIST:
                row = conn.execute(
                    "SELECT formative, exam FROM subject_scores WHERE seat_no=? AND subject_key=? AND term=?",
                    (class_student[0], key, term),
                ).fetchone()
                if row is not None and all(value is not None for value in row):
                    student_values.append(float(calc_grade(sum(row))))
            average_grade = sum(student_values) / len(student_values) if student_values else None
            rankings.append((class_student[0], average_grade))
        own_gpa = next((value for number, value in rankings if number == seat_no), None)
        sorted_gpas = sorted((value for _, value in rankings if value is not None), reverse=True)
        rank = sorted_gpas.index(own_gpa) + 1 if own_gpa is not None else None

        activities = dict(conn.execute(
            "SELECT activity_key, result FROM activities WHERE seat_no=? AND term=?", (seat_no, term)
        ).fetchall())
        activities = {key: {"ผ": "ผ่าน", "มผ": "ไม่ผ่าน"}.get(value, value) for key, value in activities.items()}
        trait_row = conn.execute(
            "SELECT l1,l2,l3,l4,l5,l6,l7,l8 FROM desired_traits WHERE seat_no=? AND term=?",
            (seat_no, term),
        ).fetchone()
        reading_row = conn.execute(
            "SELECT a1,a2,a3 FROM reading_analysis WHERE seat_no=? AND term=?",
            (seat_no, term),
        ).fetchone()
        competencies = conn.execute(
            "SELECT c1,c2,c3,c4,c5 FROM competency_assessments WHERE seat_no=? AND term=?",
            (seat_no, term),
        ).fetchone() or ("ยังไม่ประเมิน",) * len(COMPETENCY_NAMES)

        grade_prefix = _grade_code(grade)
        profile = conn.execute(
            "SELECT guardian_title, guardian_first_name, guardian_last_name "
            "FROM student_profiles WHERE grade=? AND student_id=?",
            (grade_prefix, str(student_id)),
        ).fetchone()
        guardian = "".join((profile[0] or "", profile[1] or " ", profile[2] or "")).strip() if profile else ""
    finally:
        conn.close()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=10 * mm, bottomMargin=10 * mm,
        title=f"ปพ.6 {name}",
    )
    story = []
    logo_path = ROOT / "assets" / "school_logo.jpg"
    if logo_path.is_file():
        logo = Image(str(logo_path), width=17 * mm, height=17 * mm)
        logo.hAlign = "CENTER"
        story.extend([logo, Spacer(1, 1 * mm)])
    story.extend([
        _p("แบบรายงานประจำตัวนักเรียน : ผลการพัฒนาคุณภาพผู้เรียนรายบุคคล (ปพ.6)", 15, TA_CENTER, GREEN, 18),
        _p(school, 12, TA_CENTER, TEXT, 14),
        _p(address, 9, TA_CENTER),
        _p(f"{grade} ห้อง {cfg.get('room', '1')}　ภาคเรียนที่ {term}　ปีการศึกษา {year}", 9, TA_CENTER),
        Spacer(1, 2 * mm),
    ])
    student_info = [[
        _p(f"ชื่อ-สกุล　{name}", 9),
        _p(f"รหัสประจำตัว　{student_id}", 9, TA_CENTER),
        _p(f"เลขที่　{seat_no}", 9, TA_CENTER),
    ]]
    story.append(_table(student_info, [100 * mm, 45 * mm, 36 * mm], header_rows=1, font_size=9))
    story.append(Spacer(1, 2 * mm))

    rows = [["รหัสวิชา", "รายวิชา", "ประเภท", "คะแนนเต็ม", "เฉลี่ยห้อง", "คะแนนที่ได้", "ผลการเรียน", "หมายเหตุ"]]
    for subject_type in ("พื้นฐาน", "เพิ่มเติม"):
        rows.append([_p(f"ประเภทรายวิชา: วิชา{subject_type}", 8, TA_LEFT, GREEN), "", "", "", "", "", "", ""])
        for course in course_results:
            if course["type"] != subject_type:
                continue
            show = lambda value: "—" if value is None else f"{value:g}"
            rows.append([
                course["code"],
                _p(course["name"], 7.4),
                "พื้นฐาน" if subject_type == "พื้นฐาน" else "เพิ่มเติม",
                "100",
                show(course["class_average"]),
                show(course["score"]),
                course["grade"],
                "" if course["complete"] else "รอข้อมูล",
            ])
        rows.append(["", "", "", "", "", "", "", ""])

    summary_score = sum(item["score"] for item in course_results if item["score"] is not None)
    completed_courses = sum(item["score"] is not None for item in course_results)
    gpa_text = f"{own_gpa:.2f}" if own_gpa is not None else "—"
    rank_text = str(rank) if rank is not None else "—"
    rows.append([_p("รวมคะแนนรายวิชาที่บันทึกครบ", 8, TA_LEFT), "", "", "", "", f"{summary_score:g}", "", ""])
    score_table = _table(
        rows,
        [21 * mm, 43 * mm, 17 * mm, 17 * mm, 19 * mm, 18 * mm, 22 * mm, 18 * mm],
        header_rows=1,
        font_size=7.2,
    )
    score_table.setStyle(TableStyle([
        ("SPAN", (0, 1), (-1, 1)),
        ("SPAN", (0, -1), (4, -1)),
        ("BACKGROUND", (0, 1), (-1, 1), PALE),
        ("BACKGROUND", (0, -2), (-1, -2), colors.white),
        ("ALIGN", (1, 2), (1, -1), "LEFT"),
    ]))
    # Merge the category band for the second subject group too.
    second_group_row = 2 + sum(1 for course in course_results if course["type"] == "พื้นฐาน") + 1
    if second_group_row < len(rows) - 1:
        score_table.setStyle(TableStyle([
            ("SPAN", (0, second_group_row), (-1, second_group_row)),
            ("BACKGROUND", (0, second_group_row), (-1, second_group_row), PALE),
        ]))
    story.append(score_table)
    story.append(Spacer(1, 2 * mm))

    left_rows = [
        ["GPA (เฉลี่ยระดับผลการเรียน)", gpa_text],
        ["อันดับในห้อง", rank_text],
        ["รายวิชาที่มีคะแนนครบ", f"{completed_courses}/{len(SUBJECT_LIST)}"],
        ["ผลกิจกรรมพัฒนาผู้เรียน", ""],
    ]
    for activity_key, activity_name in ACTIVITY_LIST:
        left_rows.append([activity_name.replace("กิจกรรม", "").strip(), activities.get(activity_key, "—") or "—"])
    traits = trait_row or (None,) * 8
    reading = reading_row or (None,) * 3
    left_rows.extend([
        ["ผลประเมินคุณลักษณะอันพึงประสงค์", _label(traits)],
        ["ผลประเมินการอ่าน คิดวิเคราะห์ และเขียน", _label(reading)],
        ["ผลประเมินสมรรถนะสำคัญของผู้เรียน", ""],
    ])
    for name, result in zip(COMPETENCY_NAMES, competencies):
        short_name = name.replace("ความสามารถในการ", "")
        left_rows.append([short_name, result or "ยังไม่ประเมิน"])

    left_table = _table(
        [[_p(label, 7.4, TA_LEFT), _p(value, 7.4, TA_CENTER)] for label, value in left_rows],
        [73 * mm, 22 * mm], header_rows=0, font_size=7.4,
    )
    for index, row in enumerate(left_rows):
        if row[1] == "":
            left_table.setStyle(TableStyle([
                ("SPAN", (0, index), (1, index)),
                ("BACKGROUND", (0, index), (1, index), PALE),
                ("TEXTCOLOR", (0, index), (1, index), GREEN),
            ]))

    signers = [
        (teacher, "ครูประจำชั้น / ครูที่ปรึกษา"),
        (academic_head, "หัวหน้าฝ่ายวิชาการ"),
        (director, "ผู้อำนวยการโรงเรียน"),
        (guardian, "ผู้ปกครองนักเรียน"),
    ]
    signature_rows = []
    for signer, role in signers:
        signature_rows.append([_p("ลงชื่อ", 8, TA_CENTER)])
        signature_rows.append([_p("........................................................", 8, TA_CENTER)])
        signature_rows.append([_p(f"({signer or '........................................'})", 8, TA_CENTER)])
        signature_rows.append([_p(role, 8, TA_CENTER)])
        signature_rows.append([_p("วันที่ ........../........../..........", 8, TA_CENTER)])
        signature_rows.append([Spacer(1, 1.5 * mm)])
    signature_table = Table(signature_rows, colWidths=[85 * mm], hAlign="CENTER")
    signature_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 0.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
    ]))
    lower = Table([[left_table, signature_table]], colWidths=[98 * mm, 83 * mm], hAlign="CENTER")
    lower.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.45, GRID),
        ("LINEBEFORE", (1, 0), (1, 0), 0.45, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.extend([lower, Spacer(1, 1.5 * mm), _p("หมายเหตุ: ระบบยังไม่เก็บหน่วยกิต GPA และอันดับจึงคำนวณจากค่าเฉลี่ยระดับผลการเรียนของวิชาที่มีข้อมูล", 7, TA_LEFT, colors.HexColor("#60746c"))])
    doc.build(story)
    return buffer.getvalue()
