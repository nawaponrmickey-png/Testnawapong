import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from import_data import GRADE_MAPPING


_RESET_TABLES = (
    "students",
    "attendance_daily",
    "subject_scores",
    "indicator_assessments",
    "activities",
    "desired_traits",
    "reading_analysis",
    "competency_assessments",
)


def _grade_number(grade_label: str) -> int:
    text = str(grade_label).strip()
    if text.startswith("ชั้นประถมศึกษาปีที่ "):
        text = text.removeprefix("ชั้นประถมศึกษาปีที่ ").strip()
    elif text.startswith("ป."):
        text = text.removeprefix("ป.").strip()
    try:
        grade_number = int(text)
    except ValueError as error:
        raise ValueError("ไม่พบระดับชั้น ป.1–ป.6 ในข้อมูลนักเรียน") from error
    if not 1 <= grade_number <= 6:
        raise ValueError("ระดับชั้นต้องอยู่ระหว่าง ป.1–ป.6")
    return grade_number


def _grade_label(grade_number: int) -> str:
    return list(GRADE_MAPPING)[grade_number - 1]


def _grade_code(grade_number: int) -> str:
    return f"ป.{grade_number}"


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def migration_source_grades(source_path: Path) -> list[str]:
    source_path = Path(source_path)
    if not source_path.is_file():
        raise ValueError(f"ไม่พบฐานข้อมูลปี {source_path.stem}")
    with sqlite3.connect(source_path) as connection:
        if not _table_exists(connection, "student_profiles"):
            return []
        grade_codes = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT grade FROM student_profiles "
                "WHERE grade IS NOT NULL AND TRIM(grade) != ''"
            )
        ]
    grades = sorted(
        {_grade_number(code) for code in grade_codes if str(code).startswith("ป.")})
    return [_grade_label(number) for number in grades]


def _source_roster(connection, grade_number):
    grade_code = _grade_code(grade_number)
    profile_rows = []
    profile_columns = []
    if _table_exists(connection, "student_profiles"):
        profile_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(student_profiles)")
        ]
        required_columns = {
            "grade", "seat_no", "student_id", "citizen_id",
            "title", "first_name", "last_name",
        }
        if required_columns.issubset(profile_columns):
            profile_rows = connection.execute(
                "SELECT * FROM student_profiles WHERE grade=? "
                "ORDER BY seat_no, student_id",
                (grade_code,),
            ).fetchall()

    if profile_rows:
        indexes = {column: profile_columns.index(column) for column in profile_columns}
        roster = [
            (
                row[indexes["seat_no"]],
                row[indexes["citizen_id"]],
                row[indexes["student_id"]],
                row[indexes["title"]],
                row[indexes["first_name"]],
                row[indexes["last_name"]],
            )
            for row in profile_rows
        ]
        return roster, profile_columns, profile_rows

    config = dict(connection.execute("SELECT key, val FROM config").fetchall())
    if _grade_number(config.get("grade", "")) != grade_number:
        return [], profile_columns, []
    roster = connection.execute(
        "SELECT seat_no, citizen_id, student_id, title, first_name, last_name "
        "FROM students ORDER BY seat_no"
    ).fetchall()
    return roster, profile_columns, []


def _target_status(target_path: Path) -> tuple[str | None, dict[str, int]]:
    counts = {}
    if not target_path.exists():
        return None, counts
    try:
        with sqlite3.connect(target_path) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                return "ฐานข้อมูลปีปลายทางเสียหาย", counts
            for table in (*_RESET_TABLES, "student_profiles"):
                if _table_exists(connection, table):
                    counts[table] = int(
                        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
    except sqlite3.Error as error:
        return f"อ่านฐานข้อมูลปีปลายทางไม่สำเร็จ: {error}", counts
    return None, counts


def preview_year_migration(
    source_path: Path,
    target_path: Path,
    source_year: int,
    target_year: int,
    source_grade: str | None = None,
) -> dict:
    source_path = Path(source_path)
    target_path = Path(target_path)
    if int(target_year) != int(source_year) + 1:
        raise ValueError("เลือกย้ายข้อมูลได้เฉพาะปีการศึกษาถัดไป")
    if source_path.resolve() == target_path.resolve():
        raise ValueError("ฐานข้อมูลต้นทางและปลายทางต้องเป็นคนละปี")
    if not source_path.is_file():
        raise ValueError(f"ไม่พบฐานข้อมูลปี {source_year}")

    with sqlite3.connect(source_path) as connection:
        config = dict(connection.execute("SELECT key, val FROM config").fetchall())
        selected_grade = source_grade or config.get("grade", "")
        grade_number = _grade_number(selected_grade)
        source_grade_label = _grade_label(grade_number)
        students, _, profile_rows = _source_roster(connection, grade_number)
        student_ids = [
            str(row[2] or "").strip() for row in students if str(row[2] or "").strip()
        ]
        profile_count = len(profile_rows)

    target_block_reason, target_counts = _target_status(target_path)
    target_profile_conflicts = 0
    if target_path.exists() and student_ids and not target_block_reason:
        with sqlite3.connect(target_path) as target:
            if _table_exists(target, "student_profiles"):
                placeholders = ",".join("?" for _ in student_ids)
                target_profile_conflicts = int(
                    target.execute(
                        f"SELECT COUNT(*) FROM student_profiles "
                        f"WHERE grade != ? AND student_id IN ({placeholders})",
                        (_grade_code(grade_number + 1), *student_ids),
                    ).fetchone()[0]
                )

    graduating_grade = grade_number == 6
    target_has_data = any(target_counts.values())
    target_grade = _grade_label(grade_number + 1) if not graduating_grade else None
    can_migrate = (
        not graduating_grade
        and bool(students)
        and target_block_reason is None
        and target_profile_conflicts == 0
    )
    return {
        "source_year": int(source_year),
        "target_year": int(target_year),
        "source_grade": source_grade_label,
        "target_grade": target_grade,
        "student_count": len(students),
        "profile_count": profile_count,
        "profile_conflicts": target_profile_conflicts,
        "students": students,
        "target_exists": target_path.exists(),
        "target_has_data": target_has_data,
        "target_data_counts": target_counts,
        "target_block_reason": target_block_reason,
        "can_migrate": can_migrate,
        "graduating_grade": graduating_grade,
    }


def migrate_students_to_next_year(
    source_path: Path,
    target_path: Path,
    source_year: int,
    target_year: int,
    source_grade: str | None = None,
    *,
    replace_target_data: bool = False,
) -> dict:
    source_path = Path(source_path)
    target_path = Path(target_path)
    preview = preview_year_migration(
        source_path, target_path, source_year, target_year, source_grade
    )
    if preview["graduating_grade"]:
        raise ValueError("นักเรียน ป.6 จบการศึกษาแล้ว จึงไม่มีชั้นถัดไปให้เลื่อน")
    if preview["student_count"] == 0:
        raise ValueError("ไม่พบรายชื่อนักเรียนในประวัติของชั้นต้นทาง")
    if preview["target_block_reason"]:
        raise ValueError(preview["target_block_reason"])
    if preview["profile_conflicts"]:
        raise ValueError("รหัสนักเรียนซ้ำกับประวัติชั้นอื่นในปีปลายทาง")
    if preview["target_has_data"] and not replace_target_data:
        raise ValueError("ต้องยืนยันแทนที่ข้อมูลในปีปลายทางก่อนย้าย")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".pp5-year-migration-",
        dir=target_path.parent,
    ) as temp_dir:
        staged_path = Path(temp_dir) / target_path.name
        template_path = target_path if target_path.is_file() else source_path
        with sqlite3.connect(template_path) as template, sqlite3.connect(
            staged_path
        ) as staged:
            template.backup(staged)

        with sqlite3.connect(source_path) as source:
            grade_number = _grade_number(preview["source_grade"])
            students, profile_columns, profile_rows = _source_roster(
                source, grade_number
            )

        with sqlite3.connect(staged_path) as staged:
            staged.execute("PRAGMA foreign_keys = ON")
            staged.execute("BEGIN IMMEDIATE")
            target_config = dict(staged.execute("SELECT key, val FROM config").fetchall())

            for table in _RESET_TABLES:
                if _table_exists(staged, table):
                    staged.execute(f"DELETE FROM {table}")

            staged.executemany(
                "INSERT INTO students "
                "(seat_no, citizen_id, student_id, title, first_name, last_name) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                students,
            )

            target_grade_code = _grade_code(grade_number + 1)
            if _table_exists(staged, "student_profiles"):
                staged.execute(
                    "DELETE FROM student_profiles WHERE grade=?",
                    (target_grade_code,),
                )
                student_ids = [
                    str(row[2] or "").strip()
                    for row in students
                    if str(row[2] or "").strip()
                ]
                if student_ids:
                    placeholders = ",".join("?" for _ in student_ids)
                    staged.execute(
                        f"DELETE FROM student_profiles "
                        f"WHERE student_id IN ({placeholders})",
                        student_ids,
                    )
                target_columns = {
                    row[1] for row in staged.execute("PRAGMA table_info(student_profiles)")
                }
                transferable_columns = [
                    column for column in profile_columns
                    if column in target_columns and column != "grade"
                ]
                if profile_rows and transferable_columns:
                    insert_columns = ["grade", *transferable_columns]
                    placeholders = ",".join("?" for _ in insert_columns)
                    insert_sql = (
                        f"INSERT INTO student_profiles ({','.join(insert_columns)}) "
                        f"VALUES ({placeholders})"
                    )
                    grade_index = profile_columns.index("grade")
                    staged.executemany(
                        insert_sql,
                        [
                            (
                                target_grade_code,
                                *(
                                    row[profile_columns.index(column)]
                                    for column in transferable_columns
                                ),
                            )
                            for row in profile_rows
                        ],
                    )

            next_grade_label = _grade_label(grade_number + 1)
            target_config.update(
                {
                    "grade": next_grade_label,
                    "year": str(target_year),
                }
            )
            staged.executemany(
                "INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)",
                target_config.items(),
            )
            staged.commit()

        safety_copy = None
        if target_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safety_copy = target_path.with_name(
                f"{target_path.stem}_before_migration_{target_year}_{timestamp}"
                f"{target_path.suffix}"
            )
            with sqlite3.connect(target_path) as target, sqlite3.connect(
                safety_copy
            ) as backup:
                target.backup(backup)
            for suffix in ("-wal", "-shm"):
                Path(f"{target_path}{suffix}").unlink(missing_ok=True)
        staged_path.replace(target_path)

    return {
        "source_year": int(source_year),
        "target_year": int(target_year),
        "source_grade": preview["source_grade"],
        "target_grade": preview["target_grade"],
        "student_count": preview["student_count"],
        "profile_count": preview["profile_count"],
        "safety_copy": safety_copy,
    }
