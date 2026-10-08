"""Build a one-page, printer-ready PP5 course cover PDF."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from assessment import LEVEL_LABELS, overall_level
from database import get_connection
from exporter import SUBJECT_LIST, calc_grade


ROOT = Path(__file__).resolve().parent
FONT_PATH = ROOT / "Sarabun-Regular.ttf"
FONT_NAME = "CoverSarabun"
if FONT_PATH.is_file():
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
    pdfmetrics.registerFontFamily(
        FONT_NAME, normal=FONT_NAME, bold=FONT_NAME, italic=FONT_NAME, boldItalic=FONT_NAME
    )
else:
    FONT_NAME = "Helvetica"

GREEN = colors.HexColor("#145b4b")
LIGHT_GREEN = colors.HexColor("#edf4f0")
GRID = colors.HexColor("#82918b")
INK = colors.HexColor("#20312d")
GRADE_LEVELS = ("4", "3.5", "3", "2.5", "2", "1.5", "1", "0", "ร", "มส", "รอประเมิน")
ASSESSMENT_LEVELS = ("ดีเยี่ยม", "ดี", "ผ่าน", "ไม่ผ่าน", "ยังไม่ประเมิน")


def _grade_code(grade: str) -> str:
    prefix = "ชั้นประถมศึกษาปีที่ "
    return f"ป.{grade[len(prefix):].strip()}" if grade.startswith(prefix) else grade


def _subject_code(subject_name: str, grade: str) -> str:
    code = subject_name.partition(" ")[0]
    thai_to_ascii = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
    grade_number = "".join(character for character in grade.translate(thai_to_ascii) if character.isdigit())
    if len(code) >= 3 and grade_number and grade_number[-1] in "123456":
        code = code[:2] + "๐๑๒๓๔๕๖"[int(grade_number[-1])] + code[3:]
    return code


def _count_levels(conn, table: str, columns: tuple[str, ...], term: int, students: list[tuple]) -> dict[str, int]:
    counts = dict.fromkeys(ASSESSMENT_LEVELS, 0)
    for student in students:
        values = conn.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE seat_no=? AND term=?",
            (student[0], term),
        ).fetchone()
        result = overall_level(values) if values else None
        label = LEVEL_LABELS.get(result, "ยังไม่ประเมิน")
        counts[label] += 1
    return counts


def _student_gender_counts(conn, grade: str, students: list[tuple]) -> tuple[int, int, int]:
    profiles = conn.execute(
        "SELECT student_id, gender FROM student_profiles WHERE grade=?",
        (_grade_code(grade),),
    ).fetchall()
    by_id = {str(student_id): (gender or "").strip().lower() for student_id, gender in profiles}
    male = female = 0
    for student in students:
        gender = by_id.get(str(student[1]), "")
        if gender in {"ช", "ชาย", "m", "male"}:
            male += 1
        elif gender in {"ญ", "หญิง", "f", "female"}:
            female += 1
    return male, female, len(students) - male - female


def _p(text: object, style: ParagraphStyle) -> Paragraph:
    safe_text = str(text if text is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(safe_text, style)


def _table(data: list[list], widths: list[float], *, header=True, font_size=10) -> Table:
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="CENTER")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.55, GRID),
        ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("LEADING", (0, 0), (-1, -1), font_size + 2),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        commands.extend([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT_GREEN),
            ("TEXTCOLOR", (0, 0), (-1, 0), GREEN),
        ])
    table.setStyle(TableStyle(commands))
    return table


def build_pp5_cover_pdf(subject_key: str, semester: str, room: str = "1") -> bytes:
    """Return a fixed-layout PDF cover for a subject, semester, and class."""
    known_subjects = {key: (name, kind) for key, name, kind in SUBJECT_LIST}
    if subject_key not in known_subjects:
        raise ValueError("เลือกรายวิชาที่ถูกต้องก่อนสร้างเอกสาร")
    if semester not in {"1", "2", "ทั้งปี"}:
        raise ValueError("เลือกภาคเรียนที่ถูกต้องก่อนสร้างเอกสาร")

    subject_name, subject_type = known_subjects[subject_key]
    conn = get_connection()
    try:
        cfg = dict(conn.execute("SELECT key, val FROM config").fetchall())
        students = conn.execute(
            "SELECT seat_no, student_id, title, first_name, last_name FROM students ORDER BY seat_no"
        ).fetchall()
        school = cfg.get("school_name", "")
        grade = cfg.get("grade", "")
        year = cfg.get("year", "")
        teacher = cfg.get("teacher_1", "")
        address = cfg.get("address", "")
        male, female, unspecified = _student_gender_counts(conn, grade, students)

        grades = dict.fromkeys(GRADE_LEVELS, 0)
        saved_weight = conn.execute("SELECT val FROM config WHERE key='grade_term1_weight'").fetchone()
        try:
            term1_weight = float(str(saved_weight[0]).strip().removesuffix("%")) if saved_weight else 50.0
        except (TypeError, ValueError):
            term1_weight = 50.0
        term1_weight = min(100.0, max(0.0, term1_weight))
        complete_score_count = 0
        for student in students:
            seat_no = student[0]
            terms = (1, 2) if semester == "ทั้งปี" else (int(semester),)
            scores = []
            for term in terms:
                row = conn.execute(
                    "SELECT formative, exam FROM subject_scores WHERE seat_no=? AND subject_key=? AND term=?",
                    (seat_no, subject_key, term),
                ).fetchone()
                if row is None or any(value is None for value in row):
                    scores = []
                    break
                scores.append(sum(row))
            if not scores:
                grades["รอประเมิน"] += 1
            else:
                complete_score_count += 1
                score = (
                    round((scores[0] * term1_weight + scores[1] * (100 - term1_weight)) / 100, 1)
                    if semester == "ทั้งปี" else round(scores[0], 1)
                )
                grades[calc_grade(score)] += 1

        summary_term = 2 if semester == "ทั้งปี" else int(semester)
        traits = _count_levels(conn, "desired_traits", tuple(f"l{i}" for i in range(1, 9)), summary_term, students)
        reading = _count_levels(conn, "reading_analysis", ("a1", "a2", "a3"), summary_term, students)
    finally:
        conn.close()

    stream = BytesIO()
    frame = Frame(15 * mm, 13 * mm, A4[0] - 30 * mm, A4[1] - 26 * mm, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc = BaseDocTemplate(stream, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=13 * mm, bottomMargin=13 * mm)
    doc.addPageTemplates([PageTemplate(id="cover", frames=[frame])])

    title = ParagraphStyle("CoverTitle", fontName=FONT_NAME, fontSize=19, leading=22, alignment=TA_CENTER, textColor=INK, spaceAfter=1)
    subtitle = ParagraphStyle("CoverSubtitle", fontName=FONT_NAME, fontSize=14, leading=16, alignment=TA_CENTER, textColor=INK)
    section = ParagraphStyle("CoverSection", fontName=FONT_NAME, fontSize=12, leading=14, textColor=GREEN, spaceBefore=5, spaceAfter=2)
    normal = ParagraphStyle("CoverNormal", fontName=FONT_NAME, fontSize=10, leading=12, textColor=INK)
    centered = ParagraphStyle("CoverCentered", parent=normal, alignment=TA_CENTER)

    story = []
    logo = ROOT / "assets" / "school_logo.jpg"
    if logo.is_file():
        mark = Image(str(logo), width=19 * mm, height=19 * mm)
        mark.hAlign = "CENTER"
        story.extend([mark, Spacer(1, 1 * mm)])
    story.extend([
        _p("แบบบันทึกผลการเรียนประจำรายวิชา (ปพ.5)", title),
        _p(school, subtitle),
        _p(address, normal),
        Spacer(1, 3 * mm),
    ])

    metadata = [
        ["ชั้น", grade, "ห้อง", room],
        ["ปีการศึกษา", year, "ภาคเรียน", "ทั้งปีการศึกษา" if semester == "ทั้งปี" else f"ภาคเรียนที่ {semester}"],
        ["รหัสวิชา", _subject_code(subject_name, grade), "รายวิชา", f"{subject_name.partition(' ')[-1]} ({subject_type})"],
        ["ครูประจำวิชา", teacher or "................................", "สัดส่วนรายปี", f"ภาค 1 {term1_weight:g}% / ภาค 2 {100 - term1_weight:g}%"],
    ]
    meta = _table(metadata, [27 * mm, 48 * mm, 27 * mm, 78 * mm], header=False, font_size=9.5)
    meta.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_GREEN),
        ("BACKGROUND", (2, 0), (2, -1), LIGHT_GREEN),
        ("TEXTCOLOR", (0, 0), (0, -1), GREEN),
        ("TEXTCOLOR", (2, 0), (2, -1), GREEN),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("ALIGN", (2, 0), (2, -1), "LEFT"),
    ]))
    story.extend([meta, Spacer(1, 2 * mm), _p("ข้อมูลนักเรียน", section)])
    total = len(students)
    gender_rows = [
        ["รายการ", "ชาย", "หญิง", "รวม"],
        ["จำนวน (คน)", str(male), str(female), str(total)],
        ["ร้อยละ (%)", f"{male / total * 100:.2f}" if total else "0.00", f"{female / total * 100:.2f}" if total else "0.00", "100.00" if total else "0.00"],
    ]
    story.append(_table(gender_rows, [52 * mm, 42 * mm, 42 * mm, 66 * mm], font_size=9.5))
    story.append(_p("สรุปผลสัมฤทธิ์ทางการเรียน", section))
    grade_rows = [["ระดับผลการเรียน", *GRADE_LEVELS, "รวม"]]
    grade_rows.append(["จำนวน (คน)", *[str(grades[level]) for level in GRADE_LEVELS], str(total)])
    grade_rows.append(["ร้อยละ (%)", *[f"{grades[level] / total * 100:.2f}" if total else "0.00" for level in GRADE_LEVELS], "100.00" if total else "0.00"])
    available = A4[0] - 30 * mm
    first_width = 27 * mm
    other_width = (available - first_width) / (len(GRADE_LEVELS) + 1)
    story.append(_table(grade_rows, [first_width] + [other_width] * (len(GRADE_LEVELS) + 1), font_size=7.8))
    total = len(students)
    pending_score_count = total - complete_score_count
    period_label = "ทั้งปีการศึกษา" if semester == "ทั้งปี" else f"ภาคเรียนที่ {semester}"
    story.extend([
        Spacer(1, 1.5 * mm),
        _p(
            f"สถานะคะแนน ({period_label}): บันทึกครบ {complete_score_count} คน · รอข้อมูล {pending_score_count} คน",
            normal,
        ),
    ])

    def assessment_table(label: str, counts: dict[str, int]) -> Table:
        rows = [[label, *ASSESSMENT_LEVELS, "รวม"],
                ["จำนวน (คน)", *[str(counts[level]) for level in ASSESSMENT_LEVELS], str(total)],
                ["ร้อยละ (%)", *[f"{counts[level] / total * 100:.2f}" if total else "0.00" for level in ASSESSMENT_LEVELS], "100.00" if total else "0.00"]]
        return _table(rows, [34 * mm] + [27 * mm] * 5, font_size=8.5)

    story.extend([
        _p("คุณลักษณะอันพึงประสงค์", section),
        assessment_table("ระดับ", traits),
        _p("การอ่าน คิดวิเคราะห์ และเขียน", section),
        assessment_table("ระดับ", reading),
        Spacer(1, 5 * mm),
        _p(f"ผลการเรียนประจำ{period_label}　 □ อนุมัติ　 □ ไม่อนุมัติ　 วันที่ ........../........../..........", centered),
        Spacer(1, 2 * mm),
        _p("ลงชื่อ ................................................ ครูประจำวิชา　　 ลงชื่อ ................................................ ผู้บริหาร", centered),
        _p(f"({teacher or '................................................'})　　　　　　　　　(................................................)", centered),
        Spacer(1, 3 * mm),
        _p("หมายเหตุ: แบบบันทึกผลรายวิชาเป็นเอกสารที่สถานศึกษาจัดทำ และสร้างจากข้อมูลในระบบ ณ วันที่พิมพ์", ParagraphStyle("Note", parent=normal, fontSize=8, textColor=colors.HexColor("#60746c"))),
    ])
    doc.build(story)
    return stream.getvalue()
