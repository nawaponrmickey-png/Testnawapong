def check_attendance_eligibility(attended: int, total: int) -> tuple[float, bool]:
    if total <= 0:
        return 0.0, False
    rate = round((attended / total) * 100, 2)
    return rate, rate >= 80.0

def calculate_grade(total_score: float, eligible_for_exam: bool, submitted_all: bool) -> str:
    if not eligible_for_exam:
        return "มส"
    if not submitted_all:
        return "ร"

    if total_score >= 80: return "4"
    elif total_score >= 75: return "3.5"
    elif total_score >= 70: return "3"
    elif total_score >= 65: return "2.5"
    elif total_score >= 60: return "2"
    elif total_score >= 55: return "1.5"
    elif total_score >= 50: return "1"
    else: return "0"

def get_trait_label(level: int) -> str:
    mapping = {3: "ดีเยี่ยม (3)", 2: "ดี (2)", 1: "ผ่าน (1)", 0: "ไม่ผ่าน (0)"}
    return mapping.get(level, "ไม่ผ่าน (0)")
