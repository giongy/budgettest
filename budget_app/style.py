from PyQt6.QtGui import QColor

# Column widths
CATEGORY_COLUMN_WIDTH = 250  # width for category/label column
PERIOD_COLUMN_WIDTH = 60     # width for the period column
NUMERIC_COLUMN_WIDTH = 80    # width for budget/actual numeric columns (adjust to taste)
MIN_COLUMN_WIDTH = 10        # hard floor so small widths like 20 stay effective

# Row styling
MAIN_CATEGORY_BG = QColor("#7FB2F5")  # light blue background for main categories
DIFF_POSITIVE_COLOR = QColor("#97F18D")
DIFF_NEGATIVE_COLOR = QColor("#F18686")

# Font settings
UI_FONT_FAMILY = "Segoe UI"
UI_BASE_FONT_SIZE = 11
UI_BOLD_FONT_SIZE = 12
DIFF_FONT_SIZE = 10
SUMMARY_FONT_SIZE = 10

