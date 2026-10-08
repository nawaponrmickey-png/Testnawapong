"""Shared scoring levels and summary rules for learner assessments."""

LEVEL_LABELS = {
    3: "ดีเยี่ยม",
    2: "ดี",
    1: "ผ่าน",
    0: "ไม่ผ่าน",
}
LEVEL_OPTIONS = ("ยังไม่ประเมิน", "ไม่ผ่าน", "ผ่าน", "ดี", "ดีเยี่ยม")
LABEL_TO_LEVEL = {label: level for level, label in LEVEL_LABELS.items()}
LEVEL_TO_LABEL = {level: f"{label} ({level})" for level, label in LEVEL_LABELS.items()}
LEVEL_OPTION_LABELS = ("ยังไม่ประเมิน",) + tuple(LEVEL_TO_LABEL[level] for level in (0, 1, 2, 3))
DISPLAY_TO_LEVEL = {label: level for level, label in LEVEL_TO_LABEL.items()}


def overall_level(levels):
    """Summarize complete level ratings; return None while any rating is missing."""
    values = []
    for level in levels:
        if level is None or level == "":
            return None
        try:
            values.append(int(level))
        except (TypeError, ValueError):
            return None
    if not values:
        return None
    if any(level < 0 or level > 3 for level in values):
        return None
    if 0 in values:
        return 0

    excellent = sum(level == 3 for level in values)
    good_or_better = sum(level >= 2 for level in values)
    if good_or_better == len(values):
        return 3 if excellent > len(values) / 2 else 2
    if good_or_better > len(values) / 2:
        return 2
    return 1


def percentage_or_default(value, default=50):
    """Read a stored percent whether it is saved as 50 or as the text '50%'."""
    try:
        percentage = float(str(value).strip().removesuffix("%"))
    except (TypeError, ValueError):
        return default
    return min(100, max(0, percentage))
