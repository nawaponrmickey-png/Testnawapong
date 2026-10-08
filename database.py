import os
import sqlite3
from pathlib import Path

from audit_log import install_audit_triggers


APP_ROOT = Path(__file__).resolve().parent
BASE_DATABASE_PATH = Path(os.environ.get("PP5_DATABASE_PATH", APP_ROOT / "pp5_system.db")).expanduser()
ACTIVE_YEAR_FILE = BASE_DATABASE_PATH.with_name("pp5_active_year.txt")


def _year_database_path(year):
    return BASE_DATABASE_PATH.with_name(
        f"{BASE_DATABASE_PATH.stem}_{int(year)}{BASE_DATABASE_PATH.suffix}"
    )


def _initial_academic_year():
    if BASE_DATABASE_PATH.is_file():
        try:
            with sqlite3.connect(BASE_DATABASE_PATH) as conn:
                row = conn.execute(
                    "SELECT val FROM config WHERE key='year'"
                ).fetchone()
            if row and str(row[0]).isdigit():
                return int(row[0])
        except sqlite3.Error:
            pass
    return 2569


def get_active_academic_year():
    if ACTIVE_YEAR_FILE.is_file():
        try:
            return int(ACTIVE_YEAR_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
    return _initial_academic_year()


def get_database_path(year=None):
    active_year = int(year if year is not None else get_active_academic_year())
    target = _year_database_path(active_year)
    ACTIVE_YEAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ACTIVE_YEAR_FILE.exists():
        # Preserve the existing single-year database as the initial year's data.
        if BASE_DATABASE_PATH.exists() and not target.exists() and target != BASE_DATABASE_PATH:
            target.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(BASE_DATABASE_PATH) as source, sqlite3.connect(target) as backup:
                source.backup(backup)
        ACTIVE_YEAR_FILE.write_text(str(active_year), encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def list_available_academic_years():
    years = set()
    if BASE_DATABASE_PATH.parent.exists():
        prefix = f"{BASE_DATABASE_PATH.stem}_"
        suffix = BASE_DATABASE_PATH.suffix
        for path in BASE_DATABASE_PATH.parent.glob(f"{prefix}*{suffix}"):
            year_text = path.name[len(prefix):-len(suffix)] if suffix else path.name[len(prefix):]
            if year_text.isdigit():
                years.add(int(year_text))
    years.add(get_active_academic_year())
    return sorted(years)


def activate_academic_year(year):
    """Switch to an existing year's database or initialize a clean one."""
    year = int(year)
    if not 2500 <= year <= 2700:
        raise ValueError("ปีการศึกษาต้องอยู่ระหว่าง พ.ศ. 2500–2700")

    current_path = get_database_path()
    current_year = get_active_academic_year()
    if year == current_year:
        return False

    shared_config = {}
    if current_path.is_file():
        conn = sqlite3.connect(current_path)
        try:
            shared_config = dict(conn.execute(
                "SELECT key, val FROM config WHERE key IN "
                "('school_name','address','grade','teacher_1','teacher_2','advisor','academic_head','director')"
            ).fetchall())
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    target_path = _year_database_path(year)
    is_new_year = not target_path.exists()
    ACTIVE_YEAR_FILE.write_text(str(year), encoding="utf-8")
    if is_new_year:
        try:
            init_db()
            conn = get_connection()
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)",
                    shared_config.items(),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO config (key, val) VALUES ('year', ?)",
                    (str(year),),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            ACTIVE_YEAR_FILE.write_text(str(current_year), encoding="utf-8")
            raise
    return is_new_year


def get_connection():
    database_path = get_database_path()
    conn = sqlite3.connect(database_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS students (seat_no INTEGER PRIMARY KEY, citizen_id TEXT, student_id TEXT, title TEXT, first_name TEXT, last_name TEXT)")
    cur.execute("""CREATE TABLE IF NOT EXISTS student_profiles (
        grade TEXT NOT NULL,
        room TEXT NOT NULL DEFAULT '',
        seat_no INTEGER NOT NULL,
        student_id TEXT NOT NULL,
        citizen_id TEXT DEFAULT '',
        gender TEXT DEFAULT '',
        title TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        last_name TEXT DEFAULT '',
        birth_date TEXT DEFAULT '',
        age TEXT DEFAULT '',
        weight TEXT DEFAULT '',
        height TEXT DEFAULT '',
        blood_group TEXT DEFAULT '',
        religion TEXT DEFAULT '',
        race TEXT DEFAULT '',
        nationality TEXT DEFAULT '',
        house_no TEXT DEFAULT '',
        village_no TEXT DEFAULT '',
        road TEXT DEFAULT '',
        subdistrict TEXT DEFAULT '',
        district TEXT DEFAULT '',
        province TEXT DEFAULT '',
        guardian_title TEXT DEFAULT '',
        guardian_first_name TEXT DEFAULT '',
        guardian_last_name TEXT DEFAULT '',
        guardian_occupation TEXT DEFAULT '',
        guardian_relationship TEXT DEFAULT '',
        father_title TEXT DEFAULT '',
        father_first_name TEXT DEFAULT '',
        father_last_name TEXT DEFAULT '',
        father_occupation TEXT DEFAULT '',
        mother_title TEXT DEFAULT '',
        mother_first_name TEXT DEFAULT '',
        mother_last_name TEXT DEFAULT '',
        mother_occupation TEXT DEFAULT '',
        disadvantaged TEXT DEFAULT '',
        pending_dismissal TEXT DEFAULT '',
        PRIMARY KEY (grade, student_id)
    )""")
    cur.execute("CREATE TABLE IF NOT EXISTS attendance_daily (seat_no INTEGER, term INTEGER, month_no INTEGER, day INTEGER, status TEXT, PRIMARY KEY(seat_no, term, month_no, day))")
    cur.execute("CREATE TABLE IF NOT EXISTS subject_scores (seat_no INTEGER, subject_key TEXT, term INTEGER, formative REAL, exam REAL, PRIMARY KEY(seat_no, subject_key, term))")
    cur.execute("CREATE TABLE IF NOT EXISTS indicator_assessments (seat_no INTEGER, subject_key TEXT, indicator_code TEXT, checked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(seat_no, subject_key, indicator_code))")
    indicator_columns = {row[1] for row in cur.execute("PRAGMA table_info(indicator_assessments)")}
    if "level" not in indicator_columns:
        cur.execute("ALTER TABLE indicator_assessments ADD COLUMN level INTEGER")
    cur.execute("CREATE TABLE IF NOT EXISTS activities (seat_no INTEGER, activity_key TEXT, term INTEGER, result TEXT, PRIMARY KEY(seat_no, activity_key, term))")
    cur.execute("CREATE TABLE IF NOT EXISTS desired_traits (seat_no INTEGER, term INTEGER, t1 REAL, t2 REAL, t3 REAL, t4 REAL, t5 REAL, t6 REAL, t7 REAL, t8 REAL, result TEXT, l1 INTEGER, l2 INTEGER, l3 INTEGER, l4 INTEGER, l5 INTEGER, l6 INTEGER, l7 INTEGER, l8 INTEGER, PRIMARY KEY(seat_no, term))")
    cur.execute("CREATE TABLE IF NOT EXISTS reading_analysis (seat_no INTEGER, term INTEGER, r1 REAL, r2 REAL, r3 REAL, r4 REAL, result TEXT, a1 INTEGER, a2 INTEGER, a3 INTEGER, PRIMARY KEY(seat_no, term))")
    extra_columns = {
        "desired_traits": [f"l{i} INTEGER" for i in range(1, 9)],
        "reading_analysis": [f"a{i} INTEGER" for i in range(1, 4)],
    }
    for table, definitions in extra_columns.items():
        columns = {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}
        for definition in definitions:
            column = definition.split()[0]
            if column not in columns:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
    cur.execute("CREATE TABLE IF NOT EXISTS competency_assessments (seat_no INTEGER, term INTEGER, c1 TEXT DEFAULT 'ยังไม่บันทึก', c2 TEXT DEFAULT 'ยังไม่บันทึก', c3 TEXT DEFAULT 'ยังไม่บันทึก', c4 TEXT DEFAULT 'ยังไม่บันทึก', c5 TEXT DEFAULT 'ยังไม่บันทึก', PRIMARY KEY(seat_no, term))")
    cur.execute("""CREATE TABLE IF NOT EXISTS class_schedules (
        schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
        grade TEXT NOT NULL,
        room TEXT NOT NULL DEFAULT '',
        weekday INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 5),
        period INTEGER NOT NULL CHECK (period BETWEEN 1 AND 6),
        subject TEXT NOT NULL,
        teacher TEXT NOT NULL,
        UNIQUE (grade, room, weekday, period)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS teacher_substitutions (
        substitution_date TEXT NOT NULL,
        grade TEXT NOT NULL,
        room TEXT NOT NULL DEFAULT '',
        weekday INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 5),
        period INTEGER NOT NULL CHECK (period BETWEEN 1 AND 6),
        subject TEXT NOT NULL,
        regular_teacher TEXT NOT NULL,
        substitute_teacher TEXT NOT NULL,
        PRIMARY KEY (substitution_date, grade, room, weekday, period)
    )""")
    for k, v in [
        ("school_name", "โรงเรียนสาธิต ปพ.5"),
        ("address", "ข้อมูลสาธิต"),
        ("grade", "ชั้นประถมศึกษาปีที่ 1"),
        ("year", "2569"),
        ("teacher_1", "ครูตัวอย่าง"),
    ]:
        cur.execute("INSERT OR IGNORE INTO config (key, val) VALUES (?, ?)", (k, v))
    install_audit_triggers(conn)
    conn.commit()
    conn.close()
