import re
import sqlite3
import tempfile
from datetime import date
from pathlib import Path


def ensure_daily_database_backup(
    database_path: Path,
    academic_year: int,
    *,
    today: date | None = None,
    retention_days: int = 30,
) -> Path | None:
    """Create one automatic SQLite backup per day and retain only recent automatic copies."""
    database_path = Path(database_path)
    backup_date = today or date.today()
    backup_dir = database_path.parent
    backup_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"สำรองระบบปพ5_อัตโนมัติ_{int(academic_year)}_"
        f"{backup_date:%Y%m%d}.db"
    )
    backup_path = backup_dir / filename
    if backup_path.is_file():
        return None

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{filename}.",
            suffix=".tmp",
            dir=backup_dir,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        with sqlite3.connect(database_path) as source, sqlite3.connect(
            temporary_path
        ) as backup:
            source.backup(backup)
        temporary_path.replace(backup_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    if retention_days > 0:
        backup_pattern = re.compile(
            rf"^สำรองระบบปพ5_อัตโนมัติ_{int(academic_year)}_(\d{{8}})\.db$"
        )
        backups = []
        for candidate in backup_dir.iterdir():
            match = backup_pattern.fullmatch(candidate.name)
            if match and candidate.is_file():
                backups.append((match.group(1), candidate))
        backups.sort(reverse=True)
        for _, old_backup in backups[retention_days:]:
            old_backup.unlink()

    return backup_path
