import sqlite3

from database import get_active_academic_year, get_database_path


GRADE_MAPPING = {
    f"ชั้นประถมศึกษาปีที่ {grade}": f"ชั้นประถมศึกษาปีที่ {grade}"
    for grade in range(1, 7)
}


def _sample_students(grade_number):
    return [
        (
            seat_no,
            f"DEMO-{grade_number}-{seat_no:02d}",
            "เด็กชาย" if seat_no % 2 else "เด็กหญิง",
            "ตัวอย่าง",
            f"สาธิต{grade_number}-{seat_no:02d}",
        )
        for seat_no in range(1, 6)
    ]


def import_grade_data(grade_label="ชั้นประถมศึกษาปีที่ 1"):
    if grade_label not in GRADE_MAPPING:
        return False, f"ไม่พบข้อมูลระดับชั้น {grade_label}"

    grade_number = int(grade_label[-1])
    students = _sample_students(grade_number)
    with sqlite3.connect(get_database_path()) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)",
            [
                ("school_name", "โรงเรียนสาธิต ปพ.5"),
                ("address", "ข้อมูลสาธิต"),
                ("grade", grade_label),
                ("teacher_1", "ครูตัวอย่าง"),
                ("teacher_2", ""),
                ("year", str(get_active_academic_year())),
            ],
        )
        conn.execute("DELETE FROM students")
        conn.executemany(
            "INSERT INTO students "
            "(seat_no, citizen_id, student_id, title, first_name, last_name) "
            "VALUES (?, '', ?, ?, ?, ?)",
            students,
        )

    return True, f"โหลดข้อมูลสาธิต {grade_label} จำนวน {len(students)} คน เรียบร้อยแล้ว"
