import hashlib
import html
import sqlite3
from datetime import date, time
from io import BytesIO

import pandas as pd
import streamlit as st

from database import get_active_academic_year, get_connection
from exporter import SUBJECT_LIST
from import_data import GRADE_MAPPING
from scheduling import (
    PERIOD_COUNT,
    WEEKDAYS,
    find_teacher_conflicts,
    generate_auto_schedule,
    get_class_schedules,
    get_teacher_substitutions,
    move_schedule_entries,
    save_class_schedule,
    save_auto_schedule,
    save_teacher_substitutions,
    swap_class_schedule_slots,
    valid_schedule_move_targets,
    curriculum_teacher_loads,
    curriculum_weekly_loads,
)


language = st.selectbox(
    "ภาษา / Language",
    ["ไทย", "English"],
    key="schedule_language",
    label_visibility="collapsed",
)
is_english = language == "English"


def tr(thai, english):
    return english if is_english else thai


st.header(
    tr("ตารางเรียนและตารางสอน", "Class and teacher timetables"),
    icon=":material/calendar_view_week:",
)
st.caption(
    tr(
        "จัดตารางรายชั้น/ห้อง กระจายวิชาอัตโนมัติ และพิมพ์หรือดาวน์โหลดตารางได้",
        "Manage class and teacher timetables, auto-arrange periods, and print or export.",
    )
)

conn = get_connection()
config = dict(conn.execute("SELECT key, val FROM config").fetchall())
profile_classes = conn.execute(
    "SELECT DISTINCT grade, room FROM student_profiles ORDER BY grade, room"
).fetchall()
conn.close()

def config_integer(key, default, minimum, maximum):
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if minimum <= value <= maximum else default


try:
    saved_start_time = time.fromisoformat(config.get("schedule_start_time", "08:30"))
except (TypeError, ValueError):
    saved_start_time = time(8, 30)
saved_break_after = config_integer("schedule_break_after", 3, 1, PERIOD_COUNT - 1)
saved_period_minutes = config_integer("schedule_period_minutes", 60, 30, 120)
saved_break_minutes = config_integer("schedule_break_minutes", 60, 30, 120)

with st.expander(
    tr("ตั้งเวลาเรียนและพักกลางวัน", "Set class and lunch times"),
    icon=":material/free_breakfast:",
):
    with st.form("schedule_bell_times_form", border=True):
        setting_cols = st.columns(2)
        with setting_cols[0]:
            start_time = st.time_input(
                tr("เวลาเริ่มคาบแรก", "First period starts at"),
                value=saved_start_time,
                key="schedule_start_time_editor",
            )
            break_after = st.selectbox(
                tr("พักกลางวันหลังคาบ", "Lunch break after period"),
                list(range(1, PERIOD_COUNT)),
                index=saved_break_after - 1,
                format_func=lambda value: f"{tr('คาบ', 'Period')} {value}",
                key="schedule_break_after_editor",
            )
        with setting_cols[1]:
            period_minutes = st.number_input(
                tr("เวลาต่อคาบ (นาที)", "Minutes per period"),
                min_value=30,
                max_value=120,
                step=5,
                value=saved_period_minutes,
                key="schedule_period_minutes_editor",
            )
            break_minutes = st.number_input(
                tr("เวลาพักกลางวัน (นาที)", "Lunch break duration (minutes)"),
                min_value=30,
                max_value=120,
                step=5,
                value=saved_break_minutes,
                key="schedule_break_minutes_editor",
            )
        save_times = st.form_submit_button(
            tr("บันทึกเวลาเรียน", "Save class times"),
            type="primary",
            icon=":material/save:",
        )
    if save_times:
        connection = get_connection()
        try:
            connection.executemany(
                "INSERT OR REPLACE INTO config (key, val) VALUES (?, ?)",
                [
                    ("schedule_start_time", start_time.strftime("%H:%M")),
                    ("schedule_break_after", str(break_after)),
                    ("schedule_period_minutes", str(period_minutes)),
                    ("schedule_break_minutes", str(break_minutes)),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        st.session_state["schedule_notice"] = tr("บันทึกเวลาเรียนแล้ว", "Class times saved.")
        st.rerun()
    start_time = saved_start_time
    break_after = saved_break_after
    period_minutes = saved_period_minutes
    break_minutes = saved_break_minutes

schedules = get_class_schedules()
class_keys = {(grade, "") for grade in GRADE_MAPPING}
class_keys.update((grade, str(room or "")) for grade, room in profile_classes)
class_keys.update(
    (row["grade"], str(row["room"] or ""))
    for row in schedules
)
configured_grade = config.get("grade", "")
if configured_grade:
    class_keys.add((configured_grade, ""))

for grade in {key[0] for key in class_keys}:
    if (grade, "") in class_keys and any(g == grade and r for g, r in class_keys):
        class_keys.discard((grade, ""))

grade_order = {grade: index for index, grade in enumerate(GRADE_MAPPING)}
classes = sorted(
    class_keys,
    key=lambda item: (grade_order.get(item[0], len(grade_order)), item[0], item[1]),
)
new_class_option = "เพิ่มชั้น/ห้องใหม่"
legacy_modes = {
    "จัดตารางเรียนรายห้อง": "class",
    "ดูตารางสอนรายครู": "teacher",
    "ช่วยจัดตารางอัตโนมัติ": "auto",
    "จำลองจัดตารางอัตโนมัติ": "auto",
}
if st.session_state.get("schedule_mode") in legacy_modes:
    st.session_state["schedule_mode"] = legacy_modes[st.session_state["schedule_mode"]]
selected_mode = st.session_state.get("schedule_mode", "class")
mode_labels = {
    "class": tr("จัดตารางรายห้อง", "Class timetable"),
    "teacher": tr("ตารางรายครู", "Teacher timetable"),
    "all_teachers": tr("รวมครูทุกคน", "All teachers"),
    "swap": tr("สลับคาบ", "Swap lessons"),
    "cover": tr("จัดสอนแทน", "Teacher substitution"),
    "reports": tr("รายงาน/พิมพ์รวม", "Master report"),
    "auto": tr("จัดอัตโนมัติ", "Auto-schedule"),
}
visible_modes = list(mode_labels.values())
selected_mode_label = st.selectbox(
    tr("เลือกรูปแบบการจัดการ", "Timetable tools"),
    visible_modes,
    index=list(mode_labels).index(selected_mode) if selected_mode in mode_labels else 0,
    key=f"schedule_mode_{language}",
)
class_selection = next(
    mode for mode, label in mode_labels.items() if label == selected_mode_label
)
st.session_state["schedule_mode"] = class_selection


SUBJECT_COLORS = (
    "#6d28d9", "#0369a1", "#be185d", "#0f766e",
    "#b45309", "#4d7c0f", "#c2410c", "#4338ca",
)

_DRAGGABLE_SCHEDULE_GRID = st.components.v2.component(
    "pp5_draggable_schedule_grid",
    html='<div class="schedule-root"></div>',
    css="""
.schedule-scroll { overflow-x: auto; border: 1px solid #e2e8f0; border-radius: 16px;
    background: #fff; box-shadow: 0 8px 28px #312e8110; }
.schedule-grid { border-collapse: separate; border-spacing: 5px; width: 100%;
    min-width: 760px; font-family: inherit; }
.schedule-grid th { padding: 10px 8px; color: #334155; background: #f1f5f9;
    border-radius: 10px; text-align: center; font-size: 14px; }
.schedule-grid thead th { position: sticky; top: 0; background: #172554; color: #fff; }
.schedule-grid td { width: 18%; min-width: 112px; height: 78px; padding: 4px;
    border: 1px dashed #cbd5e1; border-radius: 12px; background: #f8fafc;
    vertical-align: middle; text-align: center; transition: background .15s, border-color .15s; }
.schedule-grid td.drop-target { background: #eef2ff; border: 2px dashed #6366f1; }
.schedule-grid td.move-available { background: #ecfdf5; border: 3px solid #16a34a;
    box-shadow: inset 0 0 0 1px #fff; }
.schedule-grid td.move-available .empty-slot { color: #15803d; }
.schedule-grid .period-cell { min-width: 88px; background: #eef2ff; color: #4338ca; }
.schedule-grid .period-cell span { display: block; margin-top: 3px; font-size: 10px;
    font-weight: 400; color: #64748b; white-space: nowrap; }
.lesson-card { display: flex; flex-direction: column; gap: 4px; margin: 2px;
    padding: 8px 9px; border-radius: 10px; color: #fff; text-align: left;
    box-shadow: 0 3px 8px #0f172a20; cursor: grab; user-select: none; }
.lesson-card:active { cursor: grabbing; }
.lesson-card strong { font-size: 13px; line-height: 1.25; }
.lesson-card span { font-size: 11px; line-height: 1.25; opacity: .95; }
.empty-slot { color: #cbd5e1; font-size: 20px; }
.schedule-grid .break-row th { padding: 6px; color: #92400e;
    background: #fff7ed; border: 1px dashed #fdba74; letter-spacing: .04em; }
""",
    js="""
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.querySelector(".schedule-root");
  if (!root || !data) return;

  const table = document.createElement("table");
  table.className = "schedule-grid";
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  const periodHeading = document.createElement("th");
  periodHeading.textContent = data.periodLabel;
  headerRow.appendChild(periodHeading);
  data.days.forEach((day) => {
    const heading = document.createElement("th");
    heading.textContent = day;
    headerRow.appendChild(heading);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  let allowedTargets = [];
  const cellKey = (weekday, period) => `${weekday}:${period}`;
  const clearMoveTargets = () => {
    table.querySelectorAll(".move-available, .drop-target").forEach((cell) => {
      cell.classList.remove("move-available", "drop-target");
    });
    allowedTargets = [];
  };
  for (let period = 1; period <= data.periodCount; period += 1) {
    const row = document.createElement("tr");
    const periodCell = document.createElement("th");
    periodCell.className = "period-cell";
    const periodName = document.createElement("b");
    periodName.textContent = `${data.periodWord} ${period}`;
    const timeLabel = document.createElement("span");
    timeLabel.textContent = data.times[period - 1];
    periodCell.append(periodName, timeLabel);
    row.appendChild(periodCell);

    for (let weekday = 1; weekday <= data.dayCount; weekday += 1) {
      const cell = document.createElement("td");
      cell.dataset.weekday = String(weekday);
      cell.dataset.period = String(period);
      const lesson = data.lessons.find(
        (item) => item.weekday === weekday && item.period === period
      );
      if (lesson) {
        const card = document.createElement("div");
        card.className = "lesson-card";
        card.draggable = true;
        card.style.backgroundColor = lesson.color;
        const subject = document.createElement("strong");
        subject.textContent = lesson.subject;
        const teacher = document.createElement("span");
        teacher.textContent = lesson.teacher || data.unassignedLabel;
        card.append(subject, teacher);
        card.addEventListener("dragstart", (event) => {
          clearMoveTargets();
          allowedTargets = data.validMoves[cellKey(weekday, period)] || [];
          allowedTargets.forEach((targetKey) => {
            const [targetWeekday, targetPeriod] = targetKey.split(":");
            const targetCell = table.querySelector(
              `td[data-weekday="${targetWeekday}"][data-period="${targetPeriod}"]`
            );
            if (targetCell) targetCell.classList.add("move-available");
          });
          event.dataTransfer.setData(
            "text/plain",
            JSON.stringify({ weekday, period })
          );
          event.dataTransfer.effectAllowed = "move";
        });
        card.addEventListener("dragend", clearMoveTargets);
        cell.appendChild(card);
      } else {
        const empty = document.createElement("span");
        empty.className = "empty-slot";
        empty.textContent = "+";
        cell.appendChild(empty);
      }

      cell.addEventListener("dragover", (event) => {
        if (!allowedTargets.includes(cellKey(weekday, period))) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        cell.classList.add("drop-target");
      });
      cell.addEventListener("dragleave", () => cell.classList.remove("drop-target"));
      cell.addEventListener("drop", (event) => {
        if (!allowedTargets.includes(cellKey(weekday, period))) return;
        event.preventDefault();
        cell.classList.remove("drop-target");
        const source = JSON.parse(event.dataTransfer.getData("text/plain") || "null");
        if (!source || (source.weekday === weekday && source.period === period)) return;
        setTriggerValue("move", {
          from_weekday: source.weekday,
          from_period: source.period,
          to_weekday: weekday,
          to_period: period
        });
      });
      row.appendChild(cell);
    }
    tbody.appendChild(row);

    if (period === data.breakAfter && period < data.periodCount) {
      const breakRow = document.createElement("tr");
      breakRow.className = "break-row";
      const breakCell = document.createElement("th");
      breakCell.colSpan = data.dayCount + 1;
      breakCell.textContent = `${data.lunchLabel} · ${data.lunchTime}`;
      breakRow.appendChild(breakCell);
      tbody.appendChild(breakRow);
    }
  }
  table.appendChild(tbody);

  const legend = document.createElement("div");
  legend.className = "schedule-legend";
  data.subjects.forEach((subject) => {
    const chip = document.createElement("span");
    chip.className = "subject-chip";
    chip.style.backgroundColor = subject.color;
    chip.textContent = subject.name;
    legend.appendChild(chip);
  });
  const scroll = document.createElement("div");
  scroll.className = "schedule-scroll";
  scroll.appendChild(table);
  root.replaceChildren(legend, scroll);
}
""",
)


def subject_color(subject):
    digest = hashlib.sha256(str(subject).encode("utf-8")).digest()
    return SUBJECT_COLORS[digest[0] % len(SUBJECT_COLORS)]


def clock_range(start_time, period_minutes, period, break_after, break_minutes):
    start = start_time.hour * 60 + start_time.minute + (period - 1) * period_minutes
    if period > break_after:
        start += break_minutes
    end = start + period_minutes
    return f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}"


def lunch_range(start_time, period_minutes, break_after, break_minutes):
    start = start_time.hour * 60 + start_time.minute + break_after * period_minutes
    end = start + break_minutes
    return f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}"


def weekday_labels():
    return (
        ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
        if is_english
        else tuple(day.removeprefix("วัน") for day in WEEKDAYS)
    )


def lesson_grid(
    rows,
    view_kind,
    break_after=3,
    start_time=time(8, 30),
    period_minutes=60,
    break_minutes=60,
):
    slots = {}
    subjects = set()
    for row in rows:
        weekday = int(row["weekday"])
        period = int(row["period"])
        if 1 <= weekday <= len(WEEKDAYS) and 1 <= period <= PERIOD_COUNT:
            slots.setdefault((weekday, period), []).append(row)
            subjects.add(str(row["subject"]))

    body_rows = []
    for period in range(1, PERIOD_COUNT + 1):
        cells = []
        for weekday in range(1, len(WEEKDAYS) + 1):
            lessons = slots.get((weekday, period), [])
            cards = []
            for row in lessons:
                subject = html.escape(str(row["subject"]))
                color = subject_color(row["subject"])
                if view_kind in {"teacher", "master"}:
                    detail = f'{row["grade"]}{(" · " + tr("ห้อง ", "Room ") + str(row["room"])) if row.get("room") else ""}'
                else:
                    detail = str(row.get("teacher") or "").strip() or tr(
                        "ยังไม่ระบุครู",
                        "Teacher not assigned",
                    )
                cards.append(
                    f'<div class="lesson-card" style="--lesson-color:{color}">'
                    f'<strong>{subject}</strong>'
                    f'<span>{html.escape(detail)}</span></div>'
                )
            content = "".join(cards) or '<span class="empty-slot">＋</span>'
            cells.append(f"<td>{content}</td>")
        body_rows.append(
            f'<tr><th class="period-cell"><b>{tr("คาบ", "Period")} {period}</b>'
            f'<span>{clock_range(start_time, period_minutes, period, break_after, break_minutes)}</span>'
            f'</th>{"".join(cells)}</tr>'
        )
        if period == break_after and period < PERIOD_COUNT:
            body_rows.append(
                f'<tr class="break-row"><th colspan="6">'
                f'{tr("พักกลางวัน", "Lunch break")} · '
                f'{lunch_range(start_time, period_minutes, break_after, break_minutes)}'
                '</th></tr>'
            )

    headings = "".join(f"<th>{html.escape(day)}</th>" for day in weekday_labels())
    legend = "".join(
        f'<span class="subject-chip" style="background:{subject_color(subject)}">'
        f'{html.escape(subject)}</span>'
        for subject in sorted(subjects, key=str.casefold)
    )
    return f"""<style>
    .schedule-scroll {{ overflow-x:auto; border:1px solid #e2e8f0; border-radius:16px;
        background:#fff; box-shadow:0 8px 28px #312e8110; }}
    .schedule-grid {{ border-collapse:separate; border-spacing:5px; width:100%;
        min-width:760px; font-family:inherit; }}
    .schedule-grid th {{ padding:10px 8px; color:#334155; background:#f1f5f9;
        border-radius:10px; text-align:center; font-size:14px; }}
    .schedule-grid thead th {{ position:sticky; top:0; background:#172554; color:#fff; }}
    .schedule-grid td {{ width:18%; min-width:112px; height:78px; padding:4px;
        border:1px dashed #cbd5e1; border-radius:12px; background:#f8fafc;
        vertical-align:middle; text-align:center; }}
    .schedule-grid .period-cell {{ min-width:88px; background:#eef2ff; color:#4338ca; }}
    .schedule-grid .period-cell span {{ display:block; margin-top:3px; font-size:10px;
        font-weight:400; color:#64748b; white-space:nowrap; }}
    .lesson-card {{ display:flex; flex-direction:column; gap:4px; margin:2px;
        padding:8px 9px; border-radius:10px; background:var(--lesson-color);
        color:#fff; text-align:left; box-shadow:0 3px 8px #0f172a20; }}
    .lesson-card strong {{ font-size:13px; line-height:1.25; }}
    .lesson-card span {{ font-size:11px; line-height:1.25; opacity:.95; }}
    .schedule-legend {{ display:flex; flex-wrap:wrap; gap:6px; padding:10px 4px; }}
    .subject-chip {{ padding:4px 10px; border-radius:999px; color:#fff;
        font-size:12px; font-weight:600; white-space:nowrap; }}
    .empty-slot {{ color:#cbd5e1; font-size:20px; }}
    .schedule-grid .break-row th {{ padding:6px; color:#92400e;
        background:#fff7ed; border:1px dashed #fdba74; letter-spacing:.04em; }}
    </style>
    <div class="schedule-legend">{legend}</div>
    <div class="schedule-scroll"><table class="schedule-grid">
    <thead><tr><th>{tr("คาบเรียน", "Period")}</th>{headings}</tr></thead>
    <tbody>{"".join(body_rows)}</tbody></table></div>"""


def draggable_lesson_grid(
    rows,
    grade,
    room,
    break_after,
    start_time,
    period_minutes,
    break_minutes,
    all_schedules,
    key_suffix="",
):
    component_rows = [
        {
            "weekday": int(row["weekday"]),
            "period": int(row["period"]),
            "subject": str(row["subject"]),
            "teacher": str(row.get("teacher") or "").strip(),
            "color": subject_color(row["subject"]),
        }
        for row in rows
    ]
    subjects = sorted(
        {row["subject"] for row in component_rows},
        key=str.casefold,
    )
    instance_key = hashlib.sha1(
        f"{grade}\0{room}\0{language}\0{key_suffix}".encode("utf-8")
    ).hexdigest()[:12]
    return _DRAGGABLE_SCHEDULE_GRID(
        key=f"draggable_schedule_{instance_key}",
        data={
            "dayCount": len(WEEKDAYS),
            "days": weekday_labels(),
            "periodCount": PERIOD_COUNT,
            "periodLabel": tr("คาบเรียน", "Period"),
            "periodWord": tr("คาบ", "Period"),
            "unassignedLabel": tr("ยังไม่ระบุครู", "Teacher not assigned"),
            "lunchLabel": tr("พักกลางวัน", "Lunch break"),
            "breakAfter": break_after,
            "lunchTime": lunch_range(
                start_time,
                period_minutes,
                break_after,
                break_minutes,
            ),
            "times": [
                clock_range(start_time, period_minutes, period, break_after, break_minutes)
                for period in range(1, PERIOD_COUNT + 1)
            ],
            "lessons": component_rows,
            "validMoves": valid_schedule_move_targets(
                all_schedules,
                grade,
                room,
            ),
            "subjects": [
                {"name": subject, "color": subject_color(subject)}
                for subject in subjects
            ],
        },
        on_move_change=lambda: None,
    )


def excel_text(value):
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def create_excel(rows):
    export_rows = [
        {
            tr("ระดับชั้น", "Grade"): excel_text(row["grade"]),
            tr("ห้อง", "Room"): excel_text(row.get("room", "")),
            tr("วัน", "Day"): weekday_labels()[int(row["weekday"]) - 1],
            tr("คาบ", "Period"): int(row["period"]),
            tr("รายวิชา", "Subject"): excel_text(row["subject"]),
            tr("ครูผู้สอน", "Teacher"): excel_text(row["teacher"]),
        }
        for row in sorted(rows, key=lambda item: (item["weekday"], item["period"]))
    ]
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(
            export_rows,
            columns=[
                tr("ระดับชั้น", "Grade"),
                tr("ห้อง", "Room"),
                tr("วัน", "Day"),
                tr("คาบ", "Period"),
                tr("รายวิชา", "Subject"),
                tr("ครูผู้สอน", "Teacher"),
            ],
        ).to_excel(writer, index=False, sheet_name=tr("ตารางสอน", "Timetable"))
        worksheet = writer.sheets[tr("ตารางสอน", "Timetable")]
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for column, width in {"A": 26, "B": 14, "C": 18, "D": 10, "E": 30, "F": 28}.items():
            worksheet.column_dimensions[column].width = width
    return buffer.getvalue()


def create_substitution_excel(rows):
    columns = [
        tr("วันที่", "Date"),
        tr("ระดับชั้น", "Grade"),
        tr("ห้อง", "Room"),
        tr("คาบ", "Period"),
        tr("รายวิชา", "Subject"),
        tr("ครูประจำ", "Regular teacher"),
        tr("ครูสอนแทน", "Substitute teacher"),
    ]
    export_rows = [
        {
            columns[0]: row["substitution_date"],
            columns[1]: excel_text(row["grade"]),
            columns[2]: excel_text(row["room"]),
            columns[3]: int(row["period"]),
            columns[4]: excel_text(row["subject"]),
            columns[5]: excel_text(row["regular_teacher"]),
            columns[6]: excel_text(row["substitute_teacher"]),
        }
        for row in rows
    ]
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(export_rows, columns=columns).to_excel(
            writer,
            index=False,
            sheet_name=tr("สอนแทน", "Substitution"),
        )
        worksheet = writer.sheets[tr("สอนแทน", "Substitution")]
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for column, width in {
            "A": 16, "B": 26, "C": 14, "D": 10, "E": 30, "F": 28, "G": 28,
        }.items():
            worksheet.column_dimensions[column].width = width
    return buffer.getvalue()


def create_substitution_print_html(rows, title, school_name, year):
    table_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['substitution_date']))}</td>"
        f"<td>{html.escape(str(row['grade']))}</td>"
        f"<td>{html.escape(str(row['room']) or '-')}</td>"
        f"<td>{int(row['period'])}</td>"
        f"<td>{html.escape(str(row['subject']))}</td>"
        f"<td>{html.escape(str(row['regular_teacher']))}</td>"
        f"<td>{html.escape(str(row['substitute_teacher']))}</td>"
        "</tr>"
        for row in rows
    )
    headings = (
        tr("วันที่", "Date"),
        tr("ชั้น", "Grade"),
        tr("ห้อง", "Room"),
        tr("คาบ", "Period"),
        tr("รายวิชา", "Subject"),
        tr("ครูประจำ", "Regular teacher"),
        tr("ครูสอนแทน", "Substitute teacher"),
    )
    return f"""<!doctype html>
<html lang="{"en" if is_english else "th"}">
<head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{ font-family: Sarabun, "Noto Sans Thai", sans-serif; margin: 24px; color: #111; }}
h1, p {{ text-align: center; margin: 6px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 18px; }}
th, td {{ border: 1px solid #555; padding: 8px 5px; text-align: center; }}
th {{ background: #f1f5f3; }}
button {{ display: block; margin: 18px auto; padding: 8px 18px; }}
@media print {{ body {{ margin: 8mm; }} button {{ display: none; }} }}
</style></head>
<body>
<h1>{html.escape(school_name or tr("รายการสอนแทน", "Substitution report"))}</h1>
<p>{html.escape(title)} · {tr("ปีการศึกษา", "Academic year")} {html.escape(str(year))}</p>
<table><thead><tr>{"".join(f"<th>{html.escape(label)}</th>" for label in headings)}</tr></thead>
<tbody>{table_rows}</tbody></table>
<button type="button" onclick="window.print()">{tr("พิมพ์ / บันทึกเป็น PDF", "Print / Save as PDF")}</button>
</body></html>"""


def create_print_html(
    rows,
    title,
    school_name,
    year,
    break_after=3,
    start_time=time(8, 30),
    period_minutes=60,
    break_minutes=60,
):
    grid_rows = []
    for period in range(1, PERIOD_COUNT + 1):
        cells = []
        for weekday in range(1, len(WEEKDAYS) + 1):
            lessons = [
                row for row in rows
                if int(row["weekday"]) == weekday and int(row["period"]) == period
            ]
            cell_content = "".join(
                f'<div class="lesson" style="background:{subject_color(row["subject"])}">'
                f'<strong>{html.escape(str(row["subject"]))}</strong><br>'
                f'<small>{html.escape(str(row.get("teacher") or tr("ยังไม่ระบุครู", "Teacher not assigned")))}'
                f'{(" · " + html.escape(str(row["grade"]))) if row.get("grade") else ""}'
                f'{(" · " + tr("ห้อง ", "Room ") + html.escape(str(row["room"]))) if row.get("room") else ""}'
                "</small></div>"
                for row in lessons
            )
            cells.append(f"<td>{cell_content}</td>")
        grid_rows.append(
            f'<tr><th>{tr("คาบ", "Period")} {period}<br><small>'
            f'{clock_range(start_time, period_minutes, period, break_after, break_minutes)}'
            f'</small></th>{"".join(cells)}</tr>'
        )
    headings = "".join(f"<th>{html.escape(day)}</th>" for day in weekday_labels())
    return f"""<!doctype html>
<html lang="{"en" if is_english else "th"}">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
@page {{ size: A4 landscape; margin: 8mm; }}
body {{ font-family: Sarabun, "Noto Sans Thai", sans-serif; margin: 24px; color: #111; }}
h1, p {{ text-align: center; margin: 6px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 18px; }}
th, td {{ border: 1px solid #555; padding: 8px 5px; text-align: center; vertical-align: middle; }}
th {{ background: #f1f5f3; }}
td {{ height: 62px; white-space: normal; }}
.lesson {{ color:#fff; border-radius:5px; padding:5px; margin:2px; }}
.lesson small {{ color:#fff; }}
.break-row td {{ height:auto; background:#fff7ed; color:#92400e; font-weight:bold; }}
button {{ display: block; margin: 18px auto; padding: 8px 18px; }}
@media print {{
    body {{ margin: 0; }}
    h1 {{ font-size: 16pt; margin: 0 0 2mm; }}
    p {{ font-size: 10pt; margin: 0 0 3mm; }}
    table {{
        table-layout: fixed;
        height: 158mm;
        margin-top: 0;
        break-inside: avoid;
        page-break-inside: avoid;
    }}
    th, td {{ padding: 1.5mm 1mm; font-size: 10pt; }}
    th:first-child {{ width: 19mm; }}
    thead tr {{ height: 10mm; }}
    tbody tr:not(.break-row) {{ height: 23mm; }}
    td {{ height: auto; }}
    .lesson {{
        box-sizing: border-box;
        padding: 1.5mm;
        margin: 0.7mm;
        border-radius: 1.2mm;
        line-height: 1.2;
        overflow-wrap: anywhere;
        break-inside: avoid;
        page-break-inside: avoid;
    }}
    .lesson strong {{ font-size: 10pt; }}
    .lesson small {{ font-size: 9pt; }}
    .break-row {{ height: 7mm; }}
    .break-row td {{ height: auto; padding: 1mm; }}
    button {{ display: none; }}
}}
</style>
</head>
<body>
<h1>{html.escape(school_name or tr("ตารางเรียน", "Timetable"))}</h1>
<p>{html.escape(title)} · {tr("ปีการศึกษา", "Academic year")} {html.escape(str(year))}</p>
<table><thead><tr><th>{tr("คาบ", "Period")}</th>{headings}</tr></thead>
<tbody>{''.join(
    row + (f'<tr class="break-row"><td colspan="6">{tr("พักกลางวัน", "Lunch break")} · '
           f'{lunch_range(start_time, period_minutes, break_after, break_minutes)}</td></tr>'
           if index == break_after and index < PERIOD_COUNT else '')
    for index, row in enumerate(grid_rows, start=1)
)}</tbody></table>
<button type="button" onclick="window.print()">{tr("พิมพ์ / บันทึกเป็น PDF", "Print / Save as PDF")}</button>
</body></html>"""


if notice := st.session_state.pop("schedule_notice", None):
    st.success(notice)

if class_selection == "class":
    options = [*classes, new_class_option]
    class_choice = st.selectbox(
        tr("ชั้น/ห้อง", "Grade / room"),
        options,
        format_func=lambda value: (
            value
            if value == new_class_option
            else f"{value[0]} · ห้อง {value[1]}" if value[1] else value[0]
        ),
        key="schedule_class_choice",
    )
    if class_choice == new_class_option:
        grade = st.selectbox(
            tr("ระดับชั้น", "Grade"),
            list(GRADE_MAPPING),
            index=(
                list(GRADE_MAPPING).index(configured_grade)
                if configured_grade in GRADE_MAPPING
                else 0
            ),
            key="schedule_new_grade",
        )
        room = st.text_input(
            tr("ห้อง (เว้นว่างได้)", "Room (optional)"),
            key="schedule_new_room",
        ).strip()
    else:
        grade, room = class_choice

    class_schedules = [
        row for row in schedules
        if (row["grade"], str(row["room"] or "")) == (grade, room)
    ]
    st.subheader(
        f'{tr("ตารางเรียน", "Class timetable")} · {grade}'
        + (f' {tr("ห้อง", "Room")} {room}' if room else "")
    )
    st.caption(
        tr(
            "ลากวิชาไปคาบอื่นเพื่อย้ายหรือสลับ กรอบสีเขียวคือช่องที่ย้ายได้ "
            "ระบบตรวจครูชนและวิชาเรียนซ้ำก่อนบันทึก",
            "Drag a lesson to move or swap it. Green borders mark valid destinations; "
            "teacher conflicts and duplicate daily subjects are prevented.",
        )
    )
    grid_result = draggable_lesson_grid(
        class_schedules,
        grade,
        room,
        break_after,
        start_time,
        period_minutes,
        break_minutes,
        schedules,
    )
    if grid_result.move:
        move = grid_result.move
        try:
            conflicts = swap_class_schedule_slots(
                grade,
                room,
                move["from_weekday"],
                move["from_period"],
                move["to_weekday"],
                move["to_period"],
            )
            if conflicts:
                conflict_details = "; ".join(
                    f'{item["teacher"]} · {weekday_labels()[item["weekday"] - 1]} '
                    f'{tr("คาบ", "Period")} {item["period"]}'
                    for item in conflicts
                )
                st.error(
                    tr(
                        "ย้ายวิชาไม่ได้ เพราะครูสอนชน: ",
                        "Could not move the lesson because of a teacher conflict: ",
                    )
                    + conflict_details
                )
            else:
                st.session_state["schedule_notice"] = tr(
                    "ย้าย/สลับคาบเรียนแล้ว",
                    "Lesson moved/swapped.",
                )
                st.rerun()
        except (KeyError, TypeError, ValueError, sqlite3.Error) as error:
            st.error(
                f'{tr("ย้าย/สลับคาบไม่สำเร็จ", "Could not move/swap lesson")}: {error}'
            )

    editor_key = "schedule_editor_" + hashlib.sha1(
        f"{grade}\0{room}\0{language}".encode("utf-8")
    ).hexdigest()[:12]
    existing_slots = {
        (int(row["weekday"]), int(row["period"])): row
        for row in class_schedules
    }
    editor_rows = [
        {
            tr("วัน", "Day"): weekday_labels()[weekday - 1],
            tr("คาบ", "Period"): period,
            tr("รายวิชา", "Subject"): existing_slots.get((weekday, period), {}).get("subject", ""),
            tr("ครูผู้สอน", "Teacher"): existing_slots.get((weekday, period), {}).get("teacher", ""),
            "weekday": weekday,
            "period": period,
        }
        for weekday in range(1, len(WEEKDAYS) + 1)
        for period in range(1, PERIOD_COUNT + 1)
    ]
    with st.form(f"schedule_form_{editor_key}", border=True):
        edited = st.data_editor(
            pd.DataFrame(editor_rows),
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            key=editor_key,
            column_config={
                tr("วัน", "Day"): st.column_config.TextColumn(disabled=True),
                tr("คาบ", "Period"): st.column_config.NumberColumn(disabled=True),
                tr("รายวิชา", "Subject"): st.column_config.TextColumn(
                    help=tr("เว้นว่างเพื่อเอาคาบนี้ออก", "Leave blank to clear this period")
                ),
                tr("ครูผู้สอน", "Teacher"): st.column_config.TextColumn(
                    help=tr("ระบุชื่อครูผู้สอน", "Enter teacher name (optional)")
                ),
                "weekday": None,
                "period": None,
            },
            disabled=[tr("วัน", "Day"), tr("คาบ", "Period"), "weekday", "period"],
        )
        submitted = st.form_submit_button(
            tr("บันทึกตารางเรียน", "Save timetable"),
            type="primary",
            icon=":material/save:",
        )

    if submitted:
        entries = [
            {
                "weekday": int(row["weekday"]),
                "period": int(row["period"]),
                "subject": row[tr("รายวิชา", "Subject")],
                "teacher": row[tr("ครูผู้สอน", "Teacher")],
            }
            for _, row in edited.iterrows()
        ]
        try:
            conflicts = save_class_schedule(grade, room, entries)
            if conflicts:
                conflict_lines = [
                    f'{WEEKDAYS[item["weekday"] - 1]} คาบ {item["period"]}: '
                    f'{item["teacher"]} มีสอนที่ ' +
                    ", ".join(
                        f"{conflict_grade}" + (f" ห้อง {conflict_room}" if conflict_room else "")
                        for conflict_grade, conflict_room in item["classes"]
                        if (conflict_grade, conflict_room) != (grade, room)
                    )
                    for item in conflicts
                ]
                st.error("บันทึกไม่ได้ พบครูสอนชนกัน:\n\n" + "\n".join(
                    f"- {line}" for line in conflict_lines
                ))
            else:
                st.session_state.pop(editor_key, None)
                st.session_state["schedule_notice"] = tr("บันทึกตารางเรียนแล้ว", "Timetable saved.")
                st.rerun()
        except (KeyError, TypeError, ValueError) as error:
            st.error(f'{tr("ข้อมูลตารางเรียนไม่ถูกต้อง", "Invalid timetable data")}: {error}')
        except sqlite3.Error as error:
            st.error(f'{tr("บันทึกตารางเรียนไม่สำเร็จ", "Could not save timetable")}: {error}')

    export_rows = class_schedules
    export_label = f"{grade}_{room}" if room else grade
elif class_selection == "teacher":
    teachers = sorted(
        {row["teacher"].strip() for row in schedules if row["teacher"].strip()}
        | {
            value.strip()
            for value in (
                config.get("teacher_1", ""),
                config.get("advisor", ""),
                config.get("schedule_teacher_123", ""),
                config.get("schedule_teacher_456", ""),
            )
            if value.strip()
        }
    )
    if not teachers:
        st.info("ยังไม่มีครูในระบบ กรุณาบันทึกตารางรายห้องและระบุครูผู้สอนก่อน")
        st.stop()
    teacher = st.selectbox(
        tr("ครูผู้สอน", "Teacher"),
        teachers,
        key="schedule_teacher_choice",
    )
    export_rows = [row for row in schedules if row["teacher"].strip() == teacher]
    teacher_conflicts = [
        conflict for conflict in find_teacher_conflicts(schedules)
        if conflict["teacher"].casefold() == teacher.casefold()
    ]
    if teacher_conflicts:
        conflict_text = "; ".join(
            f'{WEEKDAYS[item["weekday"] - 1]} คาบ {item["period"]}'
            for item in teacher_conflicts
        )
        st.warning(f"พบรายการสอนชนของครู {teacher}: {conflict_text}")
    st.subheader(f'{tr("ตารางสอน", "Teacher timetable")} · {teacher}')
    st.html(
        lesson_grid(
            export_rows,
            "teacher",
            break_after,
            start_time,
            period_minutes,
            break_minutes,
        )
    )
    export_label = teacher
elif class_selection == "all_teachers":
    assigned_teachers = sorted(
        {str(row["teacher"]).strip() for row in schedules if str(row["teacher"]).strip()},
        key=str.casefold,
    )
    unassigned_count = sum(not str(row["teacher"]).strip() for row in schedules)
    st.subheader(tr("ตารางสอนครูทั้งหมด", "All teacher timetables"))
    if not assigned_teachers:
        st.info(tr("ยังไม่มีครูที่ระบุชื่อในตาราง", "No teachers are assigned in the timetable yet."))
    else:
        st.caption(
            tr("แสดงครูที่มีชื่อในตาราง", "Teachers listed in the timetable")
            + f": {len(assigned_teachers)}"
        )
        for teacher_name in assigned_teachers:
            teacher_rows = [
                row for row in schedules
                if str(row["teacher"]).strip() == teacher_name
            ]
            with st.expander(
                f"{teacher_name} · {len(teacher_rows)} {tr('คาบ', 'periods')}",
                expanded=len(assigned_teachers) == 1,
            ):
                st.html(
                    lesson_grid(
                        teacher_rows,
                        "teacher",
                        break_after,
                        start_time,
                        period_minutes,
                        break_minutes,
                    )
                )
    if unassigned_count:
        st.warning(
            f'{tr("ยังไม่ระบุครู", "Teacher not assigned")}: {unassigned_count} '
            f'{tr("คาบ", "periods")}'
        )
    export_rows = schedules
    export_label = "ครูทั้งหมด" if not is_english else "all_teachers"
elif class_selection == "swap":
    st.subheader(tr("สลับคาบเรียน", "Swap lessons"))
    if not classes:
        st.info(tr("ยังไม่มีชั้นเรียนในระบบ", "No classes are configured."))
    else:
        swap_class = st.selectbox(
            tr("เลือกชั้น/ห้อง", "Select grade / room"),
            classes,
            format_func=lambda value: (
                f"{value[0]} · {tr('ห้อง', 'Room')} {value[1]}" if value[1] else value[0]
            ),
            key="schedule_swap_class",
        )
        swap_rows = [
            row for row in schedules
            if (row["grade"], str(row["room"] or "")) == swap_class
        ]
        swap_slot_labels = {}
        for weekday in range(1, len(WEEKDAYS) + 1):
            for period in range(1, PERIOD_COUNT + 1):
                row = next(
                    (
                        item for item in swap_rows
                        if item["weekday"] == weekday and item["period"] == period
                    ),
                    None,
                )
                lesson = (
                    str(row["subject"]) + (f" · {row['teacher']}" if row["teacher"] else "")
                    if row else tr("ว่าง", "Empty")
                )
                swap_slot_labels[(weekday, period)] = (
                    f"{weekday_labels()[weekday - 1]} · "
                    f"{tr('คาบ', 'Period')} {period} · {lesson}"
                )
        with st.form("schedule_swap_form", border=True):
            swap_col_a, swap_col_b = st.columns(2)
            with swap_col_a:
                first_slot = st.selectbox(
                    tr("คาบแรก", "First period"),
                    list(swap_slot_labels),
                    format_func=swap_slot_labels.get,
                    key="schedule_swap_first",
                )
            with swap_col_b:
                second_slot = st.selectbox(
                    tr("คาบที่สอง", "Second period"),
                    list(swap_slot_labels),
                    format_func=swap_slot_labels.get,
                    key="schedule_swap_second",
                )
            swap_clicked = st.form_submit_button(
                tr("สลับคาบ", "Swap periods"),
                type="primary",
                icon=":material/swap_horiz:",
            )
        if swap_clicked:
            try:
                conflicts = swap_class_schedule_slots(
                    *swap_class,
                    *first_slot,
                    *second_slot,
                )
                if conflicts:
                    st.error(
                        tr(
                            "สลับไม่ได้ เพราะจะทำให้ครูสอนชนกัน",
                            "Swap rejected because it would create a teacher conflict.",
                        )
                    )
                else:
                    st.session_state["schedule_notice"] = tr(
                        "สลับคาบเรียบร้อยแล้ว",
                        "Periods swapped successfully.",
                    )
                    st.rerun()
            except (TypeError, ValueError, sqlite3.Error) as error:
                st.error(f'{tr("สลับคาบไม่สำเร็จ", "Could not swap periods")}: {error}')
        if swap_rows:
            st.html(
                lesson_grid(
                    swap_rows,
                    "class",
                    break_after,
                    start_time,
                    period_minutes,
                    break_minutes,
                )
            )
    export_rows = []
    export_label = "สลับคาบ" if not is_english else "swap"
elif class_selection == "cover":
    st.subheader(tr("บันทึกครูสอนแทน", "Record teacher substitutions"))
    st.caption(
        tr(
            "เลือกวันที่และครูที่ไม่มาปฏิบัติหน้าที่ แล้วระบุครูสอนแทนเป็นรายคาบ "
            "รายการนี้ไม่แก้ไขตารางประจำ",
            "Choose the date and absent teacher, then assign a substitute for each period. "
            "This does not change the regular timetable.",
        )
    )
    substitution_date = st.date_input(
        tr("วันที่สอนแทน", "Substitution date"),
        value=date.today(),
        key="schedule_substitution_date",
    )
    assigned_teachers = sorted(
        {str(row["teacher"]).strip() for row in schedules if str(row["teacher"]).strip()},
        key=str.casefold,
    )
    if not assigned_teachers:
        st.info(
            tr(
                "ยังไม่มีชื่อครูในตารางประจำ กรุณาระบุครูในตารางรายห้องก่อน",
                "No teachers are assigned in the regular timetable yet. Assign teachers first.",
            )
        )
    else:
        absent_teacher = st.selectbox(
            tr("ครูที่ไม่มาปฏิบัติหน้าที่", "Absent teacher"),
            assigned_teachers,
            key="schedule_substitution_absent_teacher",
        )
        day_number = substitution_date.isoweekday()
        absent_lessons = [
            row for row in schedules
            if row["weekday"] == day_number
            and str(row["teacher"]).strip().casefold() == absent_teacher.casefold()
        ]
        saved_for_date = get_teacher_substitutions(substitution_date.isoformat())
        saved_for_teacher = {
            (row["grade"], str(row["room"] or ""), row["period"]): row["substitute_teacher"]
            for row in saved_for_date
            if row["regular_teacher"].casefold() == absent_teacher.casefold()
        }

        if day_number > len(WEEKDAYS):
            st.info(tr(
                "เลือกวันที่อยู่ในวันจันทร์ถึงวันศุกร์",
                "Choose a date from Monday through Friday.",
            ))
        elif not absent_lessons:
            st.info(
                tr(
                    f"{absent_teacher} ไม่มีคาบสอนตามตารางประจำในวัน{WEEKDAYS[day_number - 1][3:]}",
                    f"{absent_teacher} has no regular lessons on {weekday_labels()[day_number - 1]}.",
                )
            )
        else:
            substitute_column = tr("ครูสอนแทน", "Substitute teacher")
            grade_column = tr("ระดับชั้น", "Grade")
            room_column = tr("ห้อง", "Room")
            period_column = tr("คาบ", "Period")
            subject_column = tr("รายวิชา", "Subject")
            editor_rows = [
                {
                    grade_column: row["grade"],
                    room_column: str(row["room"] or ""),
                    period_column: int(row["period"]),
                    subject_column: row["subject"],
                    substitute_column: saved_for_teacher.get(
                        (
                            row["grade"],
                            str(row["room"] or ""),
                            row["period"],
                        ),
                        "",
                    ),
                }
                for row in sorted(absent_lessons, key=lambda item: item["period"])
            ]
            editor_key = "schedule_substitution_editor_" + hashlib.sha1(
                f"{substitution_date}:{absent_teacher}:{language}".encode("utf-8")
            ).hexdigest()[:12]
            with st.form("schedule_substitution_form", border=True):
                edited_assignments = st.data_editor(
                    pd.DataFrame(editor_rows),
                    hide_index=True,
                    width="stretch",
                    num_rows="fixed",
                    key=editor_key,
                    column_config={
                        grade_column: st.column_config.TextColumn(disabled=True),
                        room_column: st.column_config.TextColumn(disabled=True),
                        period_column: st.column_config.NumberColumn(disabled=True),
                        subject_column: st.column_config.TextColumn(disabled=True),
                        substitute_column: st.column_config.TextColumn(
                            help=tr(
                                "เว้นว่างหากยังไม่มีผู้สอนแทน",
                                "Leave blank if no substitute is assigned.",
                            )
                        ),
                    },
                    disabled=[
                        grade_column,
                        room_column,
                        period_column,
                        subject_column,
                    ],
                )
                save_substitutions_clicked = st.form_submit_button(
                    tr("บันทึกรายการสอนแทน", "Save substitutions"),
                    type="primary",
                    icon=":material/save:",
                )
            if save_substitutions_clicked:
                assignments = [
                    {
                        "grade": row[grade_column],
                        "room": row[room_column],
                        "period": row[period_column],
                        "subject": row[subject_column],
                        "substitute_teacher": (
                            ""
                            if pd.isna(row[substitute_column])
                            else str(row[substitute_column] or "")
                        ),
                    }
                    for _, row in edited_assignments.iterrows()
                ]
                try:
                    conflicts = save_teacher_substitutions(
                        substitution_date.isoformat(),
                        absent_teacher,
                        assignments,
                    )
                    if conflicts:
                        conflict_details = "; ".join(
                            f'{item["teacher"]}, {weekday_labels()[item["weekday"] - 1]} '
                            f'{tr("คาบ", "period")} {item["period"]}'
                            for item in conflicts
                        )
                        st.error(
                            tr(
                                "บันทึกไม่ได้ ครูสอนแทนมีสอนชน: ",
                                "Could not save; the substitute has a scheduling conflict: ",
                            )
                            + conflict_details
                        )
                    else:
                        st.session_state.pop(editor_key, None)
                        st.session_state["schedule_notice"] = tr(
                            "บันทึกรายการสอนแทนแล้ว โดยไม่เปลี่ยนตารางประจำ",
                            "Substitutions saved without changing the regular timetable.",
                        )
                        st.rerun()
                except (KeyError, TypeError, ValueError) as error:
                    st.error(
                        f'{tr("ข้อมูลสอนแทนไม่ถูกต้อง", "Invalid substitution data")}: {error}'
                    )
                except sqlite3.Error as error:
                    st.error(
                        f'{tr("บันทึกรายการสอนแทนไม่สำเร็จ", "Could not save substitutions")}: {error}'
                    )

        if saved_for_date:
            st.subheader(tr("รายการสอนแทนของวันที่เลือก", "Substitutions for selected date"))
            st.dataframe(
                pd.DataFrame([
                    {
                        tr("ชั้น", "Grade"): row["grade"],
                        tr("ห้อง", "Room"): row["room"],
                        tr("คาบ", "Period"): row["period"],
                        tr("รายวิชา", "Subject"): row["subject"],
                        tr("ครูประจำ", "Regular teacher"): row["regular_teacher"],
                        tr("ครูสอนแทน", "Substitute teacher"): row["substitute_teacher"],
                    }
                    for row in saved_for_date
                ]),
                hide_index=True,
                width="stretch",
            )
            report_title = (
                f'{tr("รายการสอนแทน", "Substitution report")} · '
                f"{substitution_date.isoformat()}"
            )
            substitution_excel = create_substitution_excel(saved_for_date)
            substitution_html = create_substitution_print_html(
                saved_for_date,
                report_title,
                config.get("school_name", ""),
                get_active_academic_year(),
            )
            report_col, print_col = st.columns(2)
            with report_col:
                st.download_button(
                    tr("ดาวน์โหลดรายงาน Excel", "Download Excel report"),
                    data=substitution_excel,
                    file_name=f"substitutions_{substitution_date.isoformat()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    icon=":material/download:",
                )
            with print_col:
                st.download_button(
                    tr("ดาวน์โหลดรายงานสำหรับพิมพ์", "Download printable report"),
                    data=substitution_html.encode("utf-8"),
                    file_name=f"substitutions_{substitution_date.isoformat()}.html",
                    mime="text/html",
                    icon=":material/print:",
                )
            st.iframe(
                substitution_html.replace(
                    f'<button type="button" onclick="window.print()">{tr("พิมพ์ / บันทึกเป็น PDF", "Print / Save as PDF")}</button>',
                    f"<span>{tr('ดาวน์โหลดไฟล์ HTML แล้วเปิดเพื่อพิมพ์', 'Download and open the HTML file to print.')}</span>",
                ),
                height=360,
            )
    export_rows = []
    export_label = "สอนแทน" if not is_english else "substitutions"
elif class_selection == "reports":
    st.subheader(tr("รายงานตารางเรียนรวมทุกห้อง", "Master timetable report"))
    if schedules:
        st.metric(tr("จำนวนคาบที่จัดแล้ว", "Scheduled periods"), len(schedules))
        st.html(
            lesson_grid(
                schedules,
                "master",
                break_after,
                start_time,
                period_minutes,
                break_minutes,
            )
        )
    else:
        st.info(tr("ยังไม่มีข้อมูลตารางเรียน", "No timetable data is available."))
    export_rows = schedules
    export_label = "รวมทุกห้อง" if not is_english else "master"
else:
    st.subheader(tr("ช่วยจัดตารางอัตโนมัติ", "Auto-schedule"))
    st.caption(tr(
        "ระบบตั้งคาบรายวิชาต่อสัปดาห์ตามหลักสูตรสถานศึกษา ปี 2568 ไว้ให้ "
        "รวมกิจกรรมแนะแนว ลูกเสือ และชุมนุม พร้อมดึงครูรายวิชาจากตารางเดิม "
        "วิชาที่ไม่มีชื่อครูในตารางเดิมจะเว้นผู้สอนไว้ ไม่เดาสุ่ม ปรับจำนวนได้ก่อนจำลอง",
        "Weekly subject periods are prefilled from the school's 2025 curriculum, "
        "including guidance, scouts, and clubs. Teachers are taken from the existing "
        "timetable; missing assignments are left blank rather than guessed.",
    ))
    st.info(tr(
        "คาบ 6 ใช้เฉพาะพละวันพุธทุกชั้น (จัดกิจกรรมร่วมตามสายชั้น) "
        "และลูกเสือวันพฤหัสบดีคาบ 6 สำหรับ ป.1–ป.3 "
        "(ป.4–ป.6 ลูกเสือคาบ 5) คาบแรกจะเลือกคณิตศาสตร์หรือภาษาไทยก่อน "
        "และใช้วิชาอื่นเฉพาะเมื่อจำเป็นเพื่อจัดตารางให้ครบโดยไม่ให้ครูชน "
        "กิจกรรมเพื่อสังคมและสาธารณประโยชน์ 10 ชั่วโมง/ปีไม่รวมในตารางรายสัปดาห์",
        "Period 6 is reserved for Wednesday PE (joint activity by grade band) and scouts on Thursday "
        "period 6 for P1–P3 (P4–P6 scouts remain on period 5). First period is "
        "prioritized for mathematics or Thai; another subject is used only when needed "
        "to complete the timetable without teacher conflicts. The 10 annual social-service "
        "hours are not included.",
    ))
    grades = list(GRADE_MAPPING)
    rooms_by_grade = {grade: set() for grade in grades}
    for grade, room_value in profile_classes:
        if grade in rooms_by_grade:
            rooms_by_grade[grade].add(str(room_value or ""))
    for row in schedules:
        if row["grade"] in rooms_by_grade:
            rooms_by_grade[row["grade"]].add(str(row.get("room", "") or ""))
    room_options = {
        grade: sorted((rooms - {""}) or {""})
        for grade, rooms in rooms_by_grade.items()
    }
    preview_key = "schedule_auto_preview"
    target_classes = set()

    with st.form("schedule_auto_form", border=True):
        class_configs = []
        for index, grade in enumerate(grades, start=1):
            if len(room_options[grade]) == 1:
                room = room_options[grade][0]
            else:
                room = st.selectbox(
                    f'{tr("ห้องเรียน", "Classroom")} {grade}',
                    room_options[grade],
                    format_func=lambda value: f"ห้อง {value}" if value else "ไม่ระบุห้อง",
                    key=f"schedule_auto_room_{index}",
                )
            target_classes.add((grade, room))
            grade_number = index
            curriculum_loads = curriculum_weekly_loads(grade_number)
            subject_names = list(dict.fromkeys([
                *curriculum_loads,
                *(
                    subject_name.split(" ", 1)[-1]
                    for _, subject_name, _ in SUBJECT_LIST
                    if subject_name.split(" ", 1)[-1] != "สุขศึกษาฯ"
                ),
            ]))
            teacher_loads = curriculum_teacher_loads(
                grade_number,
                grade,
                room,
                schedules,
                subject_names,
            )
            teacher_summaries = []
            for subject in subject_names:
                assigned_teachers = teacher_loads.get(subject, {})
                teacher_summaries.append(
                    ", ".join(
                        f"{teacher} ({count})"
                        for teacher, count in sorted(assigned_teachers.items())
                    )
                    or tr("ยังไม่มีชื่อครูในตารางเดิม", "Not assigned in existing timetable")
                )
            workload = pd.DataFrame(
                {
                    tr("รายวิชา", "Subject"): subject_names,
                    tr("คาบ/สัปดาห์", "Periods/week"): [
                        curriculum_loads.get(subject, 0)
                        for subject in subject_names
                    ],
                    tr("ครูจากตารางเดิม", "Teachers from existing timetable"): teacher_summaries,
                }
            )
            edited_workload = st.data_editor(
                workload,
                hide_index=True,
                width="stretch",
                num_rows="fixed",
                key=f"schedule_auto_load_curriculum_{language}_{grade_number}",
                column_config={
                    tr("รายวิชา", "Subject"): st.column_config.TextColumn(disabled=True),
                    tr("คาบ/สัปดาห์", "Periods/week"): st.column_config.NumberColumn(
                        min_value=0,
                        max_value=PERIOD_COUNT * len(WEEKDAYS),
                        step=1,
                        format="%d",
                    ),
                    tr("ครูจากตารางเดิม", "Teachers from existing timetable"):
                        st.column_config.TextColumn(disabled=True),
                },
                disabled=[
                    tr("รายวิชา", "Subject"),
                    tr("ครูจากตารางเดิม", "Teachers from existing timetable"),
                ],
            )
            weekly_total = sum(
                int(value or 0)
                for value in edited_workload[tr("คาบ/สัปดาห์", "Periods/week")]
            )
            curriculum_total = sum(curriculum_loads.values())
            st.caption(
                tr(
                    f"รวม {weekly_total} คาบ/สัปดาห์ · หลักสูตรกำหนด {curriculum_total} "
                    f"คาบ/สัปดาห์ · ว่าง {PERIOD_COUNT * len(WEEKDAYS) - weekly_total} คาบ",
                    f"{weekly_total} periods/week · curriculum: {curriculum_total} · "
                    f"{PERIOD_COUNT * len(WEEKDAYS) - weekly_total} unassigned",
                )
            )
            class_configs.append(
                {
                    "grade": grade,
                    "grade_number": grade_number,
                    "room": room,
                    "teacher_loads": teacher_loads,
                    "weekly_loads": dict(
                        zip(
                            edited_workload[tr("รายวิชา", "Subject")],
                            edited_workload[tr("คาบ/สัปดาห์", "Periods/week")],
                        )
                    ),
                }
            )

        simulate_clicked = st.form_submit_button(
            tr("จำลองตารางอัตโนมัติ", "Preview auto-schedule"),
            type="primary",
            icon=":material/auto_awesome:",
        )
        save_clicked = st.form_submit_button(
            tr("สร้างและบันทึกตารางอัตโนมัติ", "Create and save timetable"),
            icon=":material/save:",
        )

    if simulate_clicked or save_clicked:
        try:
            planned_schedules = generate_auto_schedule(
                class_configs,
            )
            if save_clicked:
                conflicts = save_auto_schedule(
                    planned_schedules,
                    target_classes,
                    "",
                    "",
                )
                if conflicts:
                    conflict_lines = [
                        f'{weekday_labels()[item["weekday"] - 1]} {tr("คาบ", "Period")} {item["period"]}: '
                        f'{item["teacher"]} สอนชนกับ ' +
                        ", ".join(
                            f"{grade}" + (f" ห้อง {room}" if room else "")
                            for grade, room in item["classes"]
                        )
                        for item in conflicts
                    ]
                    st.error("บันทึกไม่ได้ เนื่องจากครูมีตารางสอนชนกับชั้น/ห้องอื่น:\n\n" +
                             "\n".join(f"- {line}" for line in conflict_lines))
                else:
                    st.session_state.pop(preview_key, None)
                    st.session_state["schedule_notice"] = "สร้างและบันทึกตารางอัตโนมัติแล้ว"
                    st.rerun()
            else:
                st.session_state[preview_key] = planned_schedules
                st.success(
                    f"จำลองตารางสำเร็จ จำนวน {len(planned_schedules)} คาบ "
                    "ระบบกระจายวิชาให้สมดุลและหลีกเลี่ยงวิชาเดิมติดกัน "
                    "ตรวจตัวอย่างด้านล่าง แล้วจึงบันทึกได้"
                )
        except (KeyError, TypeError, ValueError) as error:
            st.session_state.pop(preview_key, None)
            st.error(f'{tr("จำลองตารางไม่สำเร็จ", "Could not create timetable")}: {error}')
        except sqlite3.Error as error:
            st.error(f'{tr("บันทึกตารางอัตโนมัติไม่สำเร็จ", "Could not save timetable")}: {error}')

    if preview_key in st.session_state:
        preview = st.session_state[preview_key]
        st.subheader(tr("ตัวอย่างตารางที่จำลองได้", "Auto-schedule preview"))
        st.caption(tr(
            "ใช้ครูตามรายวิชาจากตารางเดิม วิชาที่ไม่มีข้อมูลครูจะเว้นชื่อไว้ "
            "ลากวิชาเพื่อย้ายหรือสลับได้ กรอบสีเขียวคือช่องที่ย้ายได้ "
            "และกดบันทึกตัวอย่างเมื่อตรวจสอบเรียบร้อย",
            "Teachers are assigned from the existing subject timetable. Drag lessons to move or swap; "
            "green borders show valid destinations. Save the preview after reviewing it.",
        ))
        preview_base = [
            row
            for row in schedules
            if (row["grade"], str(row.get("room", "") or "")) not in target_classes
        ]
        move_error = st.session_state.pop("schedule_preview_move_error", None)
        if move_error:
            st.error(move_error)
        for grade, room in sorted(target_classes):
            class_preview = [
                row for row in preview
                if (row["grade"], row["room"]) == (grade, room)
            ]
            preview_context = [*preview_base, *preview]
            with st.expander(
                f"{grade}" + (f" · ห้อง {room}" if room else ""),
                expanded=len(target_classes) == 1,
            ):
                preview_grid = draggable_lesson_grid(
                    class_preview,
                    grade,
                    room,
                    break_after,
                    start_time,
                    period_minutes,
                    break_minutes,
                    preview_context,
                    key_suffix="preview",
                )
                if preview_grid.move:
                    move = preview_grid.move
                    try:
                        moved_schedules, conflicts = move_schedule_entries(
                            preview_context,
                            grade,
                            room,
                            move["from_weekday"],
                            move["from_period"],
                            move["to_weekday"],
                            move["to_period"],
                        )
                        if conflicts:
                            st.session_state["schedule_preview_move_error"] = tr(
                                "ย้ายไม่ได้ เพราะจะทำให้ครูสอนชนกัน",
                                "Move rejected because it would create a teacher conflict.",
                            )
                        else:
                            st.session_state[preview_key] = [
                                row
                                for row in moved_schedules
                                if (
                                    row["grade"],
                                    str(row.get("room", "") or ""),
                                ) in target_classes
                            ]
                            st.rerun()
                    except (KeyError, TypeError, ValueError) as error:
                        st.session_state["schedule_preview_move_error"] = str(error)
                        st.rerun()
        if st.button(
            tr("บันทึกตารางจำลอง", "Save preview timetable"),
            type="primary",
            icon=":material/save:",
            key="schedule_save_preview",
        ):
            try:
                conflicts = save_auto_schedule(preview, target_classes, "", "")
                if conflicts:
                    st.error(
                        tr(
                            "บันทึกไม่ได้ เพราะมีครูสอนชนกับตารางห้องอื่น",
                            "Could not save because a teacher conflicts with another class.",
                        )
                    )
                else:
                    st.session_state.pop(preview_key, None)
                    st.session_state["schedule_notice"] = tr(
                        "บันทึกตารางจำลองแล้ว",
                        "Preview timetable saved.",
                    )
                    st.rerun()
            except (KeyError, TypeError, ValueError, sqlite3.Error) as error:
                st.error(
                    f'{tr("บันทึกตารางจำลองไม่สำเร็จ", "Could not save preview")}: {error}'
                )
    st.info(tr(
        "ตารางอัตโนมัติสร้างสำหรับ ป.1–ป.6 และแทนที่ตารางเดิมของห้องเหล่านี้เมื่อบันทึก "
        "ตรวจรายชื่อห้อง ตัวอย่าง และครูประจำวิชาให้ครบก่อนบันทึก "
        "ครูที่ไม่มีข้อมูลในตารางเดิมจะยังว่าง",
        "Auto-scheduling targets grades P1–P6 and replaces their existing timetables when saved. "
        "Review classes, previews, and subject teacher assignments before saving. "
        "Teachers missing from the current timetable remain unassigned.",
    ))
    export_rows = []
    export_label = "จำลอง"

if export_rows:
    st.subheader(tr("พิมพ์และส่งออก", "Print and export"))
    excel_bytes = create_excel(export_rows)
    if class_selection == "class":
        print_title = f'{tr("ตารางเรียน", "Class timetable")} {grade}' + (
            f' {tr("ห้อง", "Room")} {room}' if room else ""
        )
    elif class_selection == "teacher":
        print_title = f'{tr("ตารางสอน", "Teacher timetable")} {teacher}'
    elif class_selection == "all_teachers":
        print_title = tr("ตารางสอนครูทั้งหมด", "All teacher timetables")
    else:
        print_title = tr("ตารางเรียนรวมทุกห้อง", "Master timetable")
    print_html = create_print_html(
        export_rows,
        print_title,
        config.get("school_name", ""),
        get_active_academic_year(),
        break_after,
        start_time,
        period_minutes,
        break_minutes,
    )
    export_col, print_col = st.columns(2)
    with export_col:
        st.download_button(
            tr("ดาวน์โหลด Excel", "Download Excel"),
            data=excel_bytes,
            file_name=f'{tr("ตารางสอน", "Timetable")}_{export_label}.xlsx',
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            icon=":material/download:",
        )
    with print_col:
        st.download_button(
            tr("ดาวน์โหลดหน้าเว็บสำหรับพิมพ์", "Download printable HTML"),
            data=print_html.encode("utf-8"),
            file_name=f'{tr("ตารางสอน", "Timetable")}_{export_label}.html',
            mime="text/html",
            icon=":material/print:",
        )
    st.caption(tr(
        "สามารถเปิดไฟล์ HTML แล้วเลือก “พิมพ์ / บันทึกเป็น PDF” ได้",
        "Open the HTML file and choose “Print / Save as PDF”.",
    ))
    st.iframe(
        print_html.replace(
            '<button type="button" onclick="window.print()">พิมพ์ / บันทึกเป็น PDF</button>',
            f"<span>{tr('ดาวน์โหลดไฟล์ HTML แล้วเปิดเพื่อพิมพ์', 'Download and open the HTML file to print.')}</span>",
        ),
        height=640,
    )
else:
    if class_selection != "cover":
        st.info(tr("ยังไม่มีรายการในตารางนี้", "No timetable entries to export."))
