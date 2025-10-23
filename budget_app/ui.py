from PyQt6.QtWidgets import QComboBox, QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication
from pathlib import Path

from PyQt6.QtGui import QStandardItem, QFont, QBrush, QColor, QCursor, QPainter, QPixmap
from PyQt6.QtCore import Qt, QRect, QSize, QEvent

from .config import PERIOD_CHOICES


def make_item(text="", editable=False, meta=None, bold=False, color=None):
    item = QStandardItem(str(text))
    item.setEditable(editable)
    font = QFont("Segoe UI", 8)
    if bold:
        font.setBold(True)
        font.setPointSize(9)
    item.setFont(font)
    if color:
        item.setForeground(QBrush(color))
    if meta and isinstance(meta, tuple) and meta[0] == "category_label":
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    else:
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if meta:
        item.setData(meta, Qt.ItemDataRole.UserRole)
    return item

class PeriodDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(PERIOD_CHOICES)
        return combo

    def setEditorData(self, editor, index):
        val = index.model().data(index, Qt.ItemDataRole.EditRole) or ""
        editor.setCurrentText(val if val in PERIOD_CHOICES else PERIOD_CHOICES[0])

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class ButtonDelegate(QStyledItemDelegate):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.view = parent
        self.view.setMouseTracking(True)
        self.view.viewport().setMouseTracking(True)
        self.callback = callback
        self.button_size = QSize(16, 16)
        self.margin = 4
        self._pressed = None
        icon_path = Path(__file__).resolve().parent.parent / "pari.png"
        self.icon_pixmap = QPixmap(str(icon_path)) if icon_path.exists() else QPixmap()

    def paint(self, painter, option, index):
        meta = index.data(Qt.ItemDataRole.UserRole)
        if not meta or not isinstance(meta, tuple) or meta[0] != "budget":
            super().paint(painter, option, index)
            return

        text_option = QStyleOptionViewItem(option)
        self.initStyleOption(text_option, index)
        reserve = self.button_size.width() + self.margin * 2
        if text_option.rect.width() > reserve:
            text_option.rect = text_option.rect.adjusted(0, 0, -reserve, 0)
        else:
            text_option.rect = text_option.rect.adjusted(0, 0, -self.margin, 0)
        style = self.view.style() if self.view else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, text_option, painter, self.view)

        button_rect = self._button_rect(option, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._pressed == (index.row(), index.column()):
            fill_color = QColor("#cbd5f5")
        elif option.state & QStyle.StateFlag.State_MouseOver:
            fill_color = QColor("#e0f2fe")
        else:
            fill_color = QColor("#f3f4f6")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(fill_color))
        painter.drawRoundedRect(button_rect, 3, 3)
        if not self.icon_pixmap.isNull():
            pixmap = self.icon_pixmap
            target_size = button_rect.size()
            if pixmap.size() != target_size:
                pixmap = pixmap.scaled(
                    target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            icon_x = button_rect.x() + max((button_rect.width() - pixmap.width()) // 2, 0)
            icon_y = button_rect.y() + max((button_rect.height() - pixmap.height()) // 2, 0)
            painter.drawPixmap(icon_x, icon_y, pixmap)
        else:
            painter.setPen(QColor(Qt.GlobalColor.black))
            painter.drawText(button_rect, Qt.AlignmentFlag.AlignCenter, "=")
        painter.restore()

    def editorEvent(self, event, model, option, index):
        meta = index.data(Qt.ItemDataRole.UserRole)
        if not meta or not isinstance(meta, tuple) or meta[0] != "budget":
            return super().editorEvent(event, model, option, index)

        button_rect = self._button_rect(option, index)
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        inside = button_rect.contains(pos)

        if event.type() == QEvent.Type.MouseMove:
            if inside:
                self.view.viewport().setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            else:
                self.view.viewport().unsetCursor()
            return False

        if event.type() == QEvent.Type.Leave:
            self.view.viewport().unsetCursor()
            return False

        if event.type() == QEvent.Type.MouseButtonPress and hasattr(event, "button") and event.button() == Qt.MouseButton.LeftButton:
            if inside:
                self._pressed = (index.row(), index.column())
                return True
            self._pressed = None
            return False

        if event.type() == QEvent.Type.MouseButtonRelease and hasattr(event, "button") and event.button() == Qt.MouseButton.LeftButton:
            pressed = self._pressed
            self._pressed = None
            self.view.viewport().unsetCursor()
            if pressed == (index.row(), index.column()) and inside:
                self.callback(index)
                return True
            return False

        if event.type() == QEvent.Type.MouseButtonDblClick and hasattr(event, "button") and event.button() == Qt.MouseButton.LeftButton:
            if inside:
                self.callback(index)
                return True
            return False

        return super().editorEvent(event, model, option, index)

    def _button_rect(self, option: QStyleOptionViewItem, index, prepared_option: QStyleOptionViewItem | None = None) -> QRect:
        if prepared_option is not None:
            item_option = QStyleOptionViewItem(prepared_option)
        else:
            item_option = QStyleOptionViewItem(option)
            self.initStyleOption(item_option, index)

        rect = option.rect
        width = self.button_size.width()
        max_width = rect.width() - self.margin * 2
        if max_width <= 0:
            width = max(self.button_size.width(), rect.width())
        else:
            width = min(self.button_size.width(), max_width)
        available_height = rect.height() - self.margin * 2
        if available_height > 0:
            height = min(self.button_size.height(), available_height)
        else:
            height = min(self.button_size.height(), rect.height())
        height = max(height, 6)
        y = rect.y() + max((rect.height() - height) // 2, 0)

        x = rect.right() - width - self.margin
        min_x = rect.left() + self.margin
        if x < min_x:
            x = min_x

        return QRect(int(x), int(y), width, height)




