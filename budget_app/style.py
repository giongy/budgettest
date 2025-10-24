from PyQt6.QtGui import QColor

from .config import load_style_settings

_STYLE = load_style_settings()

# Column widths
CATEGORY_COLUMN_WIDTH = _STYLE["category_column_width"]
PERIOD_COLUMN_WIDTH = _STYLE["period_column_width"]
NUMERIC_COLUMN_WIDTH = _STYLE["numeric_column_width"]
MIN_COLUMN_WIDTH = _STYLE["min_column_width"]

# Row styling
MAIN_CATEGORY_BG = QColor(_STYLE["main_category_bg"])
DIFF_POSITIVE_COLOR = QColor(_STYLE["diff_positive_color"])
DIFF_NEGATIVE_COLOR = QColor(_STYLE["diff_negative_color"])

# Font settings
UI_FONT_FAMILY = _STYLE["ui_font_family"]
UI_BASE_FONT_SIZE = _STYLE["ui_base_font_size"]
UI_BOLD_FONT_SIZE = _STYLE["ui_bold_font_size"]
DIFF_FONT_SIZE = _STYLE["diff_font_size"]
SUMMARY_FONT_SIZE = _STYLE["summary_font_size"]

# Window geometry
WINDOW_SCALE_RATIO = _STYLE["window_scale_ratio"]

# Chart settings
CHART_HEIGHT = _STYLE["chart_height"]
