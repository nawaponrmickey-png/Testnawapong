from collections.abc import Iterable

from exporter import SUBJECT_LIST


def report_completeness_messages(
    conn,
    student_seat_nos: Iterable[int],
    *,
    subject_keys: Iterable[str] | None = None,
    terms: Iterable[int] = (1, 2),
) -> list[str]:
    """Return actionable warnings for missing information in a selected report."""
    seat_nos = tuple(dict.fromkeys(int(seat_no) for seat_no in student_seat_nos))
    if not seat_nos:
        return ["ยังไม่มีรายชื่อนักเรียน"]

    config = dict(conn.execute("SELECT key, val FROM config").fetchall())
    missing_metadata = [
        label
        for key, label in (
            ("school_name", "ชื่อสถานศึกษา"),
            ("grade", "ระดับชั้น"),
            ("year", "ปีการศึกษา"),
            ("teacher_1", "ชื่อครูประจำชั้น"),
            ("academic_head", "ชื่อหัวหน้าวิชาการ"),
            ("director", "ชื่อผู้อำนวยการ"),
        )
        if not str(config.get(key, "") or "").strip()
    ]
    messages = []
    if missing_metadata:
        messages.append("ข้อมูลหัวเอกสารยังไม่ครบ: " + ", ".join(missing_metadata))

    placeholders = ",".join("?" for _ in seat_nos)
    students = conn.execute(
        f"SELECT seat_no, student_id, first_name, last_name FROM students "
        f"WHERE seat_no IN ({placeholders}) ORDER BY seat_no",
        seat_nos,
    ).fetchall()
    missing_names = [
        str(row[0])
        for row in students
        if not str(row[2] or "").strip() or not str(row[3] or "").strip()
    ]
    missing_ids = [
        str(row[0]) for row in students if not str(row[1] or "").strip()
    ]
    if missing_names:
        messages.append(
            f"ยังไม่มีชื่อหรือนามสกุลนักเรียน {len(missing_names)} คน "
            f"(เลขที่ {', '.join(missing_names[:8])})"
        )
    if missing_ids:
        messages.append(
            f"ยังไม่มีรหัสนักเรียน {len(missing_ids)} คน "
            f"(เลขที่ {', '.join(missing_ids[:8])})"
        )

    selected_subjects = set(subject_keys) if subject_keys is not None else {
        subject[0] for subject in SUBJECT_LIST
    }
    selected_terms = tuple(dict.fromkeys(int(term) for term in terms))
    if selected_subjects and selected_terms:
        score_rows = conn.execute(
            f"SELECT seat_no, subject_key, term, formative, exam FROM subject_scores "
            f"WHERE seat_no IN ({placeholders})",
            seat_nos,
        ).fetchall()
        saved_scores = {
            (int(row[0]), str(row[1]), int(row[2])): (row[3], row[4])
            for row in score_rows
        }
        missing_score_fields = sum(
            value is None
            for seat_no in seat_nos
            for subject_key in selected_subjects
            for term in selected_terms
            for value in saved_scores.get((seat_no, subject_key, term), (None, None))
        )
        if missing_score_fields:
            messages.append(
                f"คะแนนรายวิชายังขาด {missing_score_fields} ช่อง "
                f"จากรายงาน {len(seat_nos)} คน "
                f"({len(selected_subjects)} วิชา, {len(selected_terms)} ภาคเรียน)"
            )

    return messages
