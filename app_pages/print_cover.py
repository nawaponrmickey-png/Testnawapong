import streamlit as st

from additional_reports import build_attendance_summary_pdf, build_class_score_pdf
from attendance_monthly_pdf import MONTHS as MONTH_NAMES, build_monthly_attendance_pdf
from database import get_connection
from exporter import SUBJECT_LIST
from html_pdf import render_html_pdf
from pp5_cover_pdf import build_pp5_cover_pdf
from pp6_report_pdf import build_student_pp6_pdf
from report_completeness import report_completeness_messages
from web_reports import build_class_report

st.header("ระบบพิมพ์เอกสาร", icon=":material/print:")
st.caption("เลือกชนิดเอกสาร แล้วสร้างไฟล์สำหรับพิมพ์หรือบันทึก")


def check_report_completeness(student_seat_nos, *, subject_keys=None, terms=(1, 2)):
    connection = get_connection()
    try:
        return report_completeness_messages(
            connection,
            student_seat_nos,
            subject_keys=subject_keys,
            terms=terms,
        )
    finally:
        connection.close()


document_type = st.selectbox(
    "เลือกชนิดเอกสาร",
    [
        "ปก ปพ.5 รายวิชา · PDF",
        "ปพ.5 ทั้งห้อง · PDF",
        "ปพ.6 รายบุคคล · PDF",
        "บันทึกเวลาเรียนรายเดือน · PDF",
        "สรุปเวลาเรียนประจำชั้น · PDF",
        "คะแนนประจำชั้นรายวิชา · PDF",
    ],
    key="new_print_document_type",
)

conn = get_connection()
config = dict(conn.execute("SELECT key, val FROM config").fetchall())
student_count = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
students = conn.execute(
    "SELECT seat_no, title, first_name, last_name FROM students ORDER BY seat_no"
).fetchall()
conn.close()

st.container(border=True).markdown(
    f"**สถานศึกษา:** {config.get('school_name', 'ยังไม่ได้ระบุ')}  \n"
    f"**ระดับชั้น:** {config.get('grade', 'ยังไม่ได้ระบุ')} · "
    f"**ปีการศึกษา:** {config.get('year', 'ยังไม่ได้ระบุ')} · "
    f"**นักเรียน:** {student_count} คน"
)

if not student_count:
    st.info("ยังไม่มีรายชื่อนักเรียน กรุณาเพิ่มรายชื่อก่อนสร้างเอกสาร")
    st.stop()

if document_type == "ปพ.5 ทั้งห้อง · PDF":
    completeness = check_report_completeness(
        [student[0] for student in students]
    )
    if completeness:
        st.warning("ก่อนพิมพ์หรือส่งออก โปรดตรวจสอบข้อมูลที่อาจยังไม่ครบ:")
        for message in completeness:
            st.write(f"- {message}")
    else:
        st.success("ข้อมูลพื้นฐานและคะแนนสำหรับรายงานนี้ครบตามรายการที่ตรวจสอบ")

if document_type == "บันทึกเวลาเรียนรายเดือน · PDF":
    term = st.selectbox(
        "ภาคเรียน",
        [1, 2],
        format_func=lambda value: f"ภาคเรียนที่ {value}",
        key="new_print_monthly_attendance_term",
    )
    months = [5, 6, 7, 8, 9, 10] if term == 1 else [11, 12, 1, 2, 3]
    month = st.selectbox(
        "เดือน",
        months,
        format_func=lambda value: MONTH_NAMES[value],
        key="new_print_monthly_attendance_month",
    )
    if st.button("สร้างบันทึกเวลาเรียน PDF", type="primary", icon=":material/picture_as_pdf:"):
        try:
            with st.spinner("กำลังจัดทำตารางเวลาเรียนรายเดือน…"):
                pdf_bytes = build_monthly_attendance_pdf(term, month)
            st.session_state["new_print_monthly_attendance_pdf"] = pdf_bytes
            st.session_state["new_print_monthly_attendance_pdf_key"] = (term, month)
        except Exception as error:
            st.error(f"สร้าง PDF ไม่สำเร็จ: {error}")
    if st.session_state.get("new_print_monthly_attendance_pdf_key") == (term, month):
        st.download_button(
            "เปิด / ดาวน์โหลด PDF",
            data=st.session_state["new_print_monthly_attendance_pdf"],
            file_name=f"เวลาเรียน_{MONTH_NAMES[month]}_ภาค{term}.pdf",
            mime="application/pdf",
            icon=":material/download:",
            type="primary",
        )

elif document_type == "สรุปเวลาเรียนประจำชั้น · PDF":
    period = st.selectbox(
        "ช่วงเวลา",
        ["1", "2", "ทั้งปี"],
        format_func=lambda value: {
            "1": "ภาคเรียนที่ 1",
            "2": "ภาคเรียนที่ 2",
            "ทั้งปี": "ตลอดปีการศึกษา",
        }[value],
        key="new_print_attendance_period",
    )
    if st.button("สร้างรายงานเวลาเรียน PDF", type="primary", icon=":material/picture_as_pdf:"):
        try:
            with st.spinner("กำลังสรุปข้อมูลเวลาเรียน…"):
                pdf_bytes = build_attendance_summary_pdf(period)
            st.session_state["new_print_attendance_pdf"] = pdf_bytes
            st.session_state["new_print_attendance_pdf_key"] = period
        except Exception as error:
            st.error(f"สร้าง PDF ไม่สำเร็จ: {error}")
    if st.session_state.get("new_print_attendance_pdf_key") == period:
        st.download_button(
            "เปิด / ดาวน์โหลด PDF",
            data=st.session_state["new_print_attendance_pdf"],
            file_name=f"สรุปเวลาเรียน_ภาค{period}.pdf",
            mime="application/pdf",
            icon=":material/download:",
            type="primary",
        )

elif document_type == "คะแนนประจำชั้นรายวิชา · PDF":
    subject_key = st.selectbox(
        "รายวิชา",
        [subject[0] for subject in SUBJECT_LIST],
        format_func=lambda key: next(
            f"{name} ({kind})" for subject_id, name, kind in SUBJECT_LIST if subject_id == key
        ),
        key="new_print_score_subject",
    )
    period = st.selectbox(
        "ช่วงเวลา",
        ["1", "2", "ทั้งปี"],
        format_func=lambda value: {
            "1": "ภาคเรียนที่ 1",
            "2": "ภาคเรียนที่ 2",
            "ทั้งปี": "ตลอดปีการศึกษา",
        }[value],
        key="new_print_score_period",
    )
    selected_terms = (1, 2) if period == "ทั้งปี" else (int(period),)
    completeness = check_report_completeness(
        [student[0] for student in students],
        subject_keys=[subject_key],
        terms=selected_terms,
    )
    if completeness:
        st.warning("ก่อนพิมพ์หรือส่งออก โปรดตรวจสอบข้อมูลที่อาจยังไม่ครบ:")
        for message in completeness:
            st.write(f"- {message}")
    else:
        st.success("ข้อมูลพื้นฐานและคะแนนสำหรับรายงานนี้ครบตามรายการที่ตรวจสอบ")
    if st.button("สร้างคะแนนประจำชั้น PDF", type="primary", icon=":material/picture_as_pdf:"):
        try:
            with st.spinner("กำลังจัดทำรายงานคะแนน…"):
                pdf_bytes = build_class_score_pdf(subject_key, period)
            st.session_state["new_print_score_pdf"] = pdf_bytes
            st.session_state["new_print_score_pdf_key"] = (subject_key, period)
        except Exception as error:
            st.error(f"สร้าง PDF ไม่สำเร็จ: {error}")
    if st.session_state.get("new_print_score_pdf_key") == (subject_key, period):
        st.download_button(
            "เปิด / ดาวน์โหลด PDF",
            data=st.session_state["new_print_score_pdf"],
            file_name=f"คะแนนประจำชั้น_{subject_key}_ภาค{period}.pdf",
            mime="application/pdf",
            icon=":material/download:",
            type="primary",
        )

elif document_type == "ปก ปพ.5 รายวิชา · PDF":
    with st.form("new_pp5_course_cover_form"):
        subject_key = st.selectbox(
            "รายวิชา",
            [subject[0] for subject in SUBJECT_LIST],
            format_func=lambda key: next(
                f"{name} ({kind})" for subject_id, name, kind in SUBJECT_LIST if subject_id == key
            ),
        )
        semester = st.selectbox(
            "ภาคเรียน",
            ["1", "2", "ทั้งปี"],
            format_func=lambda value: {
                "1": "ภาคเรียนที่ 1",
                "2": "ภาคเรียนที่ 2",
                "ทั้งปี": "ตลอดปีการศึกษา",
            }[value],
        )
        room = st.text_input("ห้อง", value=config.get("room", "1"))
        submitted = st.form_submit_button(
            "สร้างปก PDF", type="primary", icon=":material/picture_as_pdf:"
        )

    if submitted:
        try:
            normalized_room = room.strip() or "1"
            with st.spinner("กำลังจัดทำปก PDF…"):
                pdf_bytes = build_pp5_cover_pdf(subject_key, semester, normalized_room)
            st.session_state["pp5_course_cover_pdf"] = pdf_bytes
            st.session_state["pp5_course_cover_pdf_key"] = (subject_key, semester, normalized_room)
            st.session_state["pp5_course_cover_pdf_name"] = f"ปก_ปพ5_{subject_key}_ภาค{semester}.pdf"
        except Exception as error:
            st.error(f"สร้าง PDF ไม่สำเร็จ: {error}")

    normalized_room = room.strip() or "1"
    selection_key = (subject_key, semester, normalized_room)
    if st.session_state.get("pp5_course_cover_pdf_key") == selection_key:
        st.download_button(
            "เปิด / ดาวน์โหลด PDF",
            data=st.session_state["pp5_course_cover_pdf"],
            file_name=st.session_state["pp5_course_cover_pdf_name"],
            mime="application/pdf",
            icon=":material/download:",
            type="primary",
        )
        st.caption("เปิดไฟล์ PDF แล้วสั่งพิมพ์ได้ โดยรูปแบบและการจัดหน้าถูกกำหนดในไฟล์")

elif document_type == "ปพ.6 รายบุคคล · PDF":
    seat_no = st.selectbox(
        "เลือกนักเรียน",
        [student[0] for student in students],
        format_func=lambda number: next(
            f"{row[0]} · {row[1] or ''}{row[2] or ''} {row[3] or ''}".strip()
            for row in students if row[0] == number
        ),
        key="new_print_student",
    )
    term = st.selectbox(
        "ภาคเรียน",
        [1, 2],
        format_func=lambda value: f"ภาคเรียนที่ {value}",
        key="new_print_pp6_term",
    )
    completeness = check_report_completeness(
        [seat_no],
        terms=[term],
    )
    if completeness:
        st.warning("ก่อนพิมพ์หรือส่งออก โปรดตรวจสอบข้อมูลที่อาจยังไม่ครบ:")
        for message in completeness:
            st.write(f"- {message}")
    else:
        st.success("ข้อมูลพื้นฐานและคะแนนสำหรับรายงานนี้ครบตามรายการที่ตรวจสอบ")
    if st.button("สร้างรายงาน ปพ.6 PDF", type="primary", icon=":material/picture_as_pdf:"):
        try:
            with st.spinner("กำลังจัดทำรายงาน ปพ.6…"):
                pdf_bytes = build_student_pp6_pdf(seat_no, term)
            st.session_state["new_print_pp6_pdf"] = pdf_bytes
            st.session_state["new_print_pp6_pdf_key"] = (seat_no, term)
        except Exception as error:
            st.error(f"สร้าง PDF ไม่สำเร็จ: {error}")
    if st.session_state.get("new_print_pp6_pdf_key") == (seat_no, term):
        st.download_button(
            "เปิด / ดาวน์โหลด PDF",
            data=st.session_state["new_print_pp6_pdf"],
            file_name=f"ปพ6_เลขที่_{seat_no}_ภาค{term}.pdf",
            mime="application/pdf",
            icon=":material/download:",
            type="primary",
        )

else:
    st.caption("ระบบจะจัดหน้าเอกสารและสร้าง PDF ให้พร้อมดาวน์โหลด")
    if st.button("สร้าง PDF", type="primary", icon=":material/picture_as_pdf:"):
        try:
            with st.spinner("กำลังสร้างไฟล์ PDF…"):
                report_html = build_class_report()
                pdf_bytes = render_html_pdf(report_html)
            document_key = document_type
            st.session_state["new_print_report_pdf"] = pdf_bytes
            st.session_state["new_print_report_pdf_key"] = document_key
            st.session_state["new_print_report_pdf_name"] = "ปพ5_ฉบับเต็ม_ล่าสุด.pdf"
        except Exception as error:
            st.error(f"สร้าง PDF ไม่สำเร็จ: {error}")

    document_key = document_type
    if st.session_state.get("new_print_report_pdf_key") == document_key:
        st.download_button(
            "เปิด / ดาวน์โหลด PDF",
            data=st.session_state["new_print_report_pdf"],
            file_name=st.session_state["new_print_report_pdf_name"],
            mime="application/pdf",
            icon=":material/download:",
            type="primary",
        )
        st.caption("เปิด PDF แล้วสั่งพิมพ์ได้ รูปแบบเอกสารถูกจัดหน้าไว้ในไฟล์แล้ว")
