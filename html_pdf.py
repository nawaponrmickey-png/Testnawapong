"""Render standalone report HTML to a print-ready PDF with headless Chrome."""

from pathlib import Path
import shutil
import subprocess
import tempfile


def render_html_pdf(html: str) -> bytes:
    chrome = next(
        (shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser", "google-chrome-stable") if shutil.which(name)),
        None,
    )
    if not chrome:
        raise RuntimeError("ไม่พบ Google Chrome หรือ Chromium สำหรับจัดหน้า PDF บนเครื่องที่รันระบบ")

    with tempfile.TemporaryDirectory(prefix="pp5-pdf-") as temp_dir:
        temp_path = Path(temp_dir)
        html_path = temp_path / "report.html"
        pdf_path = temp_path / "report.pdf"
        profile_path = temp_path / "chrome-profile"
        html_path.write_text(html, encoding="utf-8")
        command = [
            chrome,
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=5000",
            f"--user-data-dir={profile_path}",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ใช้เวลาสร้าง PDF นานเกินกำหนด") from exc
        if completed.returncode != 0 or not pdf_path.is_file():
            details = (completed.stderr or completed.stdout or "").strip()[-1200:]
            raise RuntimeError(f"Chrome สร้าง PDF ไม่สำเร็จ {details}".strip())
        pdf_bytes = pdf_path.read_bytes()
        if not pdf_bytes.startswith(b"%PDF"):
            raise RuntimeError("ไฟล์ที่สร้างไม่ใช่ PDF ที่สมบูรณ์")
        return pdf_bytes
