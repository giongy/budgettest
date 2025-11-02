from PyQt6.QtWidgets import QComboBox, QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication, QHeaderView, QStyleOptionHeader, QToolTip
from PyQt6.QtWidgets import QTreeView
from pathlib import Path

from typing import Any, Callable

from PyQt6.QtGui import QStandardItem, QFont, QBrush, QColor, QCursor, QPainter, QPixmap, QPen, QHelpEvent
from PyQt6.QtCore import Qt, QRect, QSize, QEvent, QTimer, QPoint

from .config import PERIOD_CHOICES
from .style import UI_FONT_FAMILY, UI_BASE_FONT_SIZE, UI_BOLD_FONT_SIZE, SUMMARY_FONT_SIZE


def make_item(text="", editable=False, meta=None, bold=False, color=None):
    item = QStandardItem(str(text))
    item.setEditable(editable)
    font = QFont(UI_FONT_FAMILY, UI_BASE_FONT_SIZE)
    if bold:
        font.setBold(True)
        font.setPointSize(UI_BOLD_FONT_SIZE)
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
        self.button_size = QSize(14, 14)
        self.margin = 2
        self._pressed = None
        icon_path = Path(__file__).resolve().parent.parent / "pari.png"
        self.icon_pixmap = QPixmap(str(icon_path)) if icon_path.exists() else QPixmap()

    def _is_main_category_budget(self, index) -> bool:
        if not index.isValid():
            return False
        meta = index.data(Qt.ItemDataRole.UserRole)
        if not meta or not isinstance(meta, tuple) or meta[0] != "budget":
            return False
        parent_index = index.parent()
        if not parent_index.isValid():
            return False
        label_index = parent_index.siblingAtColumn(0)
        if not label_index.isValid():
            label_index = parent_index
        parent_meta = label_index.data(Qt.ItemDataRole.UserRole)
        if not parent_meta or not isinstance(parent_meta, tuple):
            return False
        if parent_meta[0] != "category_label":
            return False
        depth = parent_meta[2] if len(parent_meta) > 2 else 0
        return (depth or 0) == 0

    def paint(self, painter, option, index):
        meta = index.data(Qt.ItemDataRole.UserRole)
        if not meta or not isinstance(meta, tuple) or meta[0] != "budget":
            super().paint(painter, option, index)
            return
        if self._is_main_category_budget(index):
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
            fill_color = QColor("#fef3e0")
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

    def editorEvent(self, event, model, option, index):
        meta = index.data(Qt.ItemDataRole.UserRole)
        if not meta or not isinstance(meta, tuple) or meta[0] != "budget":
            return super().editorEvent(event, model, option, index)
        if self._is_main_category_budget(index):
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


class CategoryDetailDelegate(QStyledItemDelegate):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.view = parent
        self.view.setMouseTracking(True)
        self.view.viewport().setMouseTracking(True)
        self.callback = callback
        self.button_size = QSize(18, 18)
        self.margin = 3
        self._pressed = None
        icon_path = Path(__file__).resolve().parent.parent / "dettagli.png"
        if icon_path.exists():
            self.icon_pixmap = QPixmap(str(icon_path))
        else:
            style = self.view.style() if self.view else QApplication.style()
            icon = style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
            self.icon_pixmap = icon.pixmap(self.button_size) if not icon.isNull() else QPixmap()

    def _is_category_row(self, index):
        if not index.isValid():
            return False
        meta = index.data(Qt.ItemDataRole.UserRole)
        if not meta or not isinstance(meta, tuple) or meta[0] != "category_label":
            return False
        depth = meta[2] if len(meta) > 2 else 0
        return (depth or 0) > 0

    def paint(self, painter, option, index):
        if not self._is_category_row(index):
            super().paint(painter, option, index)
            return
        text_option = QStyleOptionViewItem(option)
        self.initStyleOption(text_option, index)
        reserve = self.button_size.width() + self.margin * 2
        text_option.rect = text_option.rect.adjusted(0, 0, -reserve, 0)
        style = self.view.style() if self.view else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, text_option, painter, self.view)

        button_rect = self._button_rect(option, index, text_option)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._pressed == (index.row(), index.column()):
            fill_color = QColor("#d0d0d0")
        elif option.state & QStyle.StateFlag.State_MouseOver:
            fill_color = QColor("#f0f0f0")
        else:
            fill_color = QColor("#f8f8f8")
        painter.setPen(QPen(QColor("#777777")))
        painter.setBrush(QBrush(fill_color))
        painter.drawRoundedRect(button_rect, 4, 4)
        if not self.icon_pixmap.isNull():
            pixmap = self.icon_pixmap
            if pixmap.size() != button_rect.size():
                pixmap = pixmap.scaled(
                    button_rect.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            icon_x = button_rect.x() + max((button_rect.width() - pixmap.width()) // 2, 0)
            icon_y = button_rect.y() + max((button_rect.height() - pixmap.height()) // 2, 0)
            painter.drawPixmap(icon_x, icon_y, pixmap)
        else:
            painter.drawText(button_rect, Qt.AlignmentFlag.AlignCenter, "i")
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if not self._is_category_row(index):
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


class DividerDelegate(QStyledItemDelegate):
    def __init__(self, parent, *, line_color: QColor | Qt.GlobalColor = Qt.GlobalColor.black, line_width: int = 2):
        super().__init__(parent)
        self.line_color = QColor(line_color)
        self.line_width = line_width

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        painter.save()
        pen = QPen(self.line_color)
        pen.setWidth(self.line_width)
        painter.setPen(pen)
        top = option.rect.top()
        bottom = option.rect.bottom()
        x = option.rect.left()
        painter.drawLine(x, top, x, bottom)
        painter.restore()


class SummaryHeaderView(QHeaderView):
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._summary: dict[int, Any] = {}
        self._summary_height = 0
        self._summary_font = QFont(UI_FONT_FAMILY, SUMMARY_FONT_SIZE)
        self._summary_font.setBold(False)
        self.setSectionsClickable(True)
        self._highlighted_sections: set[int] = set()
        self._highlight_pen = QPen(QColor('#673BFF'))
        self._highlight_pen.setWidth(2)
        self._highlight_pen.setCosmetic(True)
        self._toggle_column: int | None = None
        self._toggle_handler: Callable[[], None] | None = None
        self._toggle_state = False
        self._toggle_rect = QRect()
        self._summary_line_height = SUMMARY_FONT_SIZE + 1
        QToolTip.setFont(QFont(UI_FONT_FAMILY, SUMMARY_FONT_SIZE + 2))
        app = QApplication.instance()
        if app is not None:
            tooltip_style = "QToolTip { background-color: #000000; color: #FFFFFF; border: 2px solid #925ee6; font-size: %dpt; }" % (SUMMARY_FONT_SIZE + 2)
            existing = app.styleSheet() or ""
            if tooltip_style not in existing:
                if existing:
                    app.setStyleSheet(existing + "\n" + tooltip_style)
                else:
                    app.setStyleSheet(tooltip_style)
        self.setMouseTracking(True)
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.setInterval(300)
        self._tooltip_timer.timeout.connect(self._show_pending_tooltip)
        self._pending_tooltip_index: int | None = None
        self._pending_tooltip_pos = QPoint()
        self._tooltip_visible = False

    def set_summary(self, summary: dict[int, Any] | None):
        self._summary = summary or {}
        self._recalculate_summary_height()
        model = self.model()
        if model is not None:
            column_count = model.columnCount()
            for logical_index in range(column_count):
                tooltip = self._tooltip_text_for_index(logical_index)
                model.setHeaderData(
                    logical_index,
                    Qt.Orientation.Horizontal,
                    tooltip,
                    Qt.ItemDataRole.ToolTipRole,
                )
        self.updateGeometry()
        self.viewport().update()

    def configure_toggle(
        self,
        column: int | None,
        state: bool,
        handler: Callable[[], None] | None = None,
    ):
        self._toggle_column = column
        self._toggle_state = state
        if handler is not None:
            self._toggle_handler = handler
        self._toggle_rect = QRect()
        self.updateGeometry()
        self.viewport().update()

    def _recalculate_summary_height(self):
        max_lines = 0
        for entry in self._summary.values():
            if isinstance(entry, dict):
                lines = entry.get('lines')
                if lines:
                    max_lines = max(max_lines, len(lines))
            elif entry is not None:
                max_lines = max(max_lines, 1)
        if max_lines <= 0:
            self._summary_height = 0
        else:
            padding = 8
            self._summary_height = max_lines * self._summary_line_height + padding

    def _tooltip_text_for_index(self, logical_index: int) -> str | None:
        entry = self._summary.get(logical_index)
        if isinstance(entry, dict):
            return entry.get('tooltip')
        if isinstance(entry, tuple) and len(entry) == 3:
            return str(entry[0])
        if isinstance(entry, str):
            return entry
        return None

    def _show_pending_tooltip(self):
        if self._pending_tooltip_index is None:
            return
        cursor_local = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(cursor_local):
            return
        current_index = self.logicalIndexAt(cursor_local)
        if current_index != self._pending_tooltip_index:
            return
        tooltip = self._tooltip_text_for_index(self._pending_tooltip_index)
        if not tooltip:
            return
        self._pending_tooltip_pos = cursor_local
        global_pos = self.mapToGlobal(self._pending_tooltip_pos)
        QToolTip.showText(global_pos, tooltip, self)
        self._tooltip_visible = True

    def set_highlighted_sections(self, sections: set[int] | list[int] | tuple[int, ...] | None):
        new_set = set(sections or [])
        if new_set == self._highlighted_sections:
            return
        self._highlighted_sections = new_set
        self.viewport().update()

    def visible_table_width(self) -> int:
        if self.count() == 0:
            return self.viewport().width()
        last = self.count() - 1
        section_right = self.sectionViewportPosition(last) + self.sectionSize(last)
        section_right = max(0, section_right)
        return int(min(self.viewport().width(), section_right))

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        pen = QPen(QColor('#02070F'))
        pen.setWidth(2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        y = self.viewport().height() - 1
        painter.drawLine(0, y, self.visible_table_width(), y)
        painter.end()

    def sizeHint(self):
        base = super().sizeHint()
        if not self._summary:
            return base
        return QSize(base.width(), base.height() + self._summary_height)

    def sectionSizeFromContents(self, logicalIndex: int):
        base = super().sectionSizeFromContents(logicalIndex)
        if not self._summary:
            return base
        return QSize(base.width(), base.height() + self._summary_height)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        logical_index = self.logicalIndexAt(pos)
        tooltip = self._tooltip_text_for_index(logical_index)
        if tooltip:
            if self._tooltip_visible and logical_index == self._pending_tooltip_index:
                self._pending_tooltip_pos = pos
                QToolTip.showText(self.mapToGlobal(pos), tooltip, self)
            else:
                self._tooltip_visible = False
                self._pending_tooltip_index = logical_index
                self._pending_tooltip_pos = pos
                self._tooltip_timer.stop()
                self._tooltip_timer.start()
        else:
            self._tooltip_timer.stop()
            self._pending_tooltip_index = None
            if self._tooltip_visible:
                QToolTip.hideText()
                self._tooltip_visible = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
            logical_index = self.logicalIndexAt(pos)
            if (
                self._toggle_column is not None
                and logical_index == self._toggle_column
                and not self._toggle_rect.isNull()
                and self._toggle_rect.contains(pos)
            ):
                if self._toggle_handler:
                    self._toggle_handler()
                event.accept()
                return
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        self._tooltip_timer.stop()
        self._pending_tooltip_index = None
        if self._tooltip_visible:
            QToolTip.hideText()
            self._tooltip_visible = False
        if self._toggle_rect and not self._toggle_rect.isNull():
            self._toggle_rect = QRect()
        super().leaveEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            return True
        return super().event(event)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int):
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = rect
        option.section = logicalIndex
        option.textAlignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        try:
            header_text = self.model().headerData(logicalIndex, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        except Exception:
            header_text = None
        option.text = str(header_text) if header_text is not None else option.text
        self.style().drawControl(QStyle.ControlElement.CE_HeaderSection, option, painter, self)
        base_size = self.sectionSizeFromContents(logicalIndex)
        label_h = min(base_size.height(), rect.height(), SUMMARY_FONT_SIZE + 8)
        label_rect = QRect(rect.left(), rect.top(), rect.width(), label_h)
        label_opt = QStyleOptionHeader(option)
        label_opt.rect = label_rect
        self.style().drawControl(QStyle.ControlElement.CE_HeaderLabel, label_opt, painter, self)

        summary_entry = self._summary.get(logicalIndex)
        is_toggle_section = self._toggle_column is not None and logicalIndex == self._toggle_column
        summary_rect = None
        if is_toggle_section:
            self._toggle_rect = QRect()
        if summary_entry:
            summary_h = rect.height() - label_h
            if summary_h > 2:
                summary_rect = QRect(rect.left(), rect.bottom() - summary_h + 1, rect.width(), summary_h - 1)
                if is_toggle_section:
                    self._toggle_rect = QRect(summary_rect)
                painter.save()
                divider_pen = QPen(QColor('#02070F'))
                divider_pen.setWidth(1)
                divider_pen.setCosmetic(True)

                def _to_brush(value: Any) -> QBrush | None:
                    if value is None:
                        return None
                    if isinstance(value, QBrush):
                        return value
                    if isinstance(value, QColor):
                        return QBrush(value)
                    if isinstance(value, str):
                        color = QColor(value)
                        if color.isValid():
                            return QBrush(color)
                    return None

                def _to_color(value: Any, default: QColor) -> QColor:
                    if isinstance(value, QColor):
                        return value
                    if isinstance(value, str):
                        color = QColor(value)
                        if color.isValid():
                            return color
                    return default

                if isinstance(summary_entry, dict):
                    alignment = int(
                        summary_entry.get(
                            'alignment',
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        )
                    )
                    overall_brush = _to_brush(summary_entry.get('background'))
                    if overall_brush:
                        painter.fillRect(summary_rect, overall_brush)
                    else:
                        painter.fillRect(summary_rect, QBrush(QColor('#E8EAED')))
                    painter.setPen(divider_pen)
                    painter.setFont(self._summary_font)
                    lines = summary_entry.get('lines') or []
                    if lines:
                        line_count = len(lines)
                        available_height = summary_rect.height()
                        base_height = max(1, available_height // line_count)
                        remainder = available_height - base_height * line_count
                        current_top = summary_rect.top()
                        for idx, line in enumerate(lines):
                            line_height = base_height + (1 if idx < remainder else 0)
                            line_rect = QRect(summary_rect.left(), current_top, summary_rect.width(), line_height)
                            line_brush = _to_brush(line.get('bg'))
                            if line_brush:
                                painter.fillRect(line_rect, line_brush)
                            text = str(line.get('text', ''))
                            fg_color = _to_color(line.get('fg'), QColor('#111'))
                            line_font_size = line.get('font_size')
                            custom_font = None
                            if line_font_size is not None:
                                try:
                                    parsed_size = int(line_font_size)
                                except (TypeError, ValueError):
                                    parsed_size = 0
                                if parsed_size > 0:
                                    custom_font = QFont(self._summary_font)
                                    custom_font.setPointSize(parsed_size)
                            painter.setFont(custom_font or self._summary_font)
                            painter.setPen(QPen(fg_color))
                            painter.drawText(line_rect.adjusted(6, 0, -4, 0), alignment, text)
                            current_top += line_height
                        painter.setFont(self._summary_font)
                    else:
                        painter.setPen(QPen(QColor('#111')))
                        painter.drawText(summary_rect.adjusted(6, 0, -4, 0), alignment, '')
                else:
                    if isinstance(summary_entry, tuple):
                        if len(summary_entry) == 3:
                            text, brush, alignment = summary_entry
                        else:
                            text, brush = summary_entry
                            alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    else:
                        text = str(summary_entry)
                        brush = QBrush(QColor('#E8EAED'))
                        alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    painter.fillRect(summary_rect, brush)
                    painter.setPen(divider_pen)
                    painter.setPen(QPen(QColor('#111')))
                    painter.setFont(self._summary_font)
                    painter.drawText(summary_rect.adjusted(6, 0, -4, 0), int(alignment), text)
            painter.restore()
        elif is_toggle_section:
            self._toggle_rect = QRect()

        if is_toggle_section and summary_rect is not None and self._toggle_state:
            painter.save()
            outline_pen = QPen(QColor('#4A6CF0'))
            outline_pen.setWidth(2)
            outline_pen.setCosmetic(True)
            painter.setPen(outline_pen)
            painter.drawRoundedRect(summary_rect.adjusted(2, 1, -2, -1), 4, 4)
            painter.restore()

        if logicalIndex in self._highlighted_sections:
            painter.save()
            painter.setPen(self._highlight_pen)
            painter.drawLine(rect.left(), rect.top(), rect.left(), rect.bottom())
            painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
            painter.restore()
class BudgetTreeView(QTreeView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._highlighted_columns: set[int] = set()
        self._highlight_pen = QPen(QColor('#673BFF'))
        self._highlight_pen.setWidth(2)
        self._highlight_pen.setCosmetic(True)

    def highlighted_columns(self) -> set[int]:
        return set(self._highlighted_columns)

    def clear_highlighted_columns(self):
        if not self._highlighted_columns:
            return
        self._highlighted_columns.clear()
        self.viewport().update()

    def toggle_highlight_column(self, column: int) -> bool:
        if column in self._highlighted_columns:
            self._highlighted_columns.remove(column)
            self.viewport().update()
            return False
        self._highlighted_columns.add(column)
        self.viewport().update()
        return True

    def set_highlighted_columns(self, columns: set[int] | list[int] | tuple[int, ...] | None):
        new_set = set(columns or [])
        if new_set == self._highlighted_columns:
            return
        self._highlighted_columns = new_set
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        pen = QPen(QColor('#02070F'))
        pen.setWidth(2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        header = self.header()
        if header and hasattr(header, "visible_table_width"):
            line_end = int(getattr(header, "visible_table_width")())
        else:
            line_end = self.viewport().width()
        painter.drawLine(0, 0, line_end, 0)
        if not self._highlighted_columns:
            painter.end()
            return
        header = self.header()
        if header is None:
            painter.end()
            return
        painter.setPen(self._highlight_pen)
        height = self.viewport().height()
        width_limit = self.viewport().width()
        for column in self._highlighted_columns:
            if self.isColumnHidden(column):
                continue
            x = header.sectionViewportPosition(column)
            size = header.sectionSize(column)
            if size <= 0 or x >= width_limit or (x + size) <= 0:
                continue
            painter.drawLine(x, 0, x, height)
            painter.drawLine(x + size - 1, 0, x + size - 1, height)
        painter.end()
