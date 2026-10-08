from collections import defaultdict
from datetime import date
import random
import re

from database import get_connection


WEEKDAYS = ("วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์")
PERIOD_COUNT = 6
CURRICULUM_WEEKLY_LOADS = {
    "lower_primary": {
        "ภาษาไทย": 5,
        "คณิตศาสตร์": 5,
        "วิทยาศาสตร์ฯ": 2,
        "สังคมศึกษาฯ": 2,
        "ประวัติศาสตร์": 1,
        "สุขศึกษาฯ": 1,
        "ศิลปะ": 1,
        "การงานอาชีพ": 1,
        "ภาษาอังกฤษ": 3,
        "อังกฤษสื่อสาร": 2,
        "ต้านทุจริต": 1,
        "แนะแนว": 1,
        "ลูกเสือ": 1,
        "ชุมนุม": 1,
    },
    "upper_primary": {
        "ภาษาไทย": 4,
        "คณิตศาสตร์": 4,
        "วิทยาศาสตร์ฯ": 3,
        "สังคมศึกษาฯ": 2,
        "ประวัติศาสตร์": 1,
        "สุขศึกษา": 1,
        "พลศึกษา": 1,
        "ศิลปะ": 2,
        "การงานอาชีพ": 1,
        "ภาษาอังกฤษ": 2,
        "อังกฤษสื่อสาร": 1,
        "ต้านทุจริต": 1,
        "แนะแนว": 1,
        "ลูกเสือ": 1,
        "ชุมนุม": 1,
    },
}


def curriculum_weekly_loads(grade_number):
    if not 1 <= int(grade_number) <= 6:
        raise ValueError("ระดับชั้นต้องอยู่ระหว่าง ป.1 ถึง ป.6")
    curriculum_key = "lower_primary" if int(grade_number) <= 3 else "upper_primary"
    return dict(CURRICULUM_WEEKLY_LOADS[curriculum_key])


def _curriculum_subject_name(subject, grade_number):
    subject = str(subject or "").strip()
    normalized = subject.casefold()
    if "ลูกเสือ" in normalized:
        return "ลูกเสือ"
    if "แนะแนว" in normalized:
        return "แนะแนว"
    if "ชุมนุม" in normalized:
        return "ชุมนุม"
    if "อังกฤษ" in normalized and ("เพิ่ม" in normalized or "สื่อสาร" in normalized):
        return "อังกฤษสื่อสาร"
    if "อังกฤษ" in normalized:
        return "ภาษาอังกฤษ"
    if "ทุจริต" in normalized:
        return "ต้านทุจริต"
    if "วิทยาศาสตร์" in normalized and "วิทยาการคำนวณ" not in normalized:
        return "วิทยาศาสตร์ฯ"
    if "สังคมศึกษา" in normalized:
        return "สังคมศึกษาฯ"
    if "ประวัติศาสตร์" in normalized:
        return "ประวัติศาสตร์"
    if "คณิตศาสตร์" in normalized:
        return "คณิตศาสตร์"
    if "ภาษาไทย" in normalized:
        return "ภาษาไทย"
    if "การงานอาชีพ" in normalized:
        return "การงานอาชีพ"
    if "ศิลปะ" in normalized:
        return "ศิลปะ"
    if "พลศึกษา" in normalized and grade_number >= 4:
        return "พลศึกษา"
    if "สุขศึกษา" in normalized and grade_number >= 4:
        return "สุขศึกษา"
    if "สุขศึกษา" in normalized or "พลศึกษา" in normalized:
        return "สุขศึกษาฯ"
    return None


def _is_physical_education_subject(subject, grade_number):
    subject = str(subject or "").strip()
    normalized = subject.casefold()
    if int(grade_number) <= 3:
        return "พลศึกษา" in normalized or normalized in {
            "สุขศึกษาฯ",
            "สุขศึกษาและพลศึกษา",
        }
    return "พลศึกษา" in normalized


def _is_first_period_core_subject(subject):
    normalized = " ".join(str(subject or "").split()).casefold()
    return "คณิตศาสตร์" in normalized or "ภาษาไทย" in normalized


def _validate_period_six_slot(grade, weekday, period, subject):
    if int(period) != PERIOD_COUNT:
        return
    match = re.search(r"(?:ประถมศึกษาปีที่|ป\.?)\s*([1-6])", str(grade))
    grade_number = int(match.group(1)) if match else None
    subject = str(subject or "").strip()
    normalized_subject = subject.casefold()
    is_wednesday_pe = (
        int(weekday) == 3
        and grade_number is not None
        and _is_physical_education_subject(subject, grade_number)
    )
    is_thursday_lower_scouts = (
        int(weekday) == 4
        and grade_number is not None
        and grade_number <= 3
        and normalized_subject == "ลูกเสือ"
    )
    is_lower_primary_end_of_day_activity = (
        grade_number in (1, 2)
        and (
            (int(weekday) == 1 and normalized_subject == "โฮมรูม")
            or (int(weekday) == 2 and "แนะแนว" in normalized_subject)
            or (int(weekday) == 3 and "ชุมนุม" in normalized_subject)
            or (
                int(weekday) == 5
                and "สวดมนต์ไหว้พระประจำสัปดาห์" in normalized_subject
            )
        )
    )
    if not (
        is_wednesday_pe
        or is_thursday_lower_scouts
        or is_lower_primary_end_of_day_activity
    ):
        raise ValueError(
            "คาบ 6 อนุญาตเฉพาะพละวันพุธ ลูกเสือวันพฤหัสบดี ป.1–ป.3 "
            "และกิจกรรมท้ายวันตามตาราง ป.1–ป.2"
        )


def curriculum_teacher_loads(grade_number, grade, room, schedules, subjects):
    """Match curriculum subjects to their teachers in the imported class timetable."""
    class_teachers = defaultdict(lambda: defaultdict(int))
    for row in schedules:
        if (
            str(row.get("grade", "")).strip() != str(grade).strip()
            or str(row.get("room", "") or "").strip() != str(room or "").strip()
        ):
            continue
        teacher = str(row.get("teacher", "") or "").strip()
        subject = _curriculum_subject_name(row.get("subject"), grade_number)
        if teacher and subject in subjects:
            class_teachers[subject][teacher] += 1
    return {
        subject: dict(teacher_counts)
        for subject, teacher_counts in class_teachers.items()
    }


def get_class_schedules():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT grade, room, weekday, period, subject, teacher "
            "FROM class_schedules ORDER BY grade, room, weekday, period"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "grade": grade,
            "room": room,
            "weekday": weekday,
            "period": period,
            "subject": subject,
            "teacher": teacher,
        }
        for grade, room, weekday, period, subject, teacher in rows
    ]


def validate_schedule_entries(entries, grade=None):
    normalized = []
    seen_slots = set()
    seen_daily_subjects = set()
    for entry in entries:
        weekday = int(entry["weekday"])
        period = int(entry["period"])
        subject = str(entry.get("subject", "") or "").strip()
        teacher = str(entry.get("teacher", "") or "").strip()

        if not 1 <= weekday <= len(WEEKDAYS):
            raise ValueError("วันเรียนต้องอยู่ระหว่างวันจันทร์ถึงวันศุกร์")
        if not 1 <= period <= PERIOD_COUNT:
            raise ValueError(f"คาบเรียนต้องอยู่ระหว่างคาบ 1 ถึง {PERIOD_COUNT}")
        if teacher and not subject:
            raise ValueError("กรุณาระบุรายวิชาเมื่อกำหนดครูผู้สอน")
        if not subject:
            continue
        if grade is not None:
            _validate_period_six_slot(grade, weekday, period, subject)

        slot = (weekday, period)
        if slot in seen_slots:
            raise ValueError("พบการระบุคาบเรียนซ้ำในวันและคาบเดียวกัน")
        seen_slots.add(slot)
        daily_subject = (weekday, " ".join(subject.split()).casefold())
        if daily_subject in seen_daily_subjects:
            raise ValueError("รายวิชาเดียวกันห้ามเรียนซ้ำในวันเดียวกัน")
        seen_daily_subjects.add(daily_subject)
        normalized.append(
            {
                "weekday": weekday,
                "period": period,
                "subject": subject,
                "teacher": teacher,
            }
        )
    return normalized


def valid_schedule_move_targets(schedules, grade, room):
    class_key = (str(grade).strip(), str(room or "").strip())
    class_rows = [
        row
        for row in schedules
        if (
            str(row.get("grade", "")).strip(),
            str(row.get("room", "") or "").strip(),
        ) == class_key
    ]
    valid_targets = {}
    for source in class_rows:
        source_slot = (int(source["weekday"]), int(source["period"]))
        source_key = f"{source_slot[0]}:{source_slot[1]}"
        targets = []
        for weekday in range(1, len(WEEKDAYS) + 1):
            for period in range(1, PERIOD_COUNT + 1):
                target_slot = (weekday, period)
                if target_slot == source_slot:
                    continue
                target = next(
                    (
                        row
                        for row in class_rows
                        if (int(row["weekday"]), int(row["period"])) == target_slot
                    ),
                    None,
                )
                proposed = [
                    dict(row)
                    for row in schedules
                    if row is not source and row is not target
                ]
                for lesson, slot in ((target, source_slot), (source, target_slot)):
                    if lesson is not None:
                        proposed.append(
                            {
                                **lesson,
                                "grade": class_key[0],
                                "room": class_key[1],
                                "weekday": slot[0],
                                "period": slot[1],
                            }
                        )
                try:
                    validate_schedule_entries(
                        [
                            row
                            for row in proposed
                            if (
                                str(row.get("grade", "")).strip(),
                                str(row.get("room", "") or "").strip(),
                            ) == class_key
                        ],
                        grade=class_key[0],
                    )
                except ValueError:
                    continue
                if any(
                    class_key in conflict["classes"]
                    for conflict in find_teacher_conflicts(proposed)
                ):
                    continue
                targets.append(f"{weekday}:{period}")
        valid_targets[source_key] = targets
    return valid_targets


def move_schedule_entries(
    schedules,
    grade,
    room,
    first_weekday,
    first_period,
    second_weekday,
    second_period,
):
    grade = str(grade).strip()
    room = str(room or "").strip()
    first = (int(first_weekday), int(first_period))
    second = (int(second_weekday), int(second_period))
    if first == second:
        raise ValueError("กรุณาเลือกคนละคาบเพื่อย้าย")

    source = next(
        (
            row
            for row in schedules
            if (
                str(row.get("grade", "")).strip(),
                str(row.get("room", "") or "").strip(),
                int(row["weekday"]),
                int(row["period"]),
            ) == (grade, room, *first)
        ),
        None,
    )
    if source is None:
        raise ValueError("ไม่พบวิชาต้นทางที่จะย้าย")
    target = next(
        (
            row
            for row in schedules
            if (
                str(row.get("grade", "")).strip(),
                str(row.get("room", "") or "").strip(),
                int(row["weekday"]),
                int(row["period"]),
            ) == (grade, room, *second)
        ),
        None,
    )
    proposed = [
        dict(row)
        for row in schedules
        if row is not source and row is not target
    ]
    for lesson, slot in ((target, first), (source, second)):
        if lesson is not None:
            proposed.append(
                {
                    **lesson,
                    "grade": grade,
                    "room": room,
                    "weekday": slot[0],
                    "period": slot[1],
                }
            )
    validate_schedule_entries(
        [
            row
            for row in proposed
            if (
                str(row.get("grade", "")).strip(),
                str(row.get("room", "") or "").strip(),
            ) == (grade, room)
        ],
        grade=grade,
    )
    conflicts = [
        conflict
        for conflict in find_teacher_conflicts(proposed)
        if (grade, room) in conflict["classes"]
    ]
    return proposed, conflicts


def find_teacher_conflicts(schedules):
    teacher_slots = defaultdict(list)
    for schedule in schedules:
        teacher = str(schedule.get("teacher", "") or "").strip()
        if not teacher:
            continue
        key = (
            int(schedule["weekday"]),
            int(schedule["period"]),
            teacher.casefold(),
        )
        teacher_slots[key].append(schedule)

    conflicts = []
    for (weekday, period, _), lessons in teacher_slots.items():
        classes = sorted(
            {
                (lesson["grade"], lesson.get("room", "") or "")
                for lesson in lessons
            }
        )
        if len(classes) < 2:
            continue
        lesson_grade_numbers = [
            int(match.group(1))
            for lesson in lessons
            if (
                match := re.search(
                    r"(?:ประถมศึกษาปีที่|ป\.?)\s*([1-6])",
                    str(lesson["grade"]),
                )
            )
        ]
        grade_numbers = set(lesson_grade_numbers)
        is_joint_scout_session = (
            len(grade_numbers) >= 2
            and all(str(lesson.get("subject", "")).strip().casefold() == "ลูกเสือ"
                    for lesson in lessons)
            and (
                grade_numbers <= {1, 2, 3}
                or grade_numbers <= {4, 5, 6}
            )
        )
        is_joint_pe_session = (
            weekday == 3
            and period == 6
            and len(grade_numbers) >= 2
            and len(lesson_grade_numbers) == len(lessons)
            and (
                (
                    grade_numbers <= {1, 2, 3}
                    and all(
                        _is_physical_education_subject(
                            lesson.get("subject"), grade_number
                        )
                        for lesson, grade_number in zip(
                            lessons, lesson_grade_numbers
                        )
                    )
                )
                or (
                    grade_numbers <= {4, 5, 6}
                    and all(
                        str(lesson.get("subject", "")).strip() == "พลศึกษา"
                        for lesson in lessons
                    )
                )
            )
        )
        if is_joint_scout_session or is_joint_pe_session:
            continue
        conflicts.append(
            {
                "weekday": weekday,
                "period": period,
                "teacher": lessons[0]["teacher"],
                "classes": classes,
            }
        )
    return sorted(conflicts, key=lambda item: (item["weekday"], item["period"], item["teacher"]))


def swap_class_schedule_slots(
    grade,
    room,
    first_weekday,
    first_period,
    second_weekday,
    second_period,
):
    grade = str(grade).strip()
    room = str(room or "").strip()
    first = (int(first_weekday), int(first_period))
    second = (int(second_weekday), int(second_period))
    for weekday, period in (first, second):
        if not 1 <= weekday <= len(WEEKDAYS):
            raise ValueError("วันเรียนต้องอยู่ระหว่างวันจันทร์ถึงวันศุกร์")
        if not 1 <= period <= PERIOD_COUNT:
            raise ValueError(f"คาบเรียนต้องอยู่ระหว่างคาบ 1 ถึง {PERIOD_COUNT}")
    if first == second:
        raise ValueError("กรุณาเลือกคนละคาบเพื่อสลับ")

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT schedule_id, grade, room, weekday, period, subject, teacher "
            "FROM class_schedules"
        ).fetchall()
        schedules = [
            {
                "schedule_id": row[0],
                "grade": row[1],
                "room": row[2],
                "weekday": row[3],
                "period": row[4],
                "subject": row[5],
                "teacher": row[6],
            }
            for row in rows
        ]
        first_row = next(
            (
                row for row in schedules
                if (row["grade"], row["room"], row["weekday"], row["period"])
                == (grade, room, *first)
            ),
            None,
        )
        second_row = next(
            (
                row for row in schedules
                if (row["grade"], row["room"], row["weekday"], row["period"])
                == (grade, room, *second)
            ),
            None,
        )
        if first_row is None and second_row is None:
            conn.rollback()
            raise ValueError("ทั้งสองคาบยังว่าง ไม่มีรายการให้สลับ")

        proposed = [
            {key: value for key, value in row.items() if key != "schedule_id"}
            for row in schedules
            if row not in (first_row, second_row)
        ]
        for lesson, slot in ((second_row, first), (first_row, second)):
            if lesson is not None:
                _validate_period_six_slot(
                    grade, slot[0], slot[1], lesson["subject"]
                )
                proposed.append(
                    {
                        "grade": grade,
                        "room": room,
                        "weekday": slot[0],
                        "period": slot[1],
                        "subject": lesson["subject"],
                        "teacher": lesson["teacher"],
                    }
                )

        validate_schedule_entries(
            [
                row
                for row in proposed
                if (row["grade"], str(row.get("room", "") or "").strip())
                == (grade, room)
            ],
            grade=grade,
        )
        conflicts = find_teacher_conflicts(proposed)
        if conflicts:
            conn.rollback()
            return conflicts

        for row in (first_row, second_row):
            if row is not None:
                conn.execute(
                    "DELETE FROM class_schedules WHERE schedule_id=?",
                    (row["schedule_id"],),
                )
        conn.executemany(
            "INSERT INTO class_schedules "
            "(grade, room, weekday, period, subject, teacher) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (grade, room, slot[0], slot[1], lesson["subject"], lesson["teacher"])
                for lesson, slot in ((second_row, first), (first_row, second))
                if lesson is not None
            ],
        )
        conn.commit()
        return []
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_teacher_substitutions(substitution_date):
    substitution_date = date.fromisoformat(str(substitution_date)).isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT substitution_date, grade, room, weekday, period, subject, "
            "regular_teacher, substitute_teacher FROM teacher_substitutions "
            "WHERE substitution_date=? ORDER BY period, grade, room",
            (substitution_date,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "substitution_date": row[0],
            "grade": row[1],
            "room": row[2],
            "weekday": row[3],
            "period": row[4],
            "subject": row[5],
            "regular_teacher": row[6],
            "substitute_teacher": row[7],
        }
        for row in rows
    ]


def _teacher_conflict_key(conflict):
    return (
        conflict["weekday"],
        conflict["period"],
        conflict["teacher"].casefold(),
        tuple(conflict["classes"]),
    )


def save_teacher_substitutions(substitution_date, absent_teacher, assignments):
    try:
        selected_date = date.fromisoformat(str(substitution_date))
    except (TypeError, ValueError) as error:
        raise ValueError("กรุณาระบุวันที่สอนแทนให้ถูกต้อง") from error
    substitution_date = selected_date.isoformat()
    weekday = selected_date.isoweekday()
    if weekday > len(WEEKDAYS):
        raise ValueError("เลือกวันที่อยู่ในวันจันทร์ถึงวันศุกร์")

    absent_teacher = str(absent_teacher or "").strip()
    if not absent_teacher:
        raise ValueError("กรุณาระบุครูที่ไม่มาปฏิบัติหน้าที่")

    normalized_assignments = []
    seen_slots = set()
    for assignment in assignments:
        grade = str(assignment.get("grade", "") or "").strip()
        room = str(assignment.get("room", "") or "").strip()
        period = int(assignment["period"])
        subject = str(assignment.get("subject", "") or "").strip()
        substitute_teacher = str(
            assignment.get("substitute_teacher", "") or ""
        ).strip()
        if not grade or not subject or not 1 <= period <= PERIOD_COUNT:
            raise ValueError("ข้อมูลชั้น/ห้อง วิชา หรือคาบเรียนไม่ถูกต้อง")
        key = (grade, room, weekday, period)
        if key in seen_slots:
            raise ValueError("พบคาบสอนแทนซ้ำ")
        seen_slots.add(key)
        normalized_assignments.append(
            {
                "grade": grade,
                "room": room,
                "weekday": weekday,
                "period": period,
                "subject": subject,
                "substitute_teacher": substitute_teacher,
            }
        )

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        schedule_rows = conn.execute(
            "SELECT grade, room, weekday, period, subject, teacher "
            "FROM class_schedules WHERE weekday=?",
            (weekday,),
        ).fetchall()
        teacher_lessons = {
            (row[0], row[1], row[2], row[3]): {
                "grade": row[0],
                "room": row[1],
                "weekday": row[2],
                "period": row[3],
                "subject": row[4],
                "teacher": row[5],
            }
            for row in schedule_rows
            if str(row[5] or "").strip().casefold() == absent_teacher.casefold()
        }

        proposed_substitutions = {}
        for assignment in normalized_assignments:
            key = (
                assignment["grade"],
                assignment["room"],
                weekday,
                assignment["period"],
            )
            lesson = teacher_lessons.get(key)
            if lesson is None or lesson["subject"] != assignment["subject"]:
                raise ValueError(
                    "คาบที่เลือกไม่ตรงกับตารางประจำของครูที่ไม่มาปฏิบัติหน้าที่"
                )
            substitute_teacher = assignment["substitute_teacher"]
            if (
                substitute_teacher
                and substitute_teacher.casefold() == absent_teacher.casefold()
            ):
                raise ValueError("ครูที่ไม่มาปฏิบัติหน้าที่ไม่สามารถสอนแทนตนเองได้")
            if substitute_teacher:
                proposed_substitutions[key] = substitute_teacher

        existing_rows = conn.execute(
            "SELECT grade, room, weekday, period, regular_teacher, substitute_teacher "
            "FROM teacher_substitutions WHERE substitution_date=?",
            (substitution_date,),
        ).fetchall()
        current_substitutions = {
            (row[0], row[1], row[2], row[3]): row[5]
            for row in existing_rows
        }
        effective_substitutions = {
            (row[0], row[1], row[2], row[3]): row[5]
            for row in existing_rows
            if str(row[4] or "").strip().casefold() != absent_teacher.casefold()
        }
        effective_substitutions.update(proposed_substitutions)

        original_schedules = [
            {
                "grade": row[0],
                "room": row[1],
                "weekday": row[2],
                "period": row[3],
                "subject": row[4],
                "teacher": row[5],
            }
            for row in schedule_rows
        ]
        proposed_schedules = [
            {
                **schedule,
                "teacher": effective_substitutions.get(
                    (schedule["grade"], schedule["room"], weekday, schedule["period"]),
                    schedule["teacher"],
                ),
            }
            for schedule in original_schedules
        ]
        current_schedules = [
            {
                **schedule,
                "teacher": current_substitutions.get(
                    (schedule["grade"], schedule["room"], weekday, schedule["period"]),
                    schedule["teacher"],
                ),
            }
            for schedule in original_schedules
        ]
        current_conflicts = {
            _teacher_conflict_key(conflict)
            for conflict in find_teacher_conflicts(current_schedules)
        }
        new_conflicts = [
            conflict
            for conflict in find_teacher_conflicts(proposed_schedules)
            if _teacher_conflict_key(conflict) not in current_conflicts
        ]
        if new_conflicts:
            conn.rollback()
            return new_conflicts

        conn.execute(
            "DELETE FROM teacher_substitutions "
            "WHERE substitution_date=? AND lower(trim(regular_teacher))=lower(?)",
            (substitution_date, absent_teacher),
        )
        conn.executemany(
            "INSERT INTO teacher_substitutions "
            "(substitution_date, grade, room, weekday, period, subject, "
            "regular_teacher, substitute_teacher) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    substitution_date,
                    assignment["grade"],
                    assignment["room"],
                    weekday,
                    assignment["period"],
                    assignment["subject"],
                    absent_teacher,
                    assignment["substitute_teacher"],
                )
                for assignment in normalized_assignments
                if assignment["substitute_teacher"]
            ],
        )
        conn.commit()
        return []
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def generate_auto_schedule(classes, teacher_123="", teacher_456="", _attempt=0):
    classes = list(classes)
    teacher_by_band = {
        "123": str(teacher_123 or "").strip(),
        "456": str(teacher_456 or "").strip(),
    }
    if (
        teacher_by_band["123"]
        and teacher_by_band["123"].casefold() == teacher_by_band["456"].casefold()
    ):
        raise ValueError("กรุณาระบุครูคนละคนสำหรับกลุ่ม ป.1–3 และ ป.4–6")

    normalized_classes = []
    target_classes = set()
    teacher_totals = defaultdict(int)
    for class_config in classes:
        grade = str(class_config["grade"]).strip()
        room = str(class_config.get("room", "") or "").strip()
        grade_number = int(class_config["grade_number"])
        if not 1 <= grade_number <= 6:
            raise ValueError("ระดับชั้นต้องอยู่ระหว่าง ป.1 ถึง ป.6")
        class_key = (grade, room)
        if not grade or class_key in target_classes:
            raise ValueError("พบระดับชั้น/ห้องว่างหรือซ้ำในรายการจัดตาราง")
        target_classes.add(class_key)

        subject_counts = {}
        scout_count = None
        for subject, raw_count in class_config["weekly_loads"].items():
            try:
                if raw_count is None or raw_count == "":
                    count = 0
                else:
                    numeric_count = float(raw_count)
                    if not numeric_count.is_integer():
                        raise ValueError
                    count = int(numeric_count)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError(f"จำนวนคาบของวิชา {subject} ต้องเป็นจำนวนเต็ม") from error
            if not 0 <= count <= PERIOD_COUNT * len(WEEKDAYS):
                raise ValueError(
                    f"จำนวนคาบของวิชา {subject} ต้องอยู่ระหว่าง 0 ถึง "
                    f"{PERIOD_COUNT * len(WEEKDAYS)}"
                )
            subject_name = str(subject).strip()
            if subject_name.casefold() == "ลูกเสือ":
                scout_count = count
            if count:
                if not subject_name:
                    raise ValueError("ชื่อรายวิชาต้องไม่เว้นว่าง")
                if count > len(WEEKDAYS):
                    raise ValueError(
                        f"วิชา {subject_name} กำหนดได้ไม่เกิน {len(WEEKDAYS)} "
                        "คาบต่อสัปดาห์ เพราะเรียนได้วันละ 1 ครั้ง"
                    )
                subject_counts[subject_name] = count
        if scout_count is not None and scout_count != 1:
            raise ValueError("กิจกรรมลูกเสือต้องกำหนด 1 คาบต่อสัปดาห์ตามหลักสูตร")
        total = sum(subject_counts.values())
        if total > PERIOD_COUNT * len(WEEKDAYS):
            raise ValueError(
                f"{grade} มีคาบรวม {total} คาบ เกินความจุ "
                f"{PERIOD_COUNT * len(WEEKDAYS)} คาบต่อสัปดาห์"
            )
        teacher = teacher_by_band["123" if grade_number <= 3 else "456"]
        subject_teacher_loads = {}
        raw_teacher_loads = class_config.get("teacher_loads", {})
        for subject, count in subject_counts.items():
            assignments = raw_teacher_loads.get(subject, {})
            if isinstance(assignments, str):
                assignments = {assignments: count} if assignments.strip() else {}
            normalized_assignments = []
            assigned_count = 0
            for assigned_teacher, raw_assigned_count in assignments.items():
                assigned_teacher = str(assigned_teacher or "").strip()
                if not assigned_teacher:
                    continue
                try:
                    numeric_assigned_count = float(raw_assigned_count)
                    if not numeric_assigned_count.is_integer():
                        raise ValueError
                    assigned_periods = int(numeric_assigned_count)
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValueError(
                        f"จำนวนคาบของครู {assigned_teacher} ในวิชา {subject} ต้องเป็นจำนวนเต็ม"
                    ) from error
                if assigned_periods < 0:
                    raise ValueError("จำนวนคาบของครูต้องไม่ติดลบ")
                assigned_periods = min(assigned_periods, count - assigned_count)
                if assigned_periods:
                    normalized_assignments.append((assigned_teacher, assigned_periods))
                    assigned_count += assigned_periods
                    teacher_totals[assigned_teacher.casefold()] += assigned_periods
                if assigned_count == count:
                    break
            remainder = count - assigned_count
            if remainder and teacher:
                normalized_assignments.append((teacher, remainder))
                teacher_totals[teacher.casefold()] += remainder
            elif remainder:
                normalized_assignments.append(("", remainder))
            subject_teacher_loads[subject] = normalized_assignments
        normalized_classes.append(
            {
                "grade": grade,
                "room": room,
                "grade_number": grade_number,
                "teacher": teacher,
                "subject_counts": subject_counts,
                "subject_teacher_loads": subject_teacher_loads,
                "total": total,
            }
        )

    if not any(item["total"] for item in normalized_classes):
        raise ValueError("กรุณากำหนดจำนวนคาบต่อสัปดาห์อย่างน้อยหนึ่งวิชา")

    for assigned_teacher, total in teacher_totals.items():
        if total > PERIOD_COUNT * len(WEEKDAYS):
            raise ValueError(
                f"ครู {assigned_teacher} มีงานสอนรวม {total} คาบต่อสัปดาห์ "
                f"เกินความจุ {PERIOD_COUNT * len(WEEKDAYS)} คาบ "
                "กรุณาปรับจำนวนคาบรายวิชา"
            )

    result = []
    occupied_class_slots = set()
    occupied_teacher_slots = set()
    teacher_daily_load = defaultdict(int)
    pinned_lessons = defaultdict(list)
    for class_config in normalized_classes:
        scout_subject = next(
            (
                subject
                for subject in class_config["subject_counts"]
                if subject.casefold() == "ลูกเสือ"
            ),
            None,
        )
        if scout_subject is None:
            scout = None
        else:
            weekday = 4
            period = 6 if class_config["grade_number"] <= 3 else 5
            class_key = (class_config["grade"], class_config["room"])
            scout_teacher_loads = class_config["subject_teacher_loads"].get(
                scout_subject, []
            )
            teacher = next(
                (name for name, count in scout_teacher_loads if name and count),
                "",
            )
            scout = {
                "grade": class_config["grade"],
                "room": class_config["room"],
                "subject": scout_subject,
                "teacher": teacher,
                "weekday": weekday,
                "period": period,
            }
            result.append(scout)
            pinned_lessons[class_key].append(scout)
            occupied_class_slots.add((class_key, weekday, period))
            if teacher:
                occupied_teacher_slots.add((teacher.casefold(), weekday, period))
                teacher_daily_load[(teacher.casefold(), weekday)] += 1

        physical_subject = next(
            (
                subject
                for subject in class_config["subject_counts"]
                if _is_physical_education_subject(
                    subject, class_config["grade_number"]
                )
            ),
            None,
        )
        if physical_subject is None:
            continue
        class_key = (class_config["grade"], class_config["room"])
        teacher_loads = class_config["subject_teacher_loads"].get(
            physical_subject, []
        )
        teacher = next(
            (name for name, count in teacher_loads if name and count),
            "",
        )
        period = PERIOD_COUNT
        if (class_key, 3, period) in occupied_class_slots:
            raise ValueError(
                f"ไม่สามารถจัดพละวันพุธคาบ 6 ให้ {class_config['grade']}"
                + (f" ห้อง {class_config['room']}" if class_config["room"] else "")
                + " ได้"
            )
        physical_lesson = {
            "grade": class_config["grade"],
            "room": class_config["room"],
            "subject": physical_subject,
            "teacher": teacher,
            "weekday": 3,
            "period": period,
        }
        result.append(physical_lesson)
        pinned_lessons[class_key].append(physical_lesson)
        occupied_class_slots.add((class_key, 3, period))
        if teacher:
            occupied_teacher_slots.add((teacher.casefold(), 3, period))
            teacher_daily_load[(teacher.casefold(), 3)] += 1

    class_order = sorted(
        normalized_classes,
        key=lambda item: (item["grade_number"], item["room"]),
    )
    if _attempt:
        random.Random(_attempt).shuffle(class_order)
    class_order_rank = {
        (item["grade"], item["room"]): index
        for index, item in enumerate(class_order)
    }
    slot_order = [
        (weekday, period)
        for weekday in range(1, len(WEEKDAYS) + 1)
        for period in range(1, PERIOD_COUNT)
    ]
    if _attempt:
        random.Random(_attempt + 10_000).shuffle(slot_order)
    slot_order_rank = {slot: index for index, slot in enumerate(slot_order)}

    for class_config in sorted(
        normalized_classes,
        key=lambda item: (
            -bool(item["teacher"]),
            -item["total"],
            class_order_rank[(item["grade"], item["room"])],
        ),
    ):
        class_key = (class_config["grade"], class_config["room"])
        daily_load = [0] * len(WEEKDAYS)
        subject_daily_load = defaultdict(int)
        subject_at_slot = {}
        remaining = dict(class_config["subject_counts"])
        remaining_teacher_loads = {
            subject: list(assignments)
            for subject, assignments in class_config["subject_teacher_loads"].items()
        }
        for pinned in pinned_lessons[class_key]:
            daily_load[pinned["weekday"] - 1] += 1
            subject_daily_load[(pinned["subject"], pinned["weekday"])] += 1
            subject_at_slot[(pinned["weekday"], pinned["period"])] = pinned["subject"]
            remaining[pinned["subject"]] -= 1
            for index, (teacher_name, teacher_count) in enumerate(
                remaining_teacher_loads[pinned["subject"]]
            ):
                if teacher_name == pinned["teacher"] and teacher_count:
                    if teacher_count == 1:
                        remaining_teacher_loads[pinned["subject"]].pop(index)
                    else:
                        remaining_teacher_loads[pinned["subject"]][index] = (
                            teacher_name, teacher_count - 1
                        )
                    break
            if remaining[pinned["subject"]] == 0:
                del remaining[pinned["subject"]]

        while remaining:
            assignments = [
                (subject, teacher_name, count)
                for subject, subject_assignments in remaining_teacher_loads.items()
                for teacher_name, count in subject_assignments
                if count > 0
            ]
            placement_options = []
            for subject, teacher, teacher_count in assignments:
                teacher_key = teacher.casefold()
                candidates = []
                for weekday in range(1, len(WEEKDAYS) + 1):
                    for period in range(1, PERIOD_COUNT):
                        if (class_key, weekday, period) in occupied_class_slots:
                            continue
                        if subject_daily_load[(subject, weekday)]:
                            continue
                        if (
                            teacher_key
                            and (teacher_key, weekday, period) in occupied_teacher_slots
                        ):
                            continue

                        adjacent = sum(
                            subject_at_slot.get((weekday, neighbor)) == subject
                            for neighbor in (period - 1, period + 1)
                            if 1 <= neighbor <= PERIOD_COUNT
                        )
                        score = (
                            adjacent * 1000
                            + (
                                -10000
                                if period == 1 and _is_first_period_core_subject(subject)
                                else 10000
                                if period == 1
                                else 0
                            )
                            + (
                                250
                                if subject in {
                                    "ภาษาไทย", "คณิตศาสตร์", "วิทยาศาสตร์ฯ", "ภาษาอังกฤษ"
                                } and period > 3
                                else 0
                            )
                            + subject_daily_load[(subject, weekday)] * 100
                            + daily_load[weekday - 1] * 10
                            + teacher_daily_load[(teacher_key, weekday)] * 2
                        )
                        candidates.append((score, weekday, period))
                if candidates:
                    placement_options.append(
                        (
                            all(
                                period == 1
                                and not _is_first_period_core_subject(subject)
                                for _, _, period in candidates
                            ),
                            len(candidates),
                            -teacher_totals[teacher_key],
                            -teacher_count,
                            subject not in {
                                "ภาษาไทย", "คณิตศาสตร์", "วิทยาศาสตร์ฯ", "ภาษาอังกฤษ"
                            },
                            subject.casefold(),
                            teacher_key,
                            subject,
                            teacher,
                            candidates,
                        )
                    )

            if not placement_options:
                if _attempt < 64:
                    return generate_auto_schedule(
                        classes,
                        teacher_123,
                        teacher_456,
                        _attempt=_attempt + 1,
                    )
                subject, teacher, _ = min(
                    assignments,
                    key=lambda item: (
                        -teacher_totals[item[1].casefold()],
                        -item[2],
                        item[0].casefold(),
                        item[1].casefold(),
                    ),
                )
                teacher_key = teacher.casefold()
                free_class_slots = sum(
                    (class_key, weekday, period) not in occupied_class_slots
                    for weekday in range(1, len(WEEKDAYS) + 1)
                    for period in range(1, PERIOD_COUNT)
                )
                free_teacher_slots = (
                    sum(
                        (teacher_key, weekday, period) not in occupied_teacher_slots
                        for weekday in range(1, len(WEEKDAYS) + 1)
                        for period in range(1, PERIOD_COUNT)
                    )
                    if teacher_key
                    else free_class_slots
                )
                raise ValueError(
                    f"ไม่สามารถจัดคาบให้ {class_config['grade']}"
                    + (f" ห้อง {class_config['room']}" if class_config["room"] else "")
                    + f" วิชา {subject}"
                    + (f" ครู {teacher}" if teacher else "")
                    + f" (คาบที่เหลือ {sum(remaining.values())}, "
                    + f"ช่องชั้นเรียนว่าง {free_class_slots}, "
                    + f"ช่องครูว่าง {free_teacher_slots})"
                    + " ได้โดยไม่ทำให้ครูสอนชน กรุณาตรวจจำนวนคาบ/ครู"
                )

            (
                _, _, _, _, _, _, teacher_key, subject, teacher, candidates
            ) = min(placement_options)
            _, weekday, period = min(
                candidates,
                key=lambda candidate: (
                    candidate[0],
                    slot_order_rank[(candidate[1], candidate[2])],
                ),
            )
            result.append(
                {
                    "grade": class_config["grade"],
                    "room": class_config["room"],
                    "subject": subject,
                    "teacher": teacher,
                    "weekday": weekday,
                    "period": period,
                }
            )
            occupied_class_slots.add((class_key, weekday, period))
            subject_at_slot[(weekday, period)] = subject
            daily_load[weekday - 1] += 1
            subject_daily_load[(subject, weekday)] += 1
            if teacher_key:
                occupied_teacher_slots.add((teacher_key, weekday, period))
                teacher_daily_load[(teacher_key, weekday)] += 1
            remaining[subject] -= 1
            for index, (teacher_name, count) in enumerate(
                remaining_teacher_loads[subject]
            ):
                if teacher_name == teacher:
                    if count == 1:
                        remaining_teacher_loads[subject].pop(index)
                    else:
                        remaining_teacher_loads[subject][index] = (teacher_name, count - 1)
                    break
            if remaining[subject] == 0:
                del remaining[subject]

    return sorted(result, key=lambda item: (item["grade"], item["weekday"], item["period"]))


def save_auto_schedule(
    schedules,
    target_classes=None,
    teacher_123="",
    teacher_456="",
):
    target_classes = target_classes or {
        (str(row["grade"]).strip(), str(row.get("room", "") or "").strip())
        for row in schedules
    }
    target_classes = {
        (str(grade).strip(), str(room or "").strip())
        for grade, room in target_classes
    }
    if not target_classes:
        raise ValueError("ไม่มีรายการตารางสำหรับบันทึก")
    schedules_by_class = defaultdict(list)
    for lesson in schedules:
        schedules_by_class[
            (str(lesson["grade"]).strip(), str(lesson.get("room", "") or "").strip())
        ].append(lesson)
    for (grade, _), lessons in schedules_by_class.items():
        validate_schedule_entries(lessons, grade=grade)
    conflicts_in_plan = find_teacher_conflicts(schedules)
    if conflicts_in_plan:
        raise ValueError("ตารางจำลองมีครูสอนชนกัน กรุณาสร้างตารางใหม่")

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT grade, room, weekday, period, subject, teacher FROM class_schedules"
        ).fetchall()
        preserved = [
            {
                "grade": row[0],
                "room": row[1],
                "weekday": row[2],
                "period": row[3],
                "subject": row[4],
                "teacher": row[5],
            }
            for row in existing
            if (row[0], row[1]) not in target_classes
        ]
        conflicts = find_teacher_conflicts([*preserved, *schedules])
        if conflicts:
            conn.rollback()
            return conflicts

        conn.executemany(
            "DELETE FROM class_schedules WHERE grade=? AND room=?",
            sorted(target_classes),
        )
        conn.executemany(
            "INSERT INTO class_schedules "
            "(grade, room, weekday, period, subject, teacher) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    row["grade"],
                    str(row.get("room", "") or ""),
                    int(row["weekday"]),
                    int(row["period"]),
                    row["subject"],
                    row["teacher"],
                )
                for row in schedules
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)",
            (
                ("schedule_teacher_123", teacher_123),
                ("schedule_teacher_456", teacher_456),
            ),
        )
        conn.commit()
        return []
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_class_schedule(grade, room, entries):
    grade = str(grade).strip()
    room = str(room or "").strip()
    if not grade:
        raise ValueError("กรุณาระบุระดับชั้น")
    return save_class_schedules({(grade, room): entries})


def save_class_schedules(replacements):
    normalized_replacements = {}
    for (grade, room), entries in replacements.items():
        grade = str(grade).strip()
        room = str(room or "").strip()
        if not grade:
            raise ValueError("กรุณาระบุระดับชั้น")
        class_key = (grade, room)
        if class_key in normalized_replacements:
            raise ValueError("พบระดับชั้น/ห้องซ้ำในรายการบันทึก")
        normalized_replacements[class_key] = validate_schedule_entries(
            entries,
            grade=grade,
        )
    if not normalized_replacements:
        return []

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT grade, room, weekday, period, subject, teacher FROM class_schedules"
        ).fetchall()
        other_schedules = [
            {
                "grade": row[0],
                "room": row[1],
                "weekday": row[2],
                "period": row[3],
                "subject": row[4],
                "teacher": row[5],
            }
            for row in existing
            if (row[0], row[1]) not in normalized_replacements
        ]
        proposed = []
        for (grade, room), entries in normalized_replacements.items():
            proposed.extend(
                {
                    "grade": grade,
                    "room": room,
                    **entry,
                }
                for entry in entries
            )
        replaced_classes = set(normalized_replacements)
        conflicts = [
            conflict
            for conflict in find_teacher_conflicts([*other_schedules, *proposed])
            if replaced_classes.intersection(conflict["classes"])
        ]
        if conflicts:
            conn.rollback()
            return conflicts

        for grade, room in normalized_replacements:
            conn.execute(
                "DELETE FROM class_schedules WHERE grade=? AND room=?",
                (grade, room),
            )
        conn.executemany(
            "INSERT INTO class_schedules "
            "(grade, room, weekday, period, subject, teacher) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    grade,
                    room,
                    entry["weekday"],
                    entry["period"],
                    entry["subject"],
                    entry["teacher"],
                )
                for (grade, room), entries in normalized_replacements.items()
                for entry in entries
            ],
        )
        conn.commit()
        return []
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
