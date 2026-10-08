import calendar
import base64
import json
import os
import random
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import altair as alt

from audit_log import ACTION_LABELS, AUDITED_TABLES, TABLE_LABELS, get_audit_events
from assessment import DISPLAY_TO_LEVEL, LEVEL_LABELS, LEVEL_OPTION_LABELS, LEVEL_TO_LABEL, overall_level, percentage_or_default
from academic_year_migration import (
    migrate_students_to_next_year,
    migration_source_grades,
    preview_year_migration,
)
from database import (
    get_active_academic_year,
    get_database_path,
    get_connection,
    init_db,
    list_available_academic_years,
)
from exporter import ACTIVITY_LIST, COMPETENCY_NAMES, SUBJECT_LIST, TRAIT_NAMES, calc_grade, get_real_indicators
from import_data import GRADE_MAPPING, import_grade_data
from pp6_report_pdf import build_student_pp6_pdf
from report_completeness import report_completeness_messages
from web_reports import build_individual_report


DISPLAY_COLOR_OPTIONS = {
    "ดำเข้ม": "#111827",
    "น้ำเงินเข้ม": "#1E3A5F",
    "เขียวเข้ม": "#20312D",
    "เทาเข้ม": "#374151",
}
DEFAULT_DISPLAY_SETTINGS = {
    "text_size": 20,
    "text_color": "#111827",
    "table_text_size": 17,
    "table_row_height": 40,
}
DISPLAY_THEME_KEYS = {"baseFontSize", "textColor", "sidebar.textColor"}


def create_database_backup():
    database_path = get_database_path()
    with tempfile.TemporaryDirectory() as temp_dir:
        backup_path = Path(temp_dir) / "pp5_system.db"
        with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as backup:
            source.backup(backup)
        return backup_path.read_bytes()


def save_database_backup():
    app_root = get_database_path().parent
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    academic_year = get_active_academic_year()
    backup_path = app_root / f"สำรองระบบปพ5_{academic_year}_{timestamp}.db"
    temp_path = app_root / f".{backup_path.name}.tmp"
    try:
        temp_path.write_bytes(create_database_backup())
        temp_path.replace(backup_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return backup_path


def restore_database_backup(database_bytes):
    database_path = get_database_path()
    required_tables = {
        "config", "students", "student_profiles", "attendance_daily", "subject_scores",
        "activities", "desired_traits", "reading_analysis", "competency_assessments",
    }
    with tempfile.TemporaryDirectory(dir=database_path.parent) as temp_dir:
        temp_dir = Path(temp_dir)
        uploaded_path = temp_dir / "uploaded.db"
        restored_path = temp_dir / "restored.db"
        uploaded_path.write_bytes(database_bytes)
        with sqlite3.connect(uploaded_path) as uploaded:
            if uploaded.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("ไฟล์ฐานข้อมูลเสียหาย")
            tables = {
                row[0] for row in uploaded.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not required_tables.issubset(tables):
                raise ValueError("ไฟล์นี้ไม่ใช่ไฟล์สำรองของระบบ ปพ.5 นี้")
            with sqlite3.connect(restored_path) as restored:
                uploaded.backup(restored)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_copy = database_path.with_name(f"pp5_system_before_restore_{timestamp}.db")
        if database_path.exists():
            database_path.replace(safety_copy)
        try:
            restored_path.replace(database_path)
        except Exception:
            if safety_copy.exists():
                safety_copy.replace(database_path)
            raise
        for suffix in ("-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
        init_db()
    return safety_copy if safety_copy.exists() else None


def clear_assessment_data():
    """Clear all saved assessment values while keeping students and attendance."""
    assessment_tables = (
        "subject_scores",
        "indicator_assessments",
        "desired_traits",
        "reading_analysis",
        "competency_assessments",
        "activities",
    )
    safety_copy = save_database_backup()
    conn = get_connection()
    counts = {}
    try:
        conn.execute("BEGIN")
        for table in assessment_tables:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM config WHERE key IN (?, ?, ?)", (
            "demo_pre_simulation_backup",
            "demo_simulation_status",
            "demo_cancel_safety_backup",
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return safety_copy, counts


def load_display_settings():
    conn = get_connection()
    saved = dict(
        conn.execute(
            "SELECT key, val FROM config WHERE key LIKE 'display_%'"
        ).fetchall()
    )
    conn.close()
    text_color = saved.get("display_text_color", DEFAULT_DISPLAY_SETTINGS["text_color"])
    if text_color not in DISPLAY_COLOR_OPTIONS.values():
        text_color = DEFAULT_DISPLAY_SETTINGS["text_color"]
    return {
        "text_size": min(26, max(16, int(saved.get("display_text_size", DEFAULT_DISPLAY_SETTINGS["text_size"])))),
        "text_color": text_color,
        "table_text_size": min(
            22, max(14, int(saved.get("display_table_text_size", DEFAULT_DISPLAY_SETTINGS["table_text_size"])))
        ),
        "table_row_height": min(
            60, max(28, int(saved.get("display_table_row_height", DEFAULT_DISPLAY_SETTINGS["table_row_height"])))
        ),
    }


def save_display_theme(table_text_size, text_color):
    theme_path = Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml"
    lines = theme_path.read_text(encoding="utf-8").splitlines()
    section = ""
    updated = set()
    theme_font_size = round(table_text_size * 8 / 7)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if section == "[theme]" and stripped.startswith("baseFontSize ="):
            lines[index] = f"baseFontSize = {theme_font_size}"
            updated.add("baseFontSize")
        elif section == "[theme]" and stripped.startswith("textColor ="):
            lines[index] = f'textColor = "{text_color}"'
            updated.add("textColor")
        elif section == "[theme.sidebar]" and stripped.startswith("textColor ="):
            lines[index] = f'textColor = "{text_color}"'
            updated.add("sidebar.textColor")
    if updated != DISPLAY_THEME_KEYS:
        raise RuntimeError("ไม่พบค่าธีมที่ต้องอัปเดตใน .streamlit/config.toml")
    theme_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def install_management_typography(settings):
    app_root = Path(__file__).resolve().parent.parent
    regular_path = app_root / "Sarabun-Regular.ttf"
    regular_font = base64.b64encode(regular_path.read_bytes()).decode("ascii") if regular_path.exists() else ""
    text_size = settings["text_size"]
    text_color = settings["text_color"]
    st.markdown(
        f'''<style>
        @font-face {{font-family: "PP5Sarabun"; src: url(data:font/ttf;base64,{regular_font}) format("truetype"); font-weight: 400;}}
        .stApp {{font-family: "PP5Sarabun", sans-serif; font-size: {text_size}px; line-height: 1.55;
            color: {text_color}; --text-color: {text_color};
        }}
        .stApp p, .stApp label, .stApp input, .stApp textarea, .stApp button,
        .stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stCaptionContainer"] {{
            font-family: "PP5Sarabun", sans-serif;
        }}
        .stApp p {{font-size: {text_size}px; line-height: 1.55;}}
        .stApp label {{font-size: {text_size - 1}px; line-height: 1.45;}}
        .stApp [data-testid="stCaptionContainer"] {{font-size: {text_size - 3}px; line-height: 1.5;}}
        .stApp h1, .stApp h2, .stApp h3 {{font-family: "PP5Sarabun", sans-serif; font-weight: 700; line-height: 1.3;}}
        .stApp h1 {{font-size: {round(text_size * 1.7)}px;}}
        .stApp h2 {{font-size: {round(text_size * 1.43)}px;}}
        .stApp h3 {{font-size: {round(text_size * 1.2)}px;}}
        .stApp input, .stApp textarea, .stApp button {{font-size: {text_size - 1}px;}}
        [data-testid="stSidebar"] {{font-family: "PP5Sarabun", sans-serif;}}
        [data-testid="stSidebar"] p, [data-testid="stSidebar"] label {{font-size: {text_size - 1}px; line-height: 1.5;}}
        </style>''',
        unsafe_allow_html=True,
    )


display_settings = load_display_settings()
install_management_typography(display_settings)

MENU = {
    "ข้อมูลสถานศึกษา": ("ข้อมูลสถานศึกษา", ":material/apartment:", "ตั้งค่าข้อมูลโรงเรียนและปีการศึกษา"),
    "ประวัตินักเรียน ป.1–ป.6": ("ประวัตินักเรียน ป.1–ป.6", ":material/family_restroom:", "ข้อมูลนักเรียน ผู้ปกครอง และที่อยู่จากทะเบียนโรงเรียน"),
    "ทะเบียนนักเรียน": ("ทะเบียนนักเรียน", ":material/groups:", "จัดการรายชื่อนักเรียนในชั้นเรียน"),
    "สถิติคะแนน": ("สถิติคะแนน", ":material/query_stats:", "ดูภาพรวมคะแนน เกรด และผลการเรียนแยกรายวิชา"),
    "บันทึกคะแนน": ("บันทึกคะแนน", ":material/grade:", "บันทึกคะแนนเก็บและคะแนนสอบแยกตามรายวิชา"),
    "ตัวชี้วัด": ("ตัวชี้วัด", ":material/checklist:", "ทำเครื่องหมายผลการประเมินตัวชี้วัดรายวิชา"),
    "เวลาเรียน": ("เวลาเรียน", ":material/calendar_month:", "บันทึกสถานะการเข้าเรียนในแต่ละวัน"),
    "กิจกรรมพัฒนาผู้เรียน": ("กิจกรรมพัฒนาผู้เรียน", ":material/extension:", "บันทึกผลการเข้าร่วมกิจกรรมของนักเรียน"),
    "คุณลักษณะ": ("คุณลักษณะอันพึงประสงค์", ":material/stars:", "ประเมินคุณลักษณะอันพึงประสงค์รายภาคเรียน"),
    "การอ่าน คิดวิเคราะห์ และเขียน": ("การอ่าน คิดวิเคราะห์ และเขียน", ":material/menu_book:", "บันทึกผลประเมินรายภาคเรียน"),
    "สมรรถนะ": ("สมรรถนะสำคัญของผู้เรียน", ":material/psychology:", "ประเมินสมรรถนะสำคัญ 5 ด้านรายภาคเรียน"),
    "ปพ.6 รายบุคคล": ("ปพ.6 รายบุคคล", ":material/description:", "สร้าง ปพ.6 รายบุคคลเป็นไฟล์เว็บหรือ PDF"),
    "ส่งออกเอกสาร": ("ส่งออกเอกสาร", ":material/file_download:", "เปิดหน้าเว็บเอกสาร ปพ.5 และ ปพ.6 สำหรับพิมพ์"),
    "ตั้งค่าการแสดงผล": ("ตั้งค่าการแสดงผล", ":material/tune:", "ปรับตัวหนังสือ สี และขนาดตาราง"),
    "สำรองข้อมูลระบบ": ("สำรองข้อมูลระบบ", ":material/database:", "ดาวน์โหลดและกู้คืนข้อมูลทั้งหมดของระบบ"),
    "ประวัติการแก้ไข": ("ประวัติการแก้ไข", ":material/history:", "ตรวจสอบรายการเพิ่ม แก้ไข และลบข้อมูล"),
}

# วันหยุดราชการในช่วงปีการศึกษา 2569 (พ.ค. 2569 – มี.ค. 2570)
# ไม่รวมวันหยุดเฉพาะธนาคาร/แรงงาน และวันหยุดพิเศษที่ยังไม่ได้ประกาศใช้
PUBLIC_HOLIDAYS_2569 = {
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
TERM_DATE_RANGES_2569 = {
    1: ((2026, 5, 16), (2026, 10, 10)),
    2: ((2026, 11, 1), (2027, 3, 31)),
}


def get_term_date_ranges(academic_year):
    academic_year = int(academic_year)
    if academic_year == 2569:
        return TERM_DATE_RANGES_2569
    first_gregorian_year = academic_year - 543
    return {
        1: ((first_gregorian_year, 5, 16), (first_gregorian_year, 10, 10)),
        2: ((first_gregorian_year, 11, 1), (first_gregorian_year + 1, 3, 31)),
    }


def is_in_term(term, year, month, day, academic_year):
    school_date = (year, month, day)
    start_date, end_date = get_term_date_ranges(academic_year)[term]
    return start_date <= school_date <= end_date


def get_students(conn):
    return conn.execute(
        "SELECT seat_no, student_id, title, first_name, last_name "
        "FROM students ORDER BY seat_no ASC"
    ).fetchall()


def show_student_count(students):
    st.metric("นักเรียนในทะเบียน", f"{len(students):,} คน", border=True)
    if not students:
        st.info("ยังไม่มีรายชื่อนักเรียน เริ่มต้นโดยโหลดรายชื่อจากหน้าข้อมูลสถานศึกษา")
        return False
    return True


def save_button(label="บันทึกข้อมูล", key=None):
    return st.button(label, key=key, type="primary", icon=":material/save:")


def clear_assessment_editor_state():
    editor_prefixes = (
        "score_editor_v2_",
        "indicator_matrix_editor_",
        "trait_editor_named_",
        "trait_editor_level_",
        "reading_editor_",
        "competency_editor_",
        "activity_editor_",
    )
    for key in list(st.session_state.keys()):
        if key.startswith(editor_prefixes):
            st.session_state.pop(key, None)


def fill_missing_demo_assessments(conn):
    students = get_students(conn)
    config = dict(conn.execute("SELECT key, val FROM config").fetchall())
    grade = config.get("grade", "ชั้นประถมศึกษาปีที่ 1")
    counts = {"คะแนนรายวิชา": 0, "ตัวชี้วัด": 0, "คุณลักษณะ": 0, "การอ่านฯ": 0, "สมรรถนะ": 0, "กิจกรรม": 0}

    for student_index, student in enumerate(students):
        seat_no = student[0]

        for subject_key, _, _ in SUBJECT_LIST:
            for term in (1, 2):
                maxima = []
                for component, default in (("formative", 70), ("exam", 30)):
                    try:
                        maximum = max(0, min(100, int(float(config.get(
                            f"score_weight_{subject_key}_{term}_{component}", default
                        )))))
                    except (TypeError, ValueError):
                        maximum = default
                    maxima.append(maximum)
                current = conn.execute(
                    "SELECT formative, exam FROM subject_scores WHERE seat_no=? AND subject_key=? AND term=?",
                    (seat_no, subject_key, term),
                ).fetchone() or (None, None)
                values = list(current)
                for index, maximum in enumerate(maxima):
                    if values[index] is None:
                        values[index] = random.randint(0, maximum)
                        counts["คะแนนรายวิชา"] += 1
                conn.execute(
                    "INSERT OR REPLACE INTO subject_scores (seat_no, subject_key, term, formative, exam) VALUES (?,?,?,?,?)",
                    (seat_no, subject_key, term, *values),
                )

            indicators = get_real_indicators(subject_key, grade)
            for indicator_index, (code, _, _) in enumerate(indicators):
                current = conn.execute(
                    "SELECT level, checked FROM indicator_assessments WHERE seat_no=? AND subject_key=? AND indicator_code=?",
                    (seat_no, subject_key, code),
                ).fetchone()
                if current is None:
                    level = (student_index + indicator_index) % 4
                    conn.execute(
                        "INSERT INTO indicator_assessments (seat_no, subject_key, indicator_code, checked, level) VALUES (?,?,?,0,?)",
                        (seat_no, subject_key, code, level),
                    )
                    counts["ตัวชี้วัด"] += 1
                elif current[0] is None:
                    # Convert rows saved by the earlier checkbox version to the 0–3 scale.
                    conn.execute(
                        "UPDATE indicator_assessments SET level=? WHERE seat_no=? AND subject_key=? AND indicator_code=?",
                        (3 if current[1] else 0, seat_no, subject_key, code),
                    )
                    counts["ตัวชี้วัด"] += 1

        for term in (1, 2):
            trait_columns = [f"l{i}" for i in range(1, 9)]
            conn.execute(
                "INSERT OR IGNORE INTO desired_traits (seat_no, term) VALUES (?,?)",
                (seat_no, term),
            )
            existing = conn.execute(
                "SELECT " + ",".join(trait_columns) + " FROM desired_traits WHERE seat_no=? AND term=?",
                (seat_no, term),
            ).fetchone()
            generated = [value if value is not None else random.randint(0, 3) for value in existing]
            counts["คุณลักษณะ"] += sum(value is None for value in existing)
            conn.execute(
                "UPDATE desired_traits SET " + ",".join(f"{column}=?" for column in trait_columns) + " WHERE seat_no=? AND term=?",
                (*generated, seat_no, term),
            )

            conn.execute(
                "INSERT OR IGNORE INTO reading_analysis (seat_no, term) VALUES (?,?)",
                (seat_no, term),
            )
            existing = conn.execute(
                "SELECT a1,a2,a3 FROM reading_analysis WHERE seat_no=? AND term=?",
                (seat_no, term),
            ).fetchone()
            generated = [value if value is not None else random.randint(0, 3) for value in existing]
            counts["การอ่านฯ"] += sum(value is None for value in existing)
            conn.execute(
                "UPDATE reading_analysis SET a1=?,a2=?,a3=? WHERE seat_no=? AND term=?",
                (*generated, seat_no, term),
            )

            competency_columns = [f"c{i}" for i in range(1, 6)]
            conn.execute(
                "INSERT OR IGNORE INTO competency_assessments (seat_no, term) VALUES (?,?)",
                (seat_no, term),
            )
            existing = conn.execute(
                "SELECT " + ",".join(competency_columns) + " FROM competency_assessments WHERE seat_no=? AND term=?",
                (seat_no, term),
            ).fetchone()
            competency_options = ["ไม่ผ่าน", "ผ่าน", "ดี", "ดีเยี่ยม"]
            generated = [
                random.choice(competency_options) if value in (None, "", "ยังไม่บันทึก") else value
                for value in existing
            ]
            counts["สมรรถนะ"] += sum(value in (None, "", "ยังไม่บันทึก") for value in existing)
            conn.execute(
                "UPDATE competency_assessments SET " + ",".join(f"{column}=?" for column in competency_columns) + " WHERE seat_no=? AND term=?",
                (*generated, seat_no, term),
            )

            for activity_key, _ in ACTIVITY_LIST:
                existing = conn.execute(
                    "SELECT result FROM activities WHERE seat_no=? AND activity_key=? AND term=?",
                    (seat_no, activity_key, term),
                ).fetchone()
                if existing is None or existing[0] in (None, "", "ยังไม่บันทึก"):
                    conn.execute(
                        "INSERT OR REPLACE INTO activities (seat_no, activity_key, term, result) VALUES (?,?,?,?)",
                        (seat_no, activity_key, term, random.choice(["ผ", "มผ"])),
                    )
                    counts["กิจกรรม"] += 1
    return counts


with st.sidebar:
    st.markdown("### :material/menu_book: ระบบ ปพ.5")
    st.caption("ชุดข้อมูลตัวอย่างสำหรับทดลอง")
    st.space("small")
    st.markdown("**เมนูหลัก**")
    menu = st.radio(
        "เลือกส่วนที่ต้องการ",
        list(MENU),
        label_visibility="collapsed",
    )
    st.space("large")
    st.caption("ระบบบริหารข้อมูลและจัดทำเอกสารประจำชั้นเรียน")

page_title, page_icon, page_description = MENU[menu]
st.header(page_title, icon=page_icon)
st.caption(page_description)
if st.session_state.pop("display_settings_saved", False):
    st.success("บันทึกการตั้งค่าการแสดงผลแล้ว")
if st.session_state.get("display_theme_restart_required", False):
    st.warning("ขนาดและสีตัวหนังสือในตารางจะมีผลหลังปิดแล้วเปิดระบบใหม่")

if menu == "ตั้งค่าการแสดงผล":
    st.subheader("ตัวหนังสือและสี")
    st.caption("การตั้งค่าเหล่านี้มีผลกับทุกหน้าของระบบ")

    color_labels = {color: label for label, color in DISPLAY_COLOR_OPTIONS.items()}
    current_color = display_settings["text_color"]
    if current_color not in color_labels:
        current_color = DEFAULT_DISPLAY_SETTINGS["text_color"]

    with st.form("display_settings", border=True):
        text_size = st.slider(
            "ขนาดตัวหนังสือทั่วทั้งระบบ",
            min_value=16,
            max_value=26,
            value=display_settings["text_size"],
            step=1,
            format="%d px",
        )
        text_color = st.selectbox(
            "สีตัวหนังสือ",
            list(color_labels),
            index=list(color_labels).index(current_color),
            format_func=lambda color: color_labels[color],
        )
        st.subheader("ขนาดตาราง")
        table_text_size = st.slider(
            "ขนาดตัวหนังสือในตาราง",
            min_value=14,
            max_value=22,
            value=display_settings["table_text_size"],
            step=1,
            format="%d px",
        )
        table_row_height = st.slider(
            "ความสูงของแถว",
            min_value=28,
            max_value=60,
            value=display_settings["table_row_height"],
            step=2,
            format="%d px",
        )
        st.caption("ขนาดและสีตัวหนังสือในตารางต้องเปิดระบบใหม่หลังบันทึกจึงจะมีผล")
        if st.form_submit_button("บันทึกการตั้งค่า", type="primary", icon=":material/save:"):
            conn = get_connection()
            conn.executemany(
                "INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)",
                [
                    ("display_text_size", str(text_size)),
                    ("display_text_color", text_color),
                    ("display_table_text_size", str(table_text_size)),
                    ("display_table_row_height", str(table_row_height)),
                ],
            )
            conn.commit()
            conn.close()
            save_display_theme(table_text_size, text_color)
            st.session_state["display_settings_saved"] = True
            st.session_state["display_theme_restart_required"] = True
            st.rerun()

    st.subheader("ตัวอย่าง")
    st.write("ตัวอย่างข้อความตามขนาดและสีที่เลือก")
    st.dataframe(
        pd.DataFrame({"ข้อความ": ["ตัวอย่างแถวในตาราง"], "คะแนน": [55]}),
        hide_index=True,
        row_height=display_settings["table_row_height"],
        width="stretch",
    )

    st.divider()
    st.subheader("จำลองข้อมูลประเมินทั้งระบบ")
    st.caption("เติมเฉพาะช่องว่างให้ครบทุกวิชา ตัวชี้วัด คะแนน คุณลักษณะ การอ่านคิดวิเคราะห์เขียน สมรรถนะ และกิจกรรม")
    st.caption("ระบบจะเก็บคะแนนเดิมไว้ สร้างไฟล์สำรองข้างโปรแกรมก่อน และข้อมูลจำลองมีผลต่อเกรดกับเอกสาร")
    conn = get_connection()
    has_students_for_demo = conn.execute("SELECT 1 FROM students LIMIT 1").fetchone() is not None
    assessment_tables = (
        "subject_scores", "indicator_assessments", "desired_traits",
        "reading_analysis", "competency_assessments", "activities",
    )
    assessment_row_count = sum(
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in assessment_tables
    )
    conn.close()
    confirm_demo_data = st.checkbox(
        "ยืนยันให้เติมข้อมูลตัวอย่างลงในช่องที่ยังว่าง",
        key="confirm_fill_all_demo_data",
    )
    if st.button(
        "เติมข้อมูลจำลองให้ครบทั้งระบบ",
        disabled=not confirm_demo_data or not has_students_for_demo,
        type="secondary",
        icon=":material/auto_awesome:",
    ):
        conn = None
        try:
            safety_copy = save_database_backup()
            conn = get_connection()
            conn.execute("BEGIN")
            counts = fill_missing_demo_assessments(conn)
            if not any(counts.values()):
                conn.rollback()
                clear_assessment_editor_state()
                st.info("ข้อมูลประเมินมีครบอยู่แล้ว ไม่มีช่องว่างให้เติม")
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO config (key, val) VALUES ('demo_simulation_status', 'active')"
                )
                conn.commit()
                clear_assessment_editor_state()
                count_summary = " · ".join(f"{name} {count:,} ช่อง" for name, count in counts.items())
                st.success(f"เติมข้อมูลจำลองแล้ว: {count_summary}")
                st.warning(f"สำเนาก่อนเติมข้อมูล: {safety_copy.name} · ลบคะแนนจำลองหรือกู้คืนสำเนาก่อนใช้งานข้อมูลจริง")
        except Exception as error:
            if conn is not None:
                conn.rollback()
            st.error(f"เติมข้อมูลจำลองไม่สำเร็จ: {error}")
        finally:
            if conn is not None:
                conn.close()

    st.subheader("ล้างคะแนนและผลประเมินทั้งหมด")
    st.caption("ลบคะแนนรายวิชา ตัวชี้วัด คุณลักษณะ การอ่านคิดวิเคราะห์และเขียน สมรรถนะ และกิจกรรมทั้งหมด โดยไม่ใช้ข้อมูลจากไฟล์สำรอง")
    st.warning("การล้างนี้รวมข้อมูลประเมินจริงที่บันทึกไว้ด้วย รายชื่อนักเรียน เวลาเรียน และการตั้งค่าจะไม่ถูกลบ ระบบจะสร้างไฟล์สำรองไว้เผื่อกู้คืน")
    confirm_undo_demo = st.checkbox(
        "ยืนยันล้างคะแนนและผลประเมินทั้งหมด",
        key="confirm_undo_demo_data",
        disabled=assessment_row_count == 0,
    )
    if st.button(
        "ล้างคะแนนและผลประเมินทั้งหมด",
        disabled=assessment_row_count == 0 or not confirm_undo_demo,
        type="secondary",
        icon=":material/delete_sweep:",
    ):
        try:
            safety_copy, deleted_counts = clear_assessment_data()
            clear_assessment_editor_state()
            cleared_rows = sum(deleted_counts.values())
            st.success(f"ล้างคะแนนและผลประเมินแล้ว {cleared_rows:,} รายการ ข้อมูลนักเรียน เวลาเรียน และการตั้งค่ายังคงเดิม")
            if safety_copy:
                st.info(f"ไฟล์สำรองก่อนล้าง (ไม่ได้ใช้กู้คืนอัตโนมัติ): {safety_copy.name}")
            st.rerun()
        except (sqlite3.Error, OSError, ValueError) as error:
            st.error(f"ยกเลิกข้อมูลจำลองไม่สำเร็จ: {error}")

elif menu == "ตัวชี้วัด":
    conn = get_connection()
    students = get_students(conn)
    if not show_student_count(students):
        conn.close()
    else:
        settings = dict(conn.execute("SELECT key, val FROM config").fetchall())
        grade = settings.get("grade", "ชั้นประถมศึกษาปีที่ 1")
        saved_pass_level = int(settings.get("indicator_pass_level", "2"))
        if saved_pass_level not in (1, 2, 3):
            saved_pass_level = 2
        subject_names = {subject[0]: subject[1] for subject in SUBJECT_LIST}
        subject_key = st.selectbox(
            "รายวิชา", [subject[0] for subject in SUBJECT_LIST],
            format_func=lambda key: subject_names[key], key="indicator_subject",
        )
        indicators = get_real_indicators(subject_key, grade)
        if not indicators:
            st.info("ยังไม่มีข้อมูลตัวชี้วัดของรายวิชานี้")
        else:
            indicator_codes = [item[0] for item in indicators]
            indicator_editor_key = f"indicator_matrix_editor_{subject_key}"
            saved = {
                (row[0], row[1]): (
                    row[2] if row[2] is not None else (3 if row[3] else 0)
                )
                for row in conn.execute(
                    "SELECT seat_no, indicator_code, level, checked FROM indicator_assessments WHERE subject_key=?",
                    (subject_key,),
                )
            }
            indicator_rows = [
                {
                    "เลขที่": student[0],
                    "ชื่อ-นามสกุล": f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip(),
                    **{
                        code: saved.get((student[0], code))
                        for code in indicator_codes
                    },
                }
                for student_index, student in enumerate(students)
            ]
            indicator_config = {
                "เลขที่": st.column_config.NumberColumn("เลขที่", disabled=True, width="small", pinned=True),
                "ชื่อ-นามสกุล": st.column_config.TextColumn("ชื่อ-นามสกุล", disabled=True, width="medium", pinned=True),
            }
            for code, description, kind in indicators:
                indicator_config[code] = st.column_config.NumberColumn(
                    code, min_value=0, max_value=3, step=1, required=False,
                    help=f"คะแนน 0–3 · {kind} · {description}", width="small",
                )
            st.caption("ให้คะแนนแต่ละตัวชี้วัด 0–3 · เว้นว่างไว้เมื่อต้องการระบุว่ายังไม่ได้ประเมิน · เลื่อนแนวนอนเพื่อดูข้อถัดไป")
            edited = st.data_editor(
                pd.DataFrame(indicator_rows),
                column_config=indicator_config,
                width="stretch", hide_index=True, num_rows="fixed",
                key=indicator_editor_key,
                row_height=display_settings["table_row_height"],
            )
            pass_level = st.selectbox(
                "เกณฑ์ผ่านตัวชี้วัด",
                [1, 2, 3],
                index=[1, 2, 3].index(saved_pass_level),
                format_func=lambda value: f"คะแนน {value} ขึ้นไป = ผ่าน",
                key=f"indicator_pass_level_{subject_key}",
                help="ปรับตามเกณฑ์การวัดและประเมินผลที่โรงเรียนกำหนด",
            )
            indicator_summary = []
            for _, row in edited.iterrows():
                values = [row[code] for code in indicator_codes]
                assessed = [int(value) for value in values if not pd.isna(value)]
                passed_count = sum(value >= pass_level for value in assessed)
                failed_count = sum(value < pass_level for value in assessed)
                indicator_summary.append({
                    "เลขที่": int(row["เลขที่"]),
                    "ชื่อ-นามสกุล": row["ชื่อ-นามสกุล"],
                    "ประเมินแล้ว": len(assessed),
                    "ผ่าน": passed_count,
                    "ไม่ผ่าน": failed_count,
                    "ยังไม่ประเมิน": len(indicator_codes) - len(assessed),
                })
            st.subheader("สรุปผลตัวชี้วัดรายคน")
            st.caption(f"นับคะแนนตั้งแต่ {pass_level} ขึ้นไปเป็นผ่าน · คะแนนต่ำกว่าเกณฑ์เป็นไม่ผ่าน · ช่องว่างยังไม่ประเมิน")
            st.dataframe(
                pd.DataFrame(indicator_summary),
                column_config={
                    "เลขที่": st.column_config.NumberColumn("เลขที่", width="small", pinned=True),
                    "ประเมินแล้ว": st.column_config.NumberColumn("ประเมินแล้ว", width="small"),
                    "ผ่าน": st.column_config.NumberColumn("ผ่าน", width="small"),
                    "ไม่ผ่าน": st.column_config.NumberColumn("ไม่ผ่าน", width="small"),
                    "ยังไม่ประเมิน": st.column_config.NumberColumn("ยังไม่ประเมิน", width="small"),
                },
                width="stretch", hide_index=True,
            )
            if save_button("บันทึกผลตัวชี้วัด", key="save_indicator_assessment"):
                conn.executemany(
                    "INSERT OR REPLACE INTO indicator_assessments (seat_no, subject_key, indicator_code, checked, level) VALUES (?,?,?,?,?)",
                    (
                        (
                            int(row["เลขที่"]), subject_key, code,
                            0,
                            None if pd.isna(row[code]) else int(row[code]),
                        )
                        for _, row in edited.iterrows()
                        for code in indicator_codes
                    ),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO config (key, val) VALUES ('indicator_pass_level', ?)",
                    (str(pass_level),),
                )
                conn.commit()
                st.success(f"บันทึกผลตัวชี้วัด {len(indicator_codes)} ข้อและเกณฑ์ผ่านแล้ว")
        conn.close()

elif menu == "ข้อมูลสถานศึกษา":
    conn = get_connection()
    config = dict(conn.execute("SELECT key, val FROM config").fetchall())

    st.subheader("ข้อมูลโรงเรียนและชั้นเรียน")
    st.caption("ข้อมูลชุดนี้จะถูกนำไปใช้ประกอบการจัดทำเอกสาร ปพ.5 และ ปพ.6")
    with st.form("school_config", border=True):
        left, right = st.columns(2)
        with left:
            school_name = st.text_input("ชื่อสถานศึกษา", config.get("school_name", ""))
            address = st.text_input("สังกัด", config.get("address", ""))
            grade_options = list(GRADE_MAPPING)
            saved_grade = config.get("grade", grade_options[0])
            grade_index = grade_options.index(saved_grade) if saved_grade in grade_options else 0
            grade = st.selectbox("ระดับชั้น", grade_options, index=grade_index)
        with right:
            year = st.text_input("ปีการศึกษา", config.get("year", ""), disabled=True)
            st.caption("เปลี่ยนปีจากตัวเลือก ‘ปีการศึกษา’ ที่แถบด้านซ้าย ระบบจะแยกข้อมูลแต่ละปีให้")
            teacher = st.text_input("ครูประจำชั้น", config.get("teacher_1", ""))
            advisor = st.text_input("ครูที่ปรึกษา", config.get("advisor", config.get("teacher_1", "")))
            academic_head = st.text_input("หัวหน้าวิชาการ", config.get("academic_head", ""))
            director = st.text_input("ผู้อำนวยการ", config.get("director", ""))
        if st.form_submit_button("บันทึกข้อมูลสถานศึกษา", type="primary", icon=":material/save:"):
            values = {
                "school_name": school_name,
                "address": address,
                "grade": grade,
                "year": year,
                "teacher_1": teacher,
                "advisor": advisor,
                "academic_head": academic_head,
                "director": director,
            }
            conn.executemany("INSERT OR REPLACE INTO config VALUES (?, ?)", values.items())
            conn.commit()
            st.success("บันทึกข้อมูลสถานศึกษาแล้ว")

    st.subheader("นำเข้ารายชื่อนักเรียน")
    st.caption("เลือกระดับชั้นที่ต้องการ แล้วนำเข้ารายชื่อที่เตรียมไว้ในระบบ")
    import_col, action_col = st.columns([3, 1], vertical_alignment="bottom")
    with import_col:
        import_grade = st.selectbox("ระดับชั้นสำหรับนำเข้ารายชื่อ", list(GRADE_MAPPING), key="import_grade")
    with action_col:
        if st.button("โหลดรายชื่อ", type="secondary", icon=":material/download:", width="stretch"):
            success, message = import_grade_data(import_grade)
            if success:
                st.success(message)
            else:
                st.error(message)
    conn.close()

    st.subheader("ย้ายนักเรียนขึ้นชั้นในปีการศึกษาถัดไป")
    st.caption(
        "คัดลอกรายชื่อนักเรียนของชั้นที่เลือกไปยังปีถัดไป พร้อมเลื่อนระดับชั้น "
        "และย้ายข้อมูลประวัตินักเรียนที่ตรงกับรายชื่อ ระบบไม่คัดลอกคะแนนหรือผลประเมิน"
    )
    migration_years = list_available_academic_years()
    preview_state_key = "academic_year_migration_preview"
    preview_error_key = "academic_year_migration_preview_error"
    active_year = get_active_academic_year()
    default_source_year = (
        active_year - 1
        if active_year - 1 in migration_years
        else active_year
    )
    st.caption(
        "ตัวอย่าง: ถ้าจะเลื่อน ป.1 ปี 2569 เป็น ป.2 ปี 2570 "
        "ให้เลือกปีต้นทาง 2569"
    )
    with st.form("academic_year_migration_preview_form", border=True):
        source_year = st.selectbox(
            "ปีการศึกษาต้นทาง",
            migration_years,
            index=(
                migration_years.index(default_source_year)
                if default_source_year in migration_years
                else len(migration_years) - 1
            ),
            format_func=lambda value: f"พ.ศ. {value}",
            key="academic_year_migration_source_year_v2",
        )
        source_grade_options = migration_source_grades(
            get_database_path(source_year)
        )
        if not source_grade_options:
            source_grade_options = list(GRADE_MAPPING)
        default_grade_index = 0
        source_grade = st.selectbox(
            "ชั้นต้นทางที่จะเลื่อน",
            source_grade_options,
            index=default_grade_index,
            key="academic_year_migration_source_grade_v1",
        )
        target_year = source_year + 1
        st.caption(f"ปีการศึกษาปลายทาง: พ.ศ. {target_year}")
        preview_requested = st.form_submit_button(
            "ตรวจสอบรายชื่อก่อนย้าย",
            icon=":material/preview:",
        )
    if preview_requested:
        try:
            migration_preview = preview_year_migration(
                get_database_path(source_year),
                get_database_path(target_year),
                source_year,
                target_year,
                source_grade,
            )
            st.session_state[preview_state_key] = (
                source_year,
                target_year,
                source_grade,
                migration_preview,
            )
            st.session_state.pop(preview_error_key, None)
        except (OSError, sqlite3.Error, ValueError) as error:
            st.session_state.pop(preview_state_key, None)
            st.session_state[preview_error_key] = str(error)

    if error := st.session_state.pop(preview_error_key, None):
        st.error(f"ตรวจสอบข้อมูลเพื่อย้ายไม่สำเร็จ: {error}")

    saved_preview = st.session_state.get(preview_state_key)
    if saved_preview and saved_preview[:3] == (
        source_year,
        target_year,
        source_grade,
    ):
        migration_preview = saved_preview[3]
        st.write(
            f"**{migration_preview['source_grade']} → "
            f"{migration_preview['target_grade'] or 'ไม่มีชั้นถัดไป'}** · "
            f"นักเรียน {migration_preview['student_count']} คน · "
            f"ข้อมูลประวัติที่พบ {migration_preview['profile_count']} รายการ"
        )
        if migration_preview["students"]:
            st.dataframe(
                [
                    {
                        "เลขที่": row[0],
                        "รหัสนักเรียน": row[1] or "",
                        "ชื่อ-นามสกุล": f"{row[2] or ''}{row[3] or ''} {row[4] or ''}".strip(),
                    }
                    for row in migration_preview["students"]
                ],
                hide_index=True,
            )
        if migration_preview["graduating_grade"]:
            st.warning("นักเรียน ป.6 จบการศึกษาแล้ว จึงไม่มีชั้นถัดไปให้เลื่อน")
        if migration_preview["target_block_reason"]:
            st.error(
                "ไม่สามารถย้ายไปปีปลายทางได้: "
                + migration_preview["target_block_reason"]
            )
        if migration_preview["profile_conflicts"]:
            st.error(
                "พบรหัสนักเรียนซ้ำกับข้อมูลประวัติในชั้นปลายทาง "
                f"{migration_preview['profile_conflicts']} รายการ"
            )
        if migration_preview["can_migrate"]:
            if migration_preview["target_has_data"]:
                st.warning(
                    "ปีปลายทางมีข้อมูลเดิม การดำเนินการนี้จะสร้างสำเนาฐานข้อมูลเดิม "
                    "แล้วแทนที่รายชื่อนักเรียนและล้างคะแนน/เวลาเรียน/ผลประเมินในปีปลายทาง "
                    "ตารางเรียนและข้อมูลตั้งค่าโรงเรียนจะคงไว้"
                )
            else:
                st.warning(
                    "ตรวจสอบรายชื่อและระดับชั้นให้ถูกต้องก่อนยืนยัน "
                    "ข้อมูลในปีต้นทางจะไม่ถูกลบ"
                )
            confirm_migration = st.checkbox(
                f"ยืนยันย้ายนักเรียน {migration_preview['student_count']} คน "
                f"จาก {migration_preview['source_grade']} ไป "
                f"{migration_preview['target_grade']} ปีการศึกษา {target_year}"
                + (
                    " และยืนยันแทนที่ข้อมูลนักเรียน/ผลประเมินปลายทาง"
                    if migration_preview["target_has_data"]
                    else ""
                ),
                key=(
                    f"academic_year_migration_confirm_{source_year}_"
                    f"{source_grade}_{target_year}"
                ),
            )
            if st.button(
                "ยืนยันย้ายข้อมูล",
                disabled=not confirm_migration,
                type="primary",
                icon=":material/arrow_forward:",
                key=(
                    f"academic_year_migration_submit_{source_year}_"
                    f"{source_grade}_{target_year}"
                ),
            ):
                try:
                    result = migrate_students_to_next_year(
                        get_database_path(source_year),
                        get_database_path(target_year),
                        source_year,
                        target_year,
                        source_grade,
                        replace_target_data=migration_preview["target_has_data"],
                    )
                    notice = (
                        f"ย้ายนักเรียน {result['student_count']} คนจาก "
                        f"{result['source_grade']} ไป {result['target_grade']} "
                        f"ปีการศึกษา {target_year} เรียบร้อยแล้ว"
                    )
                    if result["safety_copy"]:
                        notice += f" · สำเนาข้อมูลปลายทางเดิม: {result['safety_copy'].name}"
                    st.session_state["academic_year_migration_notice"] = notice
                    st.session_state.pop(preview_state_key, None)
                    st.rerun()
                except (OSError, sqlite3.Error, ValueError) as error:
                    st.error(f"ย้ายข้อมูลไม่สำเร็จ: {error}")
    if notice := st.session_state.pop("academic_year_migration_notice", None):
        st.success(notice)

elif menu == "สำรองข้อมูลระบบ":
    st.subheader("บันทึกหรือดาวน์โหลดไฟล์สำรอง")
    st.caption(
        "ระบบสำรองฐานข้อมูลอัตโนมัติวันละครั้งและเก็บย้อนหลัง 30 วัน "
        "ไฟล์สำรองรวมข้อมูลโรงเรียน รายชื่อนักเรียน คะแนน เวลาเรียน และผลประเมินทั้งหมด"
    )
    backup_bytes = create_database_backup()
    if st.button(
        "บันทึกไฟล์สำรองไว้ข้างโปรแกรม",
        type="primary",
        icon=":material/save:",
    ):
        try:
            saved_backup = save_database_backup()
            st.success(f"บันทึกไฟล์สำรองแล้ว: {saved_backup}")
        except OSError as error:
            st.error(f"บันทึกไฟล์สำรองไม่สำเร็จ: {error}")
    st.download_button(
        "ดาวน์โหลดไฟล์สำรองข้อมูล",
        data=backup_bytes,
        file_name=f"สำรองระบบปพ5_{datetime.now().strftime('%Y%m%d_%H%M')}.db",
        mime="application/vnd.sqlite3",
        type="primary",
        icon=":material/download:",
    )

    st.subheader("กู้คืนจากไฟล์สำรอง")
    st.warning("การกู้คืนจะแทนที่ข้อมูลปัจจุบันด้วยข้อมูลในไฟล์ที่เลือก ระบบจะเก็บไฟล์ปัจจุบันเป็นสำเนาก่อนกู้คืน")
    uploaded_backup = st.file_uploader("เลือกไฟล์สำรองระบบ ปพ.5 (.db)", type=["db"])
    confirm_restore = st.checkbox("ยืนยันการแทนที่ข้อมูลปัจจุบัน", disabled=uploaded_backup is None)
    if st.button(
        "กู้คืนข้อมูลจากไฟล์",
        disabled=uploaded_backup is None or not confirm_restore,
        type="secondary",
        icon=":material/settings_backup_restore:",
    ):
        try:
            safety_copy = restore_database_backup(uploaded_backup.getvalue())
            message = "กู้คืนข้อมูลเรียบร้อยแล้ว"
            if safety_copy:
                message += f" สำเนาข้อมูลก่อนกู้คืน: {safety_copy.name}"
            st.success(message)
            st.info("กรุณาโหลดหน้าเว็บใหม่เพื่ออ่านข้อมูลชุดที่กู้คืน")
        except (sqlite3.Error, OSError, ValueError) as error:
            st.error(f"กู้คืนไม่สำเร็จ: {error}")

elif menu == "ประวัติการแก้ไข":
    st.subheader("ประวัติการแก้ไขข้อมูล")
    st.caption(
        "บันทึกเหตุการณ์เพิ่ม แก้ไข และลบข้อมูล พร้อมค่าเดิม/ค่าใหม่ "
        "ระบบนี้ยังไม่มีบัญชีผู้ใช้รายบุคคล จึงระบุผู้ทำรายการเป็น “ผู้ใช้ระบบ”"
    )
    st.warning(
        "ประวัติอาจมีข้อมูลนักเรียนและข้อมูลส่วนบุคคล ควรจำกัดผู้เข้าถึง "
        "และสำรองฐานข้อมูลอย่างปลอดภัย"
    )
    filter_col, action_col, limit_col = st.columns([2, 1, 1])
    with filter_col:
        selected_table = st.selectbox(
            "ส่วนข้อมูล",
            ["ทั้งหมด", *AUDITED_TABLES],
            format_func=lambda value: (
                "ทุกส่วน" if value == "ทั้งหมด" else TABLE_LABELS[value]
            ),
            key="audit_table_filter",
        )
    with action_col:
        selected_action = st.selectbox(
            "การเปลี่ยนแปลง",
            ["ทั้งหมด", *ACTION_LABELS],
            format_func=lambda value: (
                "ทุกประเภท" if value == "ทั้งหมด" else ACTION_LABELS[value]
            ),
            key="audit_action_filter",
        )
    with limit_col:
        event_limit = st.selectbox(
            "จำนวนรายการ",
            [100, 250, 500, 1000, 2000],
            index=2,
            key="audit_event_limit",
        )

    audit_connection = get_connection()
    try:
        audit_events = get_audit_events(
            audit_connection,
            table_name=None if selected_table == "ทั้งหมด" else selected_table,
            action=None if selected_action == "ทั้งหมด" else selected_action,
            limit=event_limit,
        )
    finally:
        audit_connection.close()

    st.caption(f"แสดงรายการล่าสุด {len(audit_events)} รายการตามตัวกรอง")
    if not audit_events:
        st.info("ยังไม่มีประวัติการเปลี่ยนแปลงที่ตรงกับตัวกรอง")
    else:
        st.dataframe(
            [
                {
                    "วัน-เวลา": event["occurred_at"],
                    "ปีการศึกษา": event["academic_year"],
                    "ผู้ทำรายการ": event["actor"],
                    "ส่วนข้อมูล": TABLE_LABELS[event["table_name"]],
                    "การเปลี่ยนแปลง": ACTION_LABELS[event["action"]],
                    "รายการ": event["record_key"],
                }
                for event in audit_events
            ],
            hide_index=True,
            width="stretch",
        )
        for event in audit_events:
            label = (
                f"{event['occurred_at']} · "
                f"{TABLE_LABELS[event['table_name']]} · "
                f"{ACTION_LABELS[event['action']]} · {event['record_key']}"
            )
            with st.expander(label):
                before_col, after_col = st.columns(2)
                with before_col:
                    st.markdown("**ค่าเดิม**")
                    st.json(event["before"] or {})
                with after_col:
                    st.markdown("**ค่าใหม่**")
                    st.json(event["after"] or {})

elif menu == "ประวัตินักเรียน ป.1–ป.6":
    conn = get_connection()
    grades = [f"ป.{grade}" for grade in range(1, 7)]
    grade = st.selectbox("ระดับชั้น", grades, key="profile_grade_filter")
    profile_rows = conn.execute(
        "SELECT seat_no, student_id, title, first_name, last_name, guardian_title, "
        "guardian_first_name, guardian_last_name, guardian_relationship, house_no, "
        "village_no, road, subdistrict, district, province "
        "FROM student_profiles WHERE grade=? ORDER BY room, seat_no, student_id",
        (grade,),
    ).fetchall()
    st.metric("นักเรียนในระดับชั้น", f"{len(profile_rows):,} คน", border=True)
    if not profile_rows:
        st.info("ยังไม่มีข้อมูลระดับชั้นนี้ในประวัตินักเรียน")
    else:
        summary = pd.DataFrame(
            [
                {
                    "เลขที่": row[0],
                    "รหัสนักเรียน": row[1],
                    "ชื่อ-นามสกุล": f"{row[2]}{row[3]} {row[4]}".strip(),
                    "ผู้ปกครอง": f"{row[5]}{row[6]} {row[7]}".strip(),
                    "เกี่ยวข้องเป็น": row[8],
                    "ที่อยู่": " ".join(str(value) for value in row[9:15] if value),
                }
                for row in profile_rows
            ]
        )
        st.dataframe(
            summary, hide_index=True, row_height=display_settings["table_row_height"], width="stretch"
        )

        options = {f"{row[0]}. {row[2]}{row[3]} {row[4]} · รหัส {row[1]}": row[1] for row in profile_rows}
        selected_label = st.selectbox("เลือกนักเรียนเพื่อดูหรือแก้ไขข้อมูล", list(options), key="profile_student")
        student_id = options[selected_label]
        profile = conn.execute(
            "SELECT * FROM student_profiles WHERE grade=? AND student_id=?",
            (grade, student_id),
        ).fetchone()
        columns = [item[1] for item in conn.execute("PRAGMA table_info(student_profiles)")]
        data = dict(zip(columns, profile))

        st.subheader("ข้อมูลผู้ปกครองและที่อยู่")
        with st.form(f"student_profile_form_{grade}_{student_id}", border=True):
            field_groups = [
                [
                    ("guardian_title", "คำนำหน้าผู้ปกครอง"),
                    ("guardian_first_name", "ชื่อผู้ปกครอง"),
                    ("guardian_last_name", "นามสกุลผู้ปกครอง"),
                    ("guardian_relationship", "เกี่ยวข้องเป็น"),
                    ("guardian_occupation", "อาชีพผู้ปกครอง"),
                ],
                [
                    ("house_no", "บ้านเลขที่"),
                    ("village_no", "หมู่"),
                    ("road", "ถนน/ซอย"),
                    ("subdistrict", "ตำบล"),
                    ("district", "อำเภอ"),
                    ("province", "จังหวัด"),
                ],
                [
                    ("father_title", "คำนำหน้าบิดา"),
                    ("father_first_name", "ชื่อบิดา"),
                    ("father_last_name", "นามสกุลบิดา"),
                    ("father_occupation", "อาชีพบิดา"),
                    ("mother_title", "คำนำหน้ามารดา"),
                    ("mother_first_name", "ชื่อมารดา"),
                    ("mother_last_name", "นามสกุลมารดา"),
                    ("mother_occupation", "อาชีพมารดา"),
                ],
            ]
            form_cols = st.columns(3)
            values = {}
            for col, group in zip(form_cols, field_groups):
                with col:
                    for field, label in group:
                        values[field] = st.text_input(label, value=data.get(field, "") or "", key=f"{grade}_{student_id}_{field}")
            if st.form_submit_button("บันทึกข้อมูลประวัตินักเรียน", type="primary", icon=":material/save:"):
                assignments = ", ".join(f"{field}=?" for field in values)
                conn.execute(
                    f"UPDATE student_profiles SET {assignments} WHERE grade=? AND student_id=?",
                    (*values.values(), grade, student_id),
                )
                conn.commit()
                st.success("บันทึกข้อมูลผู้ปกครองและที่อยู่แล้ว")
    conn.close()

elif menu == "ทะเบียนนักเรียน":
    conn = get_connection()
    students = get_students(conn)
    show_student_count(students)
    st.subheader("รายชื่อนักเรียน")
    st.caption("แก้ไขข้อมูลในตารางได้โดยตรง เพิ่มแถวใหม่ด้านล่าง แล้วกดบันทึก")
    df = pd.read_sql_query(
        "SELECT seat_no AS 'เลขที่', student_id AS 'รหัสนักเรียน', "
        "title AS 'คำนำหน้า', first_name AS 'ชื่อ', last_name AS 'นามสกุล' "
        "FROM students ORDER BY seat_no ASC",
        conn,
    )
    editor_config = {
        "เลขที่": st.column_config.NumberColumn("เลขที่", min_value=1, step=1, required=True),
        "รหัสนักเรียน": st.column_config.TextColumn("รหัสนักเรียน"),
        "คำนำหน้า": st.column_config.SelectboxColumn(
            "คำนำหน้า", options=["เด็กชาย", "เด็กหญิง", "นาย", "นางสาว"], required=True
        ),
        "ชื่อ": st.column_config.TextColumn("ชื่อ", required=True),
        "นามสกุล": st.column_config.TextColumn("นามสกุล", required=True),
    }
    edited = st.data_editor(
        df,
        column_config=editor_config,
        num_rows="dynamic",
        row_height=display_settings["table_row_height"],
        width="stretch",
        hide_index=True,
        key="student_roster_editor",
    )
    if save_button(key="save_students"):
        try:
            conn.execute("DELETE FROM students")
            for _, row in edited.iterrows():
                if pd.isna(row["เลขที่"]) or pd.isna(row["ชื่อ"]) or pd.isna(row["นามสกุล"]):
                    continue
                conn.execute(
                    "INSERT INTO students (seat_no, student_id, title, first_name, last_name) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        int(row["เลขที่"]),
                        "" if pd.isna(row["รหัสนักเรียน"]) else str(row["รหัสนักเรียน"]),
                        "" if pd.isna(row["คำนำหน้า"]) else str(row["คำนำหน้า"]),
                        str(row["ชื่อ"]),
                        str(row["นามสกุล"]),
                    ),
                )
            conn.commit()
            st.success("บันทึกทะเบียนนักเรียนแล้ว")
        except Exception as exc:
            conn.rollback()
            st.error(f"บันทึกไม่สำเร็จ: {exc}")
    conn.close()

elif menu == "สถิติคะแนน":
    conn = get_connection()
    students = get_students(conn)
    config = dict(conn.execute("SELECT key, val FROM config").fetchall())
    saved_scores = {
        (seat_no, subject_key, term): (formative, exam)
        for seat_no, subject_key, term, formative, exam in conn.execute(
            "SELECT seat_no, subject_key, term, formative, exam FROM subject_scores"
        ).fetchall()
    }
    conn.close()

    if not show_student_count(students):
        st.stop()

    subject_names = {subject_key: subject_name for subject_key, subject_name, _ in SUBJECT_LIST}
    with st.container(border=True):
        st.markdown("**ตัวกรองรายงาน**")
        filter_col, period_col = st.columns(2)
        with filter_col:
            selected_subject = st.selectbox(
                "รายวิชา",
                [None, *subject_names],
                format_func=lambda key: "ทุกวิชา" if key is None else subject_names[key],
                key="score_statistics_subject",
            )
        with period_col:
            selected_period = st.selectbox(
                "ช่วงคะแนน",
                ["ทั้งปี", "ภาคเรียนที่ 1", "ภาคเรียนที่ 2"],
                key="score_statistics_period",
            )

    term1_share = percentage_or_default(config.get("grade_term1_weight", "50"))
    term_scores = {}
    for subject_key, _, _ in SUBJECT_LIST:
        for student in students:
            scores_by_term = {}
            for term in (1, 2):
                formative, exam = saved_scores.get((student[0], subject_key, term), (None, None))
                scores_by_term[term] = (
                    round(float(formative) + float(exam), 1)
                    if formative is not None and exam is not None
                    else None
                )
            if selected_period == "ภาคเรียนที่ 1":
                value = scores_by_term[1]
            elif selected_period == "ภาคเรียนที่ 2":
                value = scores_by_term[2]
            else:
                value = (
                    round(scores_by_term[1] * term1_share / 100 + scores_by_term[2] * (100 - term1_share) / 100, 1)
                    if scores_by_term[1] is not None and scores_by_term[2] is not None
                    else None
                )
            term_scores[(student[0], subject_key)] = value

    visible_subjects = [selected_subject] if selected_subject else list(subject_names)
    scoped_scores = [
        (student, subject_key, term_scores[(student[0], subject_key)])
        for student in students
        for subject_key in visible_subjects
    ]
    assessed_scores = [score for _, _, score in scoped_scores if score is not None]
    passed_scores = [score for score in assessed_scores if score >= 50]
    score_series = pd.Series(assessed_scores, dtype="float64")
    average_score = float(score_series.mean()) if assessed_scores else None
    median_score = float(score_series.median()) if assessed_scores else None
    standard_deviation = float(score_series.std(ddof=1)) if len(assessed_scores) > 1 else None
    completion_rate = len(assessed_scores) / len(scoped_scores) * 100 if scoped_scores else 0
    pass_rate = len(passed_scores) / len(assessed_scores) * 100 if assessed_scores else 0

    metric_columns = st.columns(4)
    metric_columns[0].metric("ค่าเฉลี่ย", f"{average_score:.2f}" if average_score is not None else "—", border=True)
    metric_columns[1].metric("มัธยฐาน", f"{median_score:.2f}" if median_score is not None else "—", border=True)
    metric_columns[2].metric("S.D. (ตัวอย่าง)", f"{standard_deviation:.2f}" if standard_deviation is not None else "—", border=True)
    metric_columns[3].metric("ผ่านเกณฑ์ 50 คะแนน", f"{pass_rate:.1f}%" if assessed_scores else "—", border=True)
    subject_context = "ทุกวิชา" if selected_subject is None else subject_names[selected_subject]
    st.caption(
        f"{subject_context} · {selected_period} · n={len(assessed_scores):,} · "
        f"ข้อมูลครบ {completion_rate:.1f}% · ผ่านเกณฑ์ {len(passed_scores):,} จาก {len(assessed_scores):,} รายการ"
    )

    subject_stats = []
    for subject_key in visible_subjects:
        scores = [term_scores[(student[0], subject_key)] for student in students]
        complete = [score for score in scores if score is not None]
        series = pd.Series(complete, dtype="float64")
        passes = sum(score >= 50 for score in complete)
        subject_stats.append({
            "รายวิชา": subject_names[subject_key],
            "n": len(complete),
            "ข้อมูลขาด": len(scores) - len(complete),
            "ข้อมูลครบ (%)": round(len(complete) / len(scores) * 100, 1) if scores else 0,
            "เฉลี่ย": round(float(series.mean()), 2) if complete else None,
            "S.D.": round(float(series.std(ddof=1)), 2) if len(complete) > 1 else None,
            "ความแปรปรวน": round(float(series.var(ddof=1)), 2) if len(complete) > 1 else None,
            "มัธยฐาน": round(float(series.median()), 2) if complete else None,
            "Q1": round(float(series.quantile(.25)), 2) if complete else None,
            "Q3": round(float(series.quantile(.75)), 2) if complete else None,
            "IQR": round(float(series.quantile(.75) - series.quantile(.25)), 2) if complete else None,
            "ต่ำสุด": round(float(series.min()), 2) if complete else None,
            "สูงสุด": round(float(series.max()), 2) if complete else None,
            "ผ่าน (n)": passes,
            "ผ่าน (%)": round(passes / len(complete) * 100, 1) if complete else None,
        })
    stats_frame = pd.DataFrame(subject_stats)

    research_rows = []
    student_codes = {student[0]: f"P{index:03d}" for index, student in enumerate(students, 1)}
    for student, subject_key, selected_score in scoped_scores:
        seat_no = student[0]
        term1 = saved_scores.get((seat_no, subject_key, 1), (None, None))
        term2 = saved_scores.get((seat_no, subject_key, 2), (None, None))
        grade_value = calc_grade(selected_score) if selected_score is not None else None
        research_rows.append({
            "รหัสผู้เรียน (แทนชื่อ)": student_codes[seat_no],
            "รหัสวิชา": subject_key,
            "รายวิชา": subject_names[subject_key],
            "ช่วงคะแนน": selected_period,
            "ภาค 1 คะแนนเก็บ": term1[0],
            "ภาค 1 คะแนนสอบ": term1[1],
            "ภาค 1 รวม": term_scores[(seat_no, subject_key)] if selected_period == "ภาคเรียนที่ 1" else (
                round(float(term1[0]) + float(term1[1]), 1) if None not in term1 else None
            ),
            "ภาค 2 คะแนนเก็บ": term2[0],
            "ภาค 2 คะแนนสอบ": term2[1],
            "ภาค 2 รวม": term_scores[(seat_no, subject_key)] if selected_period == "ภาคเรียนที่ 2" else (
                round(float(term2[0]) + float(term2[1]), 1) if None not in term2 else None
            ),
            "น้ำหนักภาค 1 (%)": term1_share,
            "คะแนนสุทธิ": selected_score,
            "เกรด": grade_value,
            "ผ่านเกณฑ์ (50 คะแนน)": (selected_score >= 50) if selected_score is not None else None,
            "บันทึกครบ": selected_score is not None,
        })
    research_frame = pd.DataFrame(research_rows)
    csv_data = research_frame.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

    if selected_subject is None:
        chart_data = stats_frame.loc[stats_frame["เฉลี่ย"].notna(), ["รายวิชา", "เฉลี่ย"]]
        with st.container(border=True):
            st.subheader("คะแนนเฉลี่ยแยกรายวิชา", icon=":material/bar_chart:")
            st.caption("คะแนนเต็ม 100 · เรียงตามลำดับรายวิชา")
            if chart_data.empty:
                st.info("ยังไม่มีคะแนนที่บันทึกครบในช่วงนี้", icon=":material/info:")
            else:
                st.bar_chart(
                    chart_data,
                    x="รายวิชา",
                    y="เฉลี่ย",
                    color="#176B5B",
                    horizontal=True,
                    sort=False,
                    height=420,
                )
        with st.container(border=True):
            st.subheader("สถิติรายวิชา", icon=":material/table_chart:")
            st.dataframe(
                stats_frame,
                column_config={
                    **{
                        column: st.column_config.NumberColumn(column, format="%.2f")
                        for column in ("เฉลี่ย", "S.D.", "ความแปรปรวน", "มัธยฐาน", "Q1", "Q3", "IQR", "ต่ำสุด", "สูงสุด")
                    },
                    "ข้อมูลครบ (%)": st.column_config.NumberColumn("ข้อมูลครบ (%)", format="%.1f%%"),
                    "ผ่าน (%)": st.column_config.ProgressColumn(
                        "ผ่าน (%)", min_value=0, max_value=100, format="%.1f%%", color="#176B5B"
                    ),
                },
                hide_index=True,
            )
        score_plot_data = research_frame.dropna(subset=["คะแนนสุทธิ"])
        if not score_plot_data.empty:
            box_plot = (
                alt.Chart(score_plot_data)
                .mark_boxplot(extent="min-max", size=20, color="#176B5B")
                .encode(
                    x=alt.X("คะแนนสุทธิ:Q", title="คะแนนสุทธิ (0–100)", scale=alt.Scale(domain=[0, 100])),
                    y=alt.Y("รายวิชา:N", title=None, sort=[subject_names[key] for key in visible_subjects]),
                    tooltip=[alt.Tooltip("รายวิชา:N", title="รายวิชา")],
                )
                .properties(height=max(280, 34 * len(visible_subjects)))
            )
            with st.container(border=True):
                st.subheader("การกระจายคะแนนรายวิชา", icon=":material/monitoring:")
                st.caption("กล่องแสดงช่วงควอไทล์ที่ 1–3 เส้นกลางคือมัธยฐาน และหนวดแสดงค่าต่ำสุด–สูงสุด")
                st.altair_chart(box_plot, width="stretch")
    else:
        selected_scores = [term_scores[(student[0], selected_subject)] for student in students]
        grade_order = ("4", "3.5", "3", "2.5", "2", "1.5", "1", "0")
        grade_counts = dict.fromkeys(grade_order, 0)
        for score in selected_scores:
            if score is not None:
                grade_counts[calc_grade(score)] += 1
        chart_col, table_col = st.columns(2, vertical_alignment="top")
        with chart_col:
            with st.container(border=True, height="stretch"):
                st.subheader("ผลการเรียนตามเกรด", icon=":material/grade:")
                st.caption("จำนวนผู้เรียนที่มีคะแนนครบ แบ่งตามเกณฑ์เกรดของระบบ")
                if assessed_scores:
                    grade_frame = pd.DataFrame({"เกรด": list(grade_order), "จำนวนนักเรียน": list(grade_counts.values())})
                    st.bar_chart(grade_frame, x="เกรด", y="จำนวนนักเรียน", color="#23846F", height=320)
                else:
                    st.info("ยังไม่มีคะแนนที่บันทึกครบ", icon=":material/info:")
        with table_col:
            with st.container(border=True, height="stretch"):
                st.subheader("การกระจายคะแนน", icon=":material/stacked_bar_chart:")
                st.caption("ฮิสโตแกรมคะแนนสุทธิ · ช่วงละ 10 คะแนน")
                score_bands = tuple(
                    (f"{low}–{min(low + 9, 100)}", low, min(low + 10, 101))
                    for low in range(0, 100, 10)
                ) + (("100", 100, 101),)
                band_rows = [
                    {"ช่วงคะแนน": label, "จำนวนนักเรียน": sum(low <= score < high for score in selected_scores if score is not None)}
                    for label, low, high in score_bands
                ]
                if assessed_scores:
                    st.bar_chart(pd.DataFrame(band_rows), x="ช่วงคะแนน", y="จำนวนนักเรียน", color="#55A995", height=320, sort=False)
                else:
                    st.info("กราฟจะแสดงเมื่อมีคะแนนที่บันทึกครบ", icon=":material/info:")

        st.subheader("สถิติเชิงพรรณนา")
        st.dataframe(
            stats_frame,
            column_config={
                **{
                    column: st.column_config.NumberColumn(column, format="%.2f")
                    for column in ("เฉลี่ย", "S.D.", "ความแปรปรวน", "มัธยฐาน", "Q1", "Q3", "IQR", "ต่ำสุด", "สูงสุด")
                },
                "ข้อมูลครบ (%)": st.column_config.NumberColumn("ข้อมูลครบ (%)", format="%.1f%%"),
                "ผ่าน (%)": st.column_config.ProgressColumn(
                    "ผ่าน (%)", min_value=0, max_value=100, format="%.1f%%", color="#176B5B"
                ),
            },
            hide_index=True,
        )

    with st.expander("ระเบียบการคำนวณและการใช้ข้อมูล", icon=":material/science:"):
        st.markdown(
            """
- คะแนนเฉลี่ยและส่วนเบี่ยงเบนมาตรฐานคำนวณจากรายการที่มีคะแนนครบเท่านั้น
- S.D. และความแปรปรวนใช้สูตรตัวอย่าง (`ddof=1`); Q1/Q3 ใช้ควอนไทล์เชิงเส้น และ IQR = Q3 − Q1
- ผลผ่านเกณฑ์นับคะแนนสุทธิตั้งแต่ 50 คะแนนขึ้นไป ตามเกณฑ์เกรดของระบบ
- คะแนนทั้งปีถ่วงน้ำหนักตามสัดส่วนภาคเรียนที่ตั้งไว้ และต้องมีคะแนนครบทั้งสองภาคเรียน
- CSV มีหนึ่งแถวต่อผู้เรียนต่อรายวิชา พร้อมคะแนนรายภาคและผลคำนวณ โดยไม่ส่งออกชื่อหรือเลขประจำตัวจริง
            """
        )
    with st.expander("ดูข้อมูลระดับผู้เรียน (ใช้รหัสแทนชื่อ)", icon=":material/table_view:"):
        st.dataframe(research_frame, hide_index=True)
    st.download_button(
        "ดาวน์โหลดข้อมูลวิจัย CSV",
        data=csv_data,
        file_name=f"ข้อมูลวิจัยคะแนน_{selected_period}_{selected_subject or 'ทุกวิชา'}.csv",
        mime="text/csv",
        icon=":material/download:",
        key="download_score_research_data",
    )

    st.caption("ก่อนนำผลไปอ้างอิงงานวิจัย ควรระบุปีการศึกษา กลุ่มตัวอย่าง เกณฑ์การให้คะแนน และวิธีจัดการข้อมูลที่ขาดไว้ในระเบียบวิธีวิจัย")

elif menu == "บันทึกคะแนน":
    conn = get_connection()
    students = get_students(conn)
    has_students = bool(students)
    saved_message = st.session_state.pop("score_saved_message", None)
    if saved_message:
        st.success(saved_message)
    if has_students:
        metric_col, intro_col = st.columns([1, 3], vertical_alignment="center")
        with metric_col:
            st.metric("นักเรียนในทะเบียน", f"{len(students):,} คน", border=True)
        with intro_col:
            school = dict(conn.execute("SELECT key, val FROM config").fetchall())
            st.caption("บันทึกคะแนนเก็บและคะแนนสอบ แยกตามรายวิชา")
            st.caption(
                " · ".join(
                    value for value in (
                        school.get("school_name", ""),
                        school.get("grade", ""),
                        f"ปีการศึกษา {school['year']}" if school.get("year") else "",
                    ) if value
                )
            )
    else:
        show_student_count(students)
    if has_students:
        st.subheader("คะแนนรายวิชา")
        control, note = st.columns([2, 3], vertical_alignment="bottom")
        subject_names = {subject[0]: subject[1] for subject in SUBJECT_LIST}
        with control:
            subject_key = st.selectbox(
                "รายวิชา",
                [subject[0] for subject in SUBJECT_LIST],
                format_func=lambda key: subject_names[key],
            )
        with note:
            st.caption("กรอกคะแนนทั้งสองภาคเรียนตามสัดส่วนที่สถานศึกษากำหนด ช่องว่างหมายถึงยังไม่ประเมิน")
        saved_weights = dict(conn.execute("SELECT key, val FROM config").fetchall())
        year_term_weight = int(percentage_or_default(saved_weights.get("grade_term1_weight", "50")))
        term1_weight, term2_weight = st.columns(2)
        with term1_weight:
            term1_share = st.number_input("สัดส่วนคะแนนภาคเรียนที่ 1 (%)", min_value=0, max_value=100, value=year_term_weight, step=5, format="%.0f")
        with term2_weight:
            st.number_input("สัดส่วนคะแนนภาคเรียนที่ 2 (%)", min_value=0, max_value=100, value=100 - year_term_weight, step=5, format="%.0f", disabled=True)
        weights = {}
        weight_cols = st.columns(4)
        for term, component, label, col_index, default in (
            (1, "formative", "ภาค 1 · คะแนนเก็บเต็ม", 0, 70),
            (1, "exam", "ภาค 1 · คะแนนสอบเต็ม", 1, 30),
            (2, "formative", "ภาค 2 · คะแนนเก็บเต็ม", 2, 70),
            (2, "exam", "ภาค 2 · คะแนนสอบเต็ม", 3, 30),
        ):
            weight_key = f"score_weight_{subject_key}_{term}_{component}"
            stored = saved_weights.get(weight_key, str(default))
            with weight_cols[col_index]:
                weights[(term, component)] = st.number_input(
                    label,
                    min_value=0,
                    max_value=100,
                    step=1,
                    value=int(float(stored)),
                    key=weight_key,
                )
        rows = []
        for student in students:
            score_1 = conn.execute(
                "SELECT formative, exam FROM subject_scores WHERE seat_no=? AND subject_key=? AND term=1",
                (student[0], subject_key),
            ).fetchone() or (None, None)
            score_2 = conn.execute(
                "SELECT formative, exam FROM subject_scores WHERE seat_no=? AND subject_key=? AND term=2",
                (student[0], subject_key),
            ).fetchone() or (None, None)
            rows.append(
                {
                    "เลขที่": student[0],
                    "ชื่อ-นามสกุล": f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip(),
                    "ภาค 1 · เก็บ": score_1[0],
                    "ภาค 1 · สอบ": score_1[1],
                    "ภาค 2 · เก็บ": score_2[0],
                    "ภาค 2 · สอบ": score_2[1],
                }
            )

        score_editor_key = f"score_editor_v2_{subject_key}"
        score_config = {
            "เลขที่": st.column_config.NumberColumn("เลขที่", disabled=True, width=64, pinned=True),
            "ชื่อ-นามสกุล": st.column_config.TextColumn("ชื่อ-นามสกุล", disabled=True, width=300, pinned=True),
            "ภาค 1 · เก็บ": st.column_config.NumberColumn("ภาค 1 · เก็บ", min_value=0, step=1, width=150, alignment="center", required=False, help=f"คะแนนเต็ม {weights[(1, 'formative')]} คะแนน"),
            "ภาค 1 · สอบ": st.column_config.NumberColumn("ภาค 1 · สอบ", min_value=0, step=1, width=150, alignment="center", required=False, help=f"คะแนนเต็ม {weights[(1, 'exam')]} คะแนน"),
            "ภาค 2 · เก็บ": st.column_config.NumberColumn("ภาค 2 · เก็บ", min_value=0, step=1, width=150, alignment="center", required=False, help=f"คะแนนเต็ม {weights[(2, 'formative')]} คะแนน"),
            "ภาค 2 · สอบ": st.column_config.NumberColumn("ภาค 2 · สอบ", min_value=0, step=1, width=150, alignment="center", required=False, help=f"คะแนนเต็ม {weights[(2, 'exam')]} คะแนน"),
        }
        editor_col, results_col = st.columns([2, 1], vertical_alignment="top")
        with editor_col:
            st.caption("คะแนนที่กรอก")
            edited = st.data_editor(
                pd.DataFrame(rows), column_config=score_config, num_rows="fixed",
                width="stretch", height="content", hide_index=True, key=score_editor_key,
                row_height=display_settings["table_row_height"],
            )
        summary = edited[["เลขที่", "ชื่อ-นามสกุล"]].copy()
        summary["รวมภาค 1"] = edited["ภาค 1 · เก็บ"].astype(float) + edited["ภาค 1 · สอบ"].astype(float)
        summary["รวมภาค 2"] = edited["ภาค 2 · เก็บ"].astype(float) + edited["ภาค 2 · สอบ"].astype(float)
        summary["คะแนนสุทธิ"] = ((summary["รวมภาค 1"] * term1_share + summary["รวมภาค 2"] * (100 - term1_share)) / 100).round(1)
        complete_scores = edited[["ภาค 1 · เก็บ", "ภาค 1 · สอบ", "ภาค 2 · เก็บ", "ภาค 2 · สอบ"]].notna().all(axis=1)
        summary["เกรด"] = summary["คะแนนสุทธิ"].map(calc_grade).where(complete_scores, "รอประเมิน")
        with results_col:
            st.caption("รวมคะแนนและเกรด · คำนวณทันที")
            st.dataframe(
                summary[["เลขที่", "รวมภาค 1", "รวมภาค 2", "คะแนนสุทธิ", "เกรด"]],
                column_config={
                    "เลขที่": st.column_config.NumberColumn("เลขที่", width=64, pinned=True),
                    "รวมภาค 1": st.column_config.NumberColumn("รวมภาค 1", width=95, format="localized", alignment="center"),
                    "รวมภาค 2": st.column_config.NumberColumn("รวมภาค 2", width=95, format="localized", alignment="center"),
                    "คะแนนสุทธิ": st.column_config.NumberColumn("คะแนนสุทธิ", width=95, format="%.1f", alignment="center"),
                    "เกรด": st.column_config.TextColumn("เกรด", width=70, alignment="center"),
                },
                width="stretch",
                height="content",
                hide_index=True,
                row_height=display_settings["table_row_height"],
            )
        if save_button(key="save_scores"):
            if weights[(1, "formative")] + weights[(1, "exam")] != 100 or weights[(2, "formative")] + weights[(2, "exam")] != 100 or term1_share + (100 - term1_share) != 100:
                st.error("คะแนนเต็มของแต่ละภาคเรียนและสัดส่วนคะแนนรายปีต้องรวมกันได้ 100 คะแนน")
            else:
                invalid_scores = []
                for _, row in edited.iterrows():
                    for term, formative_col, exam_col in (
                        (1, "ภาค 1 · เก็บ", "ภาค 1 · สอบ"),
                        (2, "ภาค 2 · เก็บ", "ภาค 2 · สอบ"),
                    ):
                        if pd.notna(row[formative_col]) and float(row[formative_col]) > weights[(term, "formative")]:
                            invalid_scores.append(f"เลขที่ {int(row['เลขที่'])} ภาค {term} คะแนนเก็บ")
                        if pd.notna(row[exam_col]) and float(row[exam_col]) > weights[(term, "exam")]:
                            invalid_scores.append(f"เลขที่ {int(row['เลขที่'])} ภาค {term} คะแนนสอบ")
                if invalid_scores:
                    st.error("คะแนนเกินคะแนนเต็มที่กำหนด: " + ", ".join(invalid_scores[:6]))
                else:
                    for _, row in edited.iterrows():
                        conn.execute(
                            "INSERT OR REPLACE INTO subject_scores VALUES (?,?,1,?,?)",
                            (row["เลขที่"], subject_key, None if pd.isna(row["ภาค 1 · เก็บ"]) else row["ภาค 1 · เก็บ"], None if pd.isna(row["ภาค 1 · สอบ"]) else row["ภาค 1 · สอบ"]),
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO subject_scores VALUES (?,?,2,?,?)",
                            (row["เลขที่"], subject_key, None if pd.isna(row["ภาค 2 · เก็บ"]) else row["ภาค 2 · เก็บ"], None if pd.isna(row["ภาค 2 · สอบ"]) else row["ภาค 2 · สอบ"]),
                        )
                    for (term, component), value in weights.items():
                        conn.execute(
                            "INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)",
                            (f"score_weight_{subject_key}_{term}_{component}", str(value)),
                        )
                    conn.execute(
                        "INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)",
                        ("grade_term1_weight", str(term1_share)),
                    )
                    conn.commit()
                    st.session_state["score_saved_message"] = f"บันทึกคะแนนและสัดส่วนวิชา{subject_names[subject_key]}แล้ว"
                    st.rerun()
    conn.close()

elif menu == "เวลาเรียน":
    conn = get_connection()
    students = get_students(conn)
    attendance_settings = dict(conn.execute("SELECT key, val FROM config").fetchall())
    try:
        academic_year = int(attendance_settings.get("year", "2569"))
    except (TypeError, ValueError):
        academic_year = 2569
    has_students = show_student_count(students)
    if has_students:
        term_col, month_col, help_col = st.columns([1, 1, 2], vertical_alignment="bottom")
        with term_col:
            term = st.selectbox("ภาคเรียน", [1, 2], format_func=lambda value: f"ภาคเรียนที่ {value}", key="attendance_term")
        months = [(5, "พฤษภาคม"), (6, "มิถุนายน"), (7, "กรกฎาคม"), (8, "สิงหาคม"), (9, "กันยายน"), (10, "ตุลาคม")] if term == 1 else [(11, "พฤศจิกายน"), (12, "ธันวาคม"), (1, "มกราคม"), (2, "กุมภาพันธ์"), (3, "มีนาคม")]
        with month_col:
            month = st.selectbox("เดือน", [item[0] for item in months], format_func=lambda value: dict(months)[value], key="attendance_month")
        with help_col:
            st.caption("สถานะ: ม มา · ป ป่วย · ล ลา · ข ขาด · หยุด วันหยุด")
        year = academic_year - 543 if month >= 5 else academic_year - 542
        day_count = calendar.monthrange(year, month)[1]
        thai_days = ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"]
        holidays_for_year = PUBLIC_HOLIDAYS_2569 if academic_year == 2569 else {}
        holiday_days = {
            day: holidays_for_year[(year, month, day)]
            for day in range(1, day_count + 1)
            if (year, month, day) in holidays_for_year
        }
        day_labels = {
            day: f"{'🔴 ' if day in holiday_days else ''}{day} {thai_days[calendar.weekday(year, month, day)]}"
            for day in range(1, day_count + 1)
        }
        auto_stop_days = [
            day for day in range(1, day_count + 1)
            if calendar.weekday(year, month, day) >= 5
            or day in holiday_days
            or not is_in_term(term, year, month, day, academic_year)
        ]
        # เติมวันหยุดเป็นข้อมูลจริงด้วย เพื่อให้สรุปเวลาเรียนและเอกสารที่ส่งออกตรงกัน
        # ถ้ามีสถานะที่ครูบันทึกไว้แล้วจะไม่เขียนทับ; ช่องว่างเดิมจะถูกเติมเป็น "หยุด"
        stop_rows = [
            (student[0], term, month, day, "หยุด")
            for student in students for day in auto_stop_days
        ]
        if stop_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO attendance_daily VALUES (?,?,?,?,?)", stop_rows
            )
            conn.executemany(
                "UPDATE attendance_daily SET status='หยุด' "
                "WHERE seat_no=? AND term=? AND month_no=? AND day=? AND status=''",
                [(seat_no, term, month, day) for seat_no, term, month, day, _ in stop_rows],
            )
            closed_term_days = [
                day for day in range(1, day_count + 1)
                if not is_in_term(term, year, month, day, academic_year)
            ]
            if closed_term_days:
                conn.executemany(
                    "UPDATE attendance_daily SET status='หยุด' WHERE term=? AND month_no=? AND day=?",
                    [(term, month, day) for day in closed_term_days],
                )
            conn.commit()
        saved = {
            (row[0], row[1]): ("" if row[2] in (None, "None") else row[2])
            for row in conn.execute(
                "SELECT seat_no, day, status FROM attendance_daily WHERE term=? AND month_no=?",
                (term, month),
            ).fetchall()
        }
        rows = []
        for student in students:
            row = {"เลขที่": student[0], "ชื่อ-นามสกุล": f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip()}
            for day in range(1, day_count + 1):
                day_name = day_labels[day]
                default = "หยุด" if day in auto_stop_days else ""
                status = saved.get((student[0], day), default)
                row[day_name] = "🔴 หยุด" if status == "หยุด" else status
            rows.append(row)
        first_gregorian_year = academic_year - 543
        school_calendar = [
            (1, month, first_gregorian_year) for month in range(5, 11)
        ] + [
            (2, month, first_gregorian_year if month in (11, 12) else first_gregorian_year + 1)
            for month in (11, 12, 1, 2, 3)
        ]
        attendance_by_day = {}
        for saved_term, saved_month, seat_no, saved_day, status in conn.execute(
            "SELECT term, month_no, seat_no, day, status FROM attendance_daily"
        ).fetchall():
            attendance_by_day.setdefault((saved_term, saved_month, saved_day), {})[seat_no] = status or ""
        valid_attendance = {"ม", "ป", "ล", "ข"}
        class_days = []
        for calendar_term, calendar_month, calendar_year in school_calendar:
            for calendar_day in range(1, calendar.monthrange(calendar_year, calendar_month)[1] + 1):
                if not is_in_term(calendar_term, calendar_year, calendar_month, calendar_day, academic_year):
                    continue
                date_key = (calendar_term, calendar_month, calendar_day)
                day_statuses = attendance_by_day.get(date_key, {})
                regular_weekday = (
                    calendar.weekday(calendar_year, calendar_month, calendar_day) < 5
                    and (calendar_year, calendar_month, calendar_day) not in holidays_for_year
                )
                all_stopped = all(day_statuses.get(student[0], "") == "หยุด" for student in students)
                all_recorded = all(day_statuses.get(student[0], "") in valid_attendance for student in students)
                if (regular_weekday and not all_stopped) or (not regular_weekday and all_recorded):
                    class_days.append((calendar_term, calendar_month, calendar_day, calendar_year))

        # Use the school's 100-day-per-term attendance basis.
        class_days = [
            school_day
            for calendar_term in (1, 2)
            for school_day in [day for day in class_days if day[0] == calendar_term][:100]
        ]

        complete_school_days = sum(
            1 for calendar_term, calendar_month, calendar_day, _ in class_days
            if all(
                attendance_by_day.get((calendar_term, calendar_month, calendar_day), {}).get(student[0], "") in valid_attendance
                for student in students
            )
        )
        school_days_total = len(class_days)
        days_short_of_target = max(0, 200 - school_days_total)
        metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
        metric_col1.metric("วันเรียนตามปฏิทินจริง", f"{school_days_total} / 200 วัน")
        metric_col2.metric("ขาดจากเป้า 200 วัน", f"{days_short_of_target} วัน")
        metric_col3.metric("วันที่ลงข้อมูลครบทั้งห้อง", f"{complete_school_days} / {school_days_total} วัน")
        metric_col4.metric("วันที่ต้องกรอกสถานะเพิ่ม", f"{max(0, school_days_total - complete_school_days)} วัน")
        st.caption(f"ปีการศึกษา {academic_year} · เป้าหมายระดับประถมศึกษา 200 วัน · ภาค 1: 16 พ.ค.–10 ต.ค. · ภาค 2: 1 พ.ย.–31 มี.ค. · วันหยุดเฉพาะโรงเรียนให้ระบุ ‘หยุด’ ในตาราง")
        st.caption("นับจากสถานะที่บันทึกอยู่ หากเคยใช้ปุ่มเติมวันมาเรียนก่อนหน้านี้ ให้ตรวจแก้วันที่ไม่ตรงกับการมาเรียนจริงในตาราง")
        holiday_summary = " · ".join(
            f"{day} {dict(months)[month]}: {name}" for day, name in holiday_days.items()
        )
        if holiday_summary:
            st.caption(f"เติมวันหยุดราชการให้อัตโนมัติแล้ว · {holiday_summary}")
        elif academic_year != 2569:
            st.caption("ปีนี้ยังไม่มีรายการวันหยุดราชการที่ตั้งไว้ ระบบทำเครื่องหมายเสาร์–อาทิตย์และวันที่นอกภาคเรียนเป็น ‘หยุด’ อัตโนมัติ")
        else:
            st.caption("ระบบเติมวันหยุดราชการและวันเสาร์–อาทิตย์เป็น ‘หยุด’ อัตโนมัติ โดยไม่นับเป็นวันเรียน")
        st.caption("กรอกสถานะตามวันที่เรียนจริงได้ในตารางด้านล่าง วันเสาร์/อาทิตย์ที่มีการเรียนชดเชยจะนับเมื่อบันทึกสถานะครบทั้งห้อง")
        attendance_config = {
            "เลขที่": st.column_config.NumberColumn("เลขที่", disabled=True, width="small", pinned=True),
            "ชื่อ-นามสกุล": st.column_config.TextColumn("ชื่อ-นามสกุล", disabled=True, width="medium", pinned=True),
        }
        for day in range(1, day_count + 1):
            day_name = day_labels[day]
            attendance_config[day_name] = st.column_config.SelectboxColumn(
                day_name, options=["", "ม", "ป", "ล", "ข", "🔴 หยุด"], width="small",
                help=holiday_days.get(day, "วันเสาร์–อาทิตย์" if calendar.weekday(year, month, day) >= 5 else None),
            )
        attendance_editor_key = f"attendance_editor_{term}_{month}"
        edited = st.data_editor(
            pd.DataFrame(rows), column_config=attendance_config, width="stretch",
            hide_index=True, key=attendance_editor_key,
            row_height=display_settings["table_row_height"],
        )
        if save_button(key="save_attendance"):
            for _, row in edited.iterrows():
                for day in range(1, day_count + 1):
                    day_name = day_labels[day]
                    status = row[day_name]
                    status = "" if pd.isna(status) or status == "None" else str(status)
                    if status == "🔴 หยุด":
                        status = "หยุด"
                    conn.execute(
                        "INSERT OR REPLACE INTO attendance_daily VALUES (?,?,?,?,?)",
                        (row["เลขที่"], term, month, day, status),
                    )
            conn.commit()
            st.success(f"บันทึกเวลาเรียนเดือน{dict(months)[month]}แล้ว")
        st.subheader("สรุปเวลาเรียนรายบุคคล")
        st.caption("นับเฉพาะวันที่มีสถานะ มา/ป่วย/ลา/ขาด · เกณฑ์ผ่านเวลาเรียนไม่น้อยกว่า 80%")
        attendance_by_day = {}
        for saved_term, saved_month, seat_no, saved_day, status in conn.execute(
            "SELECT term, month_no, seat_no, day, status FROM attendance_daily"
        ).fetchall():
            attendance_by_day.setdefault((saved_term, saved_month, saved_day), {})[seat_no] = status or ""
        attendance_summary = []
        individual_stats = {
            student[0]: {
                "term1_present": 0, "term2_present": 0,
                "มา": 0, "ป่วย": 0, "ลา": 0, "ขาด": 0,
            }
            for student in students
        }
        summary_statuses = {"ม": "มา", "ป": "ป่วย", "ล": "ลา", "ข": "ขาด"}
        class_days_by_term = {1: 0, 2: 0}
        for calendar_term, _, _, _ in class_days:
            class_days_by_term[calendar_term] += 1
        for calendar_term, calendar_month, calendar_day, _ in class_days:
            day_statuses = attendance_by_day.get((calendar_term, calendar_month, calendar_day), {})
            for student in students:
                status = day_statuses.get(student[0], "")
                if status not in summary_statuses:
                    continue
                stats = individual_stats[student[0]]
                stats[f"{summary_statuses[status]}"] += 1
                if status == "ม":
                    stats[f"term{calendar_term}_present"] += 1
        for student in students:
            stats = individual_stats[student[0]]
            term1_open = class_days_by_term[1]
            term2_open = class_days_by_term[2]
            days_open = term1_open + term2_open
            days_present = stats["มา"]
            days_recorded = days_present + stats["ป่วย"] + stats["ลา"] + stats["ขาด"]
            rate = days_present / days_open * 100 if days_open else 0
            attendance_summary.append({
                "เลขที่": student[0],
                "ชื่อ-นามสกุล": f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip(),
                "เปิดเรียน ภาค 1": term1_open,
                "มา ภาค 1": stats["term1_present"],
                "เปิดเรียน ภาค 2": term2_open,
                "มา ภาค 2": stats["term2_present"],
                "วันเปิดเรียน / 200": days_open,
                "มา": days_present,
                "ป่วย": stats["ป่วย"],
                "ลา": stats["ลา"],
                "ขาด": stats["ขาด"],
                "มาเรียน (%)": round(rate, 1),
                "ยังไม่ลง": max(0, days_open - days_recorded),
                "ผลประเมิน": "ลงข้อมูลไม่ครบ" if days_recorded < days_open else ("ยังไม่มีข้อมูล" if not days_open else ("ผ่าน" if rate >= 80 else "ต่ำกว่า 80%")),
            })
        st.dataframe(
            pd.DataFrame(attendance_summary),
            column_config={
                "วันเปิดเรียน / 200": st.column_config.ProgressColumn(
                    "วันเปิดเรียน / 200", min_value=0, max_value=200, format="%d วัน"
                ),
                "มาเรียน (%)": st.column_config.NumberColumn("มาเรียน (%)", format="%.1f%%"),
            },
            width="stretch", hide_index=True,
            row_height=display_settings["table_row_height"],
        )
    conn.close()

elif menu == "กิจกรรมพัฒนาผู้เรียน":
    conn = get_connection()
    students = get_students(conn)
    has_students = show_student_count(students)
    if has_students:
        term = st.selectbox("ภาคเรียน", [1, 2], format_func=lambda value: f"ภาคเรียนที่ {value}", key="activity_term")
        st.caption("เลือกผลการเข้าร่วมกิจกรรมของนักเรียนแต่ละคน")
        rows = []
        for student in students:
            row = {"เลขที่": student[0], "ชื่อ-นามสกุล": f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip()}
            for activity_key, activity_name in ACTIVITY_LIST:
                result = conn.execute(
                    "SELECT result FROM activities WHERE seat_no=? AND activity_key=? AND term=?",
                    (student[0], activity_key, term),
                ).fetchone()
                row[activity_name] = result[0] if result and result[0] else "ยังไม่บันทึก"
            rows.append(row)
        activity_config = {
            "เลขที่": st.column_config.NumberColumn("เลขที่", disabled=True, width="small"),
            "ชื่อ-นามสกุล": st.column_config.TextColumn("ชื่อ-นามสกุล", disabled=True, width="medium"),
        }
        for _, activity_name in ACTIVITY_LIST:
            activity_config[activity_name] = st.column_config.SelectboxColumn(
                activity_name, options=["ยังไม่บันทึก", "ผ", "มผ"], help="ผ = ผ่าน, มผ = ไม่ผ่าน"
            )
        activity_editor_key = f"activity_editor_{term}"
        edited = st.data_editor(
            pd.DataFrame(rows), column_config=activity_config, width="stretch",
            hide_index=True, key=activity_editor_key,
            row_height=display_settings["table_row_height"],
        )
        if save_button(key="save_activities"):
            for _, row in edited.iterrows():
                for activity_key, activity_name in ACTIVITY_LIST:
                    conn.execute(
                        "INSERT OR REPLACE INTO activities VALUES (?,?,?,?)",
                        (row["เลขที่"], activity_key, term, row[activity_name]),
                    )
            conn.commit()
            st.success("บันทึกผลกิจกรรมแล้ว")
    conn.close()

elif menu == "คุณลักษณะ":
    conn = get_connection()
    students = get_students(conn)
    has_students = show_student_count(students)
    if has_students:
        term = st.selectbox("ภาคเรียน", [1, 2], format_func=lambda value: f"ภาคเรียนที่ {value}", key="trait_term")
        st.caption("ประเมินคุณลักษณะทั้ง 8 ข้อเป็นระดับ 0–3 ระบบสรุปผลเมื่อกรอกครบทุกข้อ")
        trait_names = TRAIT_NAMES
        score_rows = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT seat_no, l1, l2, l3, l4, l5, l6, l7, l8 FROM desired_traits WHERE term=?",
                (term,),
            ).fetchall()
        }
        rows = []
        for student in students:
            levels = score_rows.get(student[0], (None,) * 8)
            row = {"เลขที่": student[0], "ชื่อ-นามสกุล": f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip()}
            row.update({name: LEVEL_TO_LABEL.get(level, "ยังไม่ประเมิน") for name, level in zip(trait_names, levels)})
            rows.append(row)
        trait_config = {
            "เลขที่": st.column_config.NumberColumn("เลขที่", disabled=True, width="small"),
            "ชื่อ-นามสกุล": st.column_config.TextColumn("ชื่อ-นามสกุล", disabled=True, width="medium"),
        }
        for name in trait_names:
            trait_config[name] = st.column_config.SelectboxColumn(
                name, options=LEVEL_OPTION_LABELS, width="medium",
            )
        trait_editor_key = f"trait_editor_level_{term}"
        edited = st.data_editor(
            pd.DataFrame(rows), column_config=trait_config, width="stretch",
            hide_index=True, key=trait_editor_key,
            row_height=display_settings["table_row_height"],
        )
        trait_summary = []
        for _, row in edited.iterrows():
            levels = [DISPLAY_TO_LEVEL.get(row[name]) for name in trait_names]
            result = overall_level(levels)
            trait_summary.append({"เลขที่": row["เลขที่"], "ชื่อ-นามสกุล": row["ชื่อ-นามสกุล"], "ผลสรุป": LEVEL_LABELS.get(result, "ยังไม่ประเมิน")})
        st.caption("ผลสรุปคุณลักษณะอันพึงประสงค์")
        st.dataframe(pd.DataFrame(trait_summary), width="stretch", hide_index=True)
        if save_button(key="save_traits"):
            for _, row in edited.iterrows():
                values = [DISPLAY_TO_LEVEL.get(row[name]) for name in trait_names]
                conn.execute(
                    "INSERT OR REPLACE INTO desired_traits (seat_no, term, l1, l2, l3, l4, l5, l6, l7, l8) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row["เลขที่"], term, *values),
                )
            conn.commit()
            st.success("บันทึกคะแนนคุณลักษณะแล้ว")
    conn.close()

elif menu == "การอ่าน คิดวิเคราะห์ และเขียน":
    conn = get_connection()
    students = get_students(conn)
    if show_student_count(students):
        term = st.selectbox("ภาคเรียน", [1, 2], format_func=lambda value: f"ภาคเรียนที่ {value}", key="reading_term")
        st.caption("ประเมิน 3 ด้านเป็นระดับ 0–3 ระบบสรุปผลเมื่อกรอกครบทุกด้าน")
        saved_rows = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT seat_no, a1, a2, a3 FROM reading_analysis WHERE term=?",
                (term,),
            ).fetchall()
        }
        criteria = ("การอ่าน", "การคิดวิเคราะห์", "การเขียน")
        rows = []
        for student in students:
            levels = saved_rows.get(student[0], (None,) * 3)
            row = {"เลขที่": student[0], "ชื่อ-นามสกุล": f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip()}
            row.update({name: LEVEL_TO_LABEL.get(level, "ยังไม่ประเมิน") for name, level in zip(criteria, levels)})
            rows.append(row)
        reading_config = {
            "เลขที่": st.column_config.NumberColumn("เลขที่", disabled=True, width="small"),
            "ชื่อ-นามสกุล": st.column_config.TextColumn("ชื่อ-นามสกุล", disabled=True, width="medium"),
        }
        for criterion in criteria:
            reading_config[criterion] = st.column_config.SelectboxColumn(
                criterion, options=LEVEL_OPTION_LABELS,
            )
        edited = st.data_editor(
            pd.DataFrame(rows), column_config=reading_config, width="stretch",
            hide_index=True, key=f"reading_editor_level_{term}",
            row_height=display_settings["table_row_height"],
        )
        reading_summary = []
        for _, row in edited.iterrows():
            levels = [DISPLAY_TO_LEVEL.get(row[name]) for name in criteria]
            result = overall_level(levels)
            reading_summary.append({"เลขที่": row["เลขที่"], "ชื่อ-นามสกุล": row["ชื่อ-นามสกุล"], "ผลสรุป": LEVEL_LABELS.get(result, "ยังไม่ประเมิน")})
        st.caption("ผลสรุปการอ่าน คิดวิเคราะห์ และเขียน")
        st.dataframe(pd.DataFrame(reading_summary), width="stretch", hide_index=True)
        if save_button(key="save_reading"):
            for _, row in edited.iterrows():
                levels = [DISPLAY_TO_LEVEL.get(row[name]) for name in criteria]
                conn.execute(
                    "INSERT OR REPLACE INTO reading_analysis (seat_no, term, a1, a2, a3) VALUES (?, ?, ?, ?, ?)",
                    (row["เลขที่"], term, *levels),
                )
            conn.commit()
            st.success("บันทึกผลการอ่าน คิดวิเคราะห์ และเขียนแล้ว")
    conn.close()

elif menu == "สมรรถนะ":
    conn = get_connection()
    students = get_students(conn)
    has_students = show_student_count(students)
    if has_students:
        term = st.selectbox("ภาคเรียน", [1, 2], format_func=lambda value: f"ภาคเรียนที่ {value}", key="competency_term")
        st.caption("เลือกผลประเมินสมรรถนะของนักเรียนแต่ละด้าน โดยเว้นเป็น ‘ยังไม่บันทึก’ ได้")
        saved_rows = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT seat_no, c1, c2, c3, c4, c5 FROM competency_assessments WHERE term=?",
                (term,),
            ).fetchall()
        }
        rows = []
        for student in students:
            row = {
                "เลขที่": student[0],
                "ชื่อ-นามสกุล": f"{student[2] or ''}{student[3] or ''} {student[4] or ''}".strip(),
            }
            values = saved_rows.get(student[0], ("ยังไม่บันทึก",) * len(COMPETENCY_NAMES))
            row.update(dict(zip(COMPETENCY_NAMES, values)))
            rows.append(row)
        competency_config = {
            "เลขที่": st.column_config.NumberColumn("เลขที่", disabled=True, width="small"),
            "ชื่อ-นามสกุล": st.column_config.TextColumn("ชื่อ-นามสกุล", disabled=True, width="medium"),
        }
        for name in COMPETENCY_NAMES:
            competency_config[name] = st.column_config.SelectboxColumn(
                name, options=["ยังไม่บันทึก", "ไม่ผ่าน", "ผ่าน", "ดี", "ดีเยี่ยม"], width="medium"
            )
        edited = st.data_editor(
            pd.DataFrame(rows), column_config=competency_config, width="stretch",
            hide_index=True, key=f"competency_editor_{term}",
            row_height=display_settings["table_row_height"],
        )
        competency_to_level = {"ไม่ผ่าน": 0, "ผ่าน": 1, "ดี": 2, "ดีเยี่ยม": 3}
        competency_summary = []
        for _, row in edited.iterrows():
            levels = [competency_to_level.get(row[name]) for name in COMPETENCY_NAMES]
            overall = overall_level(levels)
            competency_summary.append({"เลขที่": row["เลขที่"], "ชื่อ-นามสกุล": row["ชื่อ-นามสกุล"], "ผลสรุป": LEVEL_LABELS.get(overall, "ยังไม่ประเมิน")})
        st.caption("ผลสรุปสมรรถนะสำคัญของผู้เรียน")
        st.dataframe(pd.DataFrame(competency_summary), width="stretch", hide_index=True)
        if save_button(key="save_competencies"):
            for _, row in edited.iterrows():
                values = [row[name] for name in COMPETENCY_NAMES]
                conn.execute(
                    "INSERT OR REPLACE INTO competency_assessments "
                    "(seat_no, term, c1, c2, c3, c4, c5) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (row["เลขที่"], term, *values),
                )
            conn.commit()
            st.success("บันทึกผลสมรรถนะแล้ว")
    conn.close()

elif menu == "ส่งออกเอกสาร":
    st.info("เอกสารฉบับเว็บแยกจากหน้าจัดการแล้ว เลือกชนิดเอกสารและนักเรียนได้จากหน้าเอกสาร")
    st.page_link("app_pages/print_reports.py", label="เปิดหน้าเอกสารเว็บสำหรับพิมพ์", icon=":material/open_in_new:")

elif menu == "ปพ.6 รายบุคคล":
    conn = get_connection()
    students = conn.execute(
        "SELECT seat_no, title, first_name, last_name FROM students ORDER BY seat_no"
    ).fetchall()
    conn.close()

    if not students:
        st.info("ยังไม่มีรายชื่อนักเรียน กรุณาเพิ่มรายชื่อก่อนสร้างรายงาน")
    else:
        seat_no = st.selectbox(
            "เลือกนักเรียน",
            [student[0] for student in students],
            format_func=lambda number: next(
                f"{row[0]} · {row[1] or ''}{row[2] or ''} {row[3] or ''}".strip()
                for row in students if row[0] == number
            ),
            key="manage_pp6_student",
        )
        term = st.selectbox(
            "ภาคเรียนสำหรับ PDF",
            [1, 2],
            format_func=lambda value: f"ภาคเรียนที่ {value}",
            key="manage_pp6_term",
        )

        conn = get_connection()
        try:
            html_completeness = report_completeness_messages(conn, [seat_no])
            pdf_completeness = report_completeness_messages(
                conn, [seat_no], terms=[term]
            )
        finally:
            conn.close()

        with st.container(border=True):
            st.subheader("รายงาน ปพ.6 · HTML")
            st.caption("รายงานฉบับเต็มตลอดปีการศึกษา เปิดไฟล์ที่ดาวน์โหลดเพื่อพิมพ์หรือบันทึกเป็น PDF")
            if html_completeness:
                st.warning("โปรดตรวจสอบข้อมูลที่อาจยังไม่ครบ:")
                for message in html_completeness:
                    st.write(f"- {message}")
            else:
                st.success("ข้อมูลพื้นฐานและคะแนนสำหรับรายงานนี้ครบตามรายการที่ตรวจสอบ")

            html_key = ("manage_pp6_html", seat_no)
            if st.button(
                "สร้างรายงาน ปพ.6 HTML",
                type="primary",
                icon=":material/language:",
                key="manage_pp6_create_html",
            ):
                with st.spinner("กำลังจัดหน้าเอกสารเว็บ…"):
                    st.session_state["manage_pp6_html"] = build_individual_report(seat_no)
                    st.session_state["manage_pp6_html_key"] = html_key

            if st.session_state.get("manage_pp6_html_key") == html_key:
                report_html = st.session_state["manage_pp6_html"]
                st.download_button(
                    "บันทึกรายงาน HTML",
                    data=report_html.encode("utf-8"),
                    file_name=f"ปพ6_เลขที่_{seat_no}_หน้าเว็บ.html",
                    mime="text/html",
                    icon=":material/download:",
                    key="manage_pp6_download_html",
                )
                st.caption("ตัวอย่างรายงาน")
                st.iframe(
                    report_html.replace(
                        '<button type="button" onclick="window.print()">พิมพ์ / บันทึกเป็น PDF</button>',
                        "<span>ดาวน์โหลดไฟล์ HTML แล้วเปิดเพื่อพิมพ์</span>",
                    ),
                    height="content",
                )

        with st.container(border=True):
            st.subheader("รายงาน ปพ.6 · PDF")
            if pdf_completeness:
                st.warning("โปรดตรวจสอบข้อมูลที่อาจยังไม่ครบ:")
                for message in pdf_completeness:
                    st.write(f"- {message}")
            else:
                st.success("ข้อมูลพื้นฐานและคะแนนของภาคเรียนที่เลือกครบตามรายการที่ตรวจสอบ")

            pdf_key = ("manage_pp6_pdf", seat_no, term)
            if st.button(
                "สร้างรายงาน ปพ.6 PDF",
                type="primary",
                icon=":material/picture_as_pdf:",
                key="manage_pp6_create_pdf",
            ):
                try:
                    with st.spinner("กำลังจัดทำรายงาน ปพ.6…"):
                        st.session_state["manage_pp6_pdf"] = build_student_pp6_pdf(
                            seat_no, term
                        )
                    st.session_state["manage_pp6_pdf_key"] = pdf_key
                except (ValueError, OSError, sqlite3.Error) as error:
                    st.error(f"สร้าง PDF ไม่สำเร็จ: {error}")

            if st.session_state.get("manage_pp6_pdf_key") == pdf_key:
                st.download_button(
                    "เปิด / ดาวน์โหลด PDF",
                    data=st.session_state["manage_pp6_pdf"],
                    file_name=f"ปพ6_เลขที่_{seat_no}_ภาค{term}.pdf",
                    mime="application/pdf",
                    icon=":material/download:",
                    type="primary",
                    key="manage_pp6_download_pdf",
                )
