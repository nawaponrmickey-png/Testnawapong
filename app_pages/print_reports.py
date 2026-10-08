import streamlit as st

from database import get_connection
from web_reports import build_class_report, build_individual_report
from report_completeness import report_completeness_messages

st.header("เอกสารเว็บสำหรับพิมพ์", icon=":material/print:")
st.caption("สร้างรายงานเป็นหน้าเว็บ HTML แยกจากระบบจัดการ แล้วเปิดเอกสารในแท็บใหม่เพื่อพิมพ์หรือบันทึก")

report_type = st.segmented_control(
    "เลือกประเภทรายงาน",
    ["ปพ.5 ทั้งห้อง", "ปพ.6 รายบุคคล"],
    default="ปพ.5 ทั้งห้อง",
    key="web_report_type",
)

if report_type == "ปพ.6 รายบุคคล":
    conn = get_connection()
    students = conn.execute(
        "SELECT seat_no, title, first_name, last_name FROM students ORDER BY seat_no"
    ).fetchall()
    conn.close()
    if not students:
        st.info("ยังไม่มีรายชื่อนักเรียน กรุณาเพิ่มรายชื่อก่อนสร้างรายงาน")
        st.stop()
    seat_no = st.selectbox(
        "เลือกนักเรียน",
        [student[0] for student in students],
        format_func=lambda number: next(
            f"{row[0]} · {row[1] or ''}{row[2] or ''} {row[3] or ''}".strip()
            for row in students if row[0] == number
        ),
        key="web_report_student",
    )

with st.spinner("กำลังจัดหน้าเอกสารเว็บ…"):
    conn = get_connection()
    try:
        completeness = report_completeness_messages(
            conn,
            [seat_no] if report_type == "ปพ.6 รายบุคคล" else [
                student[0]
                for student in conn.execute(
                    "SELECT seat_no FROM students ORDER BY seat_no"
                ).fetchall()
            ],
        )
    finally:
        conn.close()
    if completeness:
        st.warning("ก่อนพิมพ์หรือส่งออก โปรดตรวจสอบข้อมูลที่อาจยังไม่ครบ:")
        for message in completeness:
            st.write(f"- {message}")
    else:
        st.success("ข้อมูลพื้นฐานและคะแนนสำหรับรายงานนี้ครบตามรายการที่ตรวจสอบ")
    report_html = (
        build_class_report()
        if report_type == "ปพ.5 ทั้งห้อง"
        else build_individual_report(seat_no)
    )

file_name = "ปพ5_ฉบับเต็ม_ล่าสุด.html" if report_type == "ปพ.5 ทั้งห้อง" else f"ปพ6_เลขที่_{seat_no}_หน้าเว็บ.html"
st.download_button(
    "บันทึกหน้าเว็บ HTML",
    data=report_html.encode("utf-8"),
    file_name=file_name,
    mime="text/html",
    icon=":material/download:",
    type="primary",
)
st.caption("ตัวอย่างรายงานอยู่ด้านล่าง · ดาวน์โหลดไฟล์ HTML แล้วเปิดไฟล์นั้นเพื่อพิมพ์")
preview_html = report_html.replace(
    '<button type="button" onclick="window.print()">พิมพ์ / บันทึกเป็น PDF</button>',
    '<span>ดาวน์โหลดไฟล์ HTML แล้วเปิดเพื่อพิมพ์</span>',
)
st.iframe(preview_html, height="content")
