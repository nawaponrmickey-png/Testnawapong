import hmac
import os
import sqlite3
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from database import (
    activate_academic_year,
    get_active_academic_year,
    get_database_path,
    init_db,
    list_available_academic_years,
)
from database_backup import ensure_daily_database_backup

st.set_page_config(
    page_title="ระบบ ปพ.5 ออนไลน์",
    page_icon=":material/menu_book:",
    layout="wide",
)
SCHOOL_LOGO = Path(__file__).resolve().parent / "assets" / "school_logo.jpg"
if SCHOOL_LOGO.is_file():
    st.logo(
        str(SCHOOL_LOGO),
        size="large",
        icon_image=str(SCHOOL_LOGO),
    )

app_password = os.environ.get("PP5_PASSWORD", "").strip()
if not app_password:
    try:
        app_password = str(st.secrets["PP5_PASSWORD"]).strip()
    except (KeyError, StreamlitSecretNotFoundError):
        pass

if not app_password:
    st.error("ยังไม่ได้ตั้งค่า PP5_PASSWORD ใน Secrets ของโฮสต์ จึงปิดระบบไว้เพื่อความปลอดภัย")
    st.stop()

if not st.session_state.get("pp5_authenticated", False):
    st.title("ระบบ ปพ.5 สาธิต")
    with st.form("pp5_login"):
        password = st.text_input("รหัสผ่านเข้าใช้งาน", type="password")
        submitted = st.form_submit_button("เข้าสู่ระบบ", type="primary")
    if submitted:
        if hmac.compare_digest(password, app_password):
            st.session_state["pp5_authenticated"] = True
            st.rerun()
        st.error("รหัสผ่านไม่ถูกต้อง")
    st.stop()

init_db()

active_year = get_active_academic_year()
year_options = sorted(set(list_available_academic_years()) | {active_year + 1})
with st.sidebar:
    st.subheader("ปีการศึกษา")
    selected_year = st.selectbox(
        "เลือกปี",
        year_options,
        index=year_options.index(active_year),
        format_func=lambda year: f"พ.ศ. {year}",
        key="academic_year_selection",
    )
    if selected_year != active_year:
        st.caption(
            "ปีที่มีข้อมูลแล้วจะเปิดข้อมูลเดิม ส่วนปีใหม่จะเริ่มข้อมูลว่าง "
            "และคงข้อมูลโรงเรียน/ครูไว้ ต้องนำเข้ารายชื่อนักเรียนใหม่"
        )
        confirm_year_switch = st.checkbox(
            f"ยืนยันเปลี่ยนไปปีการศึกษา {selected_year}",
            key=f"confirm_year_switch_{selected_year}",
        )
        if st.button(
            f"เปิดปีการศึกษา {selected_year}",
            disabled=not confirm_year_switch,
            type="primary",
            icon=":material/swap_horiz:",
        ):
            try:
                created_new_year = activate_academic_year(selected_year)
                for key in list(st.session_state.keys()):
                    if key.startswith((
                        "score_editor_v2_", "indicator_matrix_editor_", "trait_editor_named_",
                        "trait_editor_level_", "reading_editor_", "competency_editor_", "activity_editor_",
                        "attendance_editor_", "schedule_",
                    )):
                        st.session_state.pop(key, None)
                st.session_state["academic_year_notice"] = (
                    f"เริ่มปีการศึกษา {selected_year} ด้วยฐานข้อมูลใหม่"
                    if created_new_year
                    else f"เปิดข้อมูลปีการศึกษา {selected_year} แล้ว"
                )
                st.rerun()
            except (OSError, sqlite3.Error, ValueError) as error:
                st.sidebar.error(f"เปลี่ยนปีไม่สำเร็จ: {error}")
    if notice := st.session_state.pop("academic_year_notice", None):
        st.success(notice)

backup_error = None
try:
    ensure_daily_database_backup(
        get_database_path(),
        get_active_academic_year(),
    )
except (OSError, sqlite3.Error) as error:
    backup_error = error

page = st.navigation(
    [
        st.Page(
            "app_pages/manage.py",
            title="ระบบจัดการ",
            icon=":material/dashboard:",
            default=True,
        ),
        st.Page(
            "app_pages/schedules.py",
            title="ตารางเรียนและตารางสอน",
            icon=":material/calendar_view_week:",
        ),
        st.Page(
            "app_pages/print_reports.py",
            title="เอกสารเว็บสำหรับพิมพ์",
            icon=":material/print:",
        ),
        st.Page(
            "app_pages/print_cover.py",
            title="ระบบพิมพ์เอกสาร",
            icon=":material/print:",
        ),
    ],
    position="top",
)
page.run()

st.divider()
if backup_error is None:
    st.caption("สำรองฐานข้อมูลอัตโนมัติวันละครั้ง · เก็บย้อนหลัง 30 วัน")
else:
    st.warning(f"สำรองข้อมูลอัตโนมัติไม่สำเร็จ: {backup_error}")
st.caption("ระบบจัดการผลการเรียนและตารางเรียน")
st.caption("พัฒนาโดย ครูนวพงศ์")
