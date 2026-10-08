import json
import sqlite3


AUDITED_TABLES = (
    "config",
    "students",
    "student_profiles",
    "attendance_daily",
    "subject_scores",
    "indicator_assessments",
    "activities",
    "desired_traits",
    "reading_analysis",
    "competency_assessments",
    "class_schedules",
    "teacher_substitutions",
)

TABLE_LABELS = {
    "config": "การตั้งค่าระบบ",
    "students": "ทะเบียนนักเรียน",
    "student_profiles": "ประวัตินักเรียน",
    "attendance_daily": "เวลาเรียน",
    "subject_scores": "คะแนนรายวิชา",
    "indicator_assessments": "ผลตัวชี้วัด",
    "activities": "กิจกรรมพัฒนาผู้เรียน",
    "desired_traits": "คุณลักษณะอันพึงประสงค์",
    "reading_analysis": "การอ่าน คิดวิเคราะห์ และเขียน",
    "competency_assessments": "สมรรถนะ",
    "class_schedules": "ตารางเรียนและตารางสอน",
    "teacher_substitutions": "รายการครูสอนแทน",
}

ACTION_LABELS = {"INSERT": "เพิ่ม", "UPDATE": "แก้ไข", "DELETE": "ลบ"}


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _record_key_expression(columns, prefix: str) -> str:
    primary_key_columns = sorted(
        (int(column[5]), str(column[1]))
        for column in columns
        if int(column[5]) > 0
    )
    if not primary_key_columns:
        return f"CAST({prefix}.rowid AS TEXT)"
    return " || ' · ' || ".join(
        "COALESCE(CAST("
        f"{prefix}.{_quote_identifier(column_name)} AS TEXT), '')"
        for _, column_name in primary_key_columns
    )


def _json_object_expression(columns, prefix: str) -> str:
    pairs = []
    for column in columns:
        name = str(column[1])
        pairs.extend((json.dumps(name, ensure_ascii=False), f"{prefix}.{_quote_identifier(name)}"))
    return "json_object(" + ", ".join(pairs) + ")"


def install_audit_triggers(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            academic_year TEXT,
            actor TEXT NOT NULL,
            table_name TEXT NOT NULL,
            action TEXT NOT NULL,
            record_key TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT
        )
        """
    )
    for table in AUDITED_TABLES:
        table_identifier = _quote_identifier(table)
        columns = connection.execute(
            f"PRAGMA table_info({table_identifier})"
        ).fetchall()
        if not columns:
            continue
        trigger_base = f"audit_{table}"
        key_new = _record_key_expression(columns, "NEW")
        key_old = _record_key_expression(columns, "OLD")
        new_json = _json_object_expression(columns, "NEW")
        old_json = _json_object_expression(columns, "OLD")
        table_literal = _quote_literal(table)
        changed_condition = " OR ".join(
            f"OLD.{_quote_identifier(str(column[1]))} "
            f"IS NOT NEW.{_quote_identifier(str(column[1]))}"
            for column in columns
        )
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {_quote_identifier(f"{trigger_base}_insert")}
            AFTER INSERT ON {table_identifier}
            BEGIN
                INSERT INTO audit_events (
                    occurred_at, academic_year, actor, table_name, action,
                    record_key, before_json, after_json
                ) VALUES (
                    strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'),
                    (SELECT val FROM config WHERE key='year'),
                    'ผู้ใช้ระบบ', {table_literal}, 'INSERT', {key_new}, NULL, {new_json}
                );
            END
            """,
        )
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {_quote_identifier(f"{trigger_base}_update")}
            AFTER UPDATE ON {table_identifier}
            WHEN {changed_condition}
            BEGIN
                INSERT INTO audit_events (
                    occurred_at, academic_year, actor, table_name, action,
                    record_key, before_json, after_json
                ) VALUES (
                    strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'),
                    (SELECT val FROM config WHERE key='year'),
                    'ผู้ใช้ระบบ', {table_literal}, 'UPDATE', {key_new}, {old_json}, {new_json}
                );
            END
            """,
        )
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {_quote_identifier(f"{trigger_base}_delete")}
            AFTER DELETE ON {table_identifier}
            BEGIN
                INSERT INTO audit_events (
                    occurred_at, academic_year, actor, table_name, action,
                    record_key, before_json, after_json
                ) VALUES (
                    strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'),
                    (SELECT val FROM config WHERE key='year'),
                    'ผู้ใช้ระบบ', {table_literal}, 'DELETE', {key_old}, {old_json}, NULL
                );
            END
            """,
        )


def get_audit_events(
    connection: sqlite3.Connection,
    *,
    table_name: str | None = None,
    action: str | None = None,
    limit: int = 500,
) -> list[dict]:
    if not 1 <= int(limit) <= 2000:
        raise ValueError("จำนวนประวัติที่เรียกดูต้องอยู่ระหว่าง 1–2000")
    conditions = []
    parameters = []
    if table_name:
        if table_name not in AUDITED_TABLES:
            raise ValueError("ไม่รู้จักตารางข้อมูลที่เลือก")
        conditions.append("table_name = ?")
        parameters.append(table_name)
    if action:
        if action not in ACTION_LABELS:
            raise ValueError("ไม่รู้จักประเภทการเปลี่ยนแปลงที่เลือก")
        conditions.append("action = ?")
        parameters.append(action)
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = connection.execute(
        "SELECT event_id, occurred_at, academic_year, actor, table_name, action, "
        "record_key, before_json, after_json FROM audit_events"
        + where_clause
        + " ORDER BY event_id DESC LIMIT ?",
        (*parameters, int(limit)),
    ).fetchall()
    return [
        {
            "event_id": row[0],
            "occurred_at": row[1],
            "academic_year": row[2] or "",
            "actor": row[3],
            "table_name": row[4],
            "action": row[5],
            "record_key": row[6],
            "before": json.loads(row[7]) if row[7] else None,
            "after": json.loads(row[8]) if row[8] else None,
        }
        for row in rows
    ]
