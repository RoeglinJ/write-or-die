"""Write-or-Die — a modern Markdown editor with a dangerous-writing mode.

While a session is active the text progressively blurs when you stop typing.
Stay idle too long and the text written *this session* is wiped (earlier
sessions are protected). Keep writing until the session timer runs out and your
text is auto-copied to the clipboard.
"""

from __future__ import annotations

import json
import os
import sys
import time
import re
from datetime import datetime

from PySide6.QtCore import (
    Qt,
    QTimer,
    QSettings,
    QStandardPaths,
    QPropertyAnimation,
    QVariantAnimation,
    QEasingCurve,
    QPoint,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QIcon,
    QPixmap,
    QPainter,
    QPainterPath,
    QColor,
    QBrush,
    QFont,
    QFontDatabase,
    QPen,
    QKeySequence,
    QTextCursor,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QPlainTextEdit,
    QTextEdit,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QGraphicsBlurEffect,
    QGraphicsOpacityEffect,
    QFileDialog,
    QMessageBox,
    QDialog,
    QSpinBox,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QFormLayout,
    QDialogButtonBox,
    QWidgetAction,
)

APP_ORG = "WriteOrDie"
APP_NAME = "Editor"
APP_ID = "writeordie.editor"

MAX_BLUR = 16.0  # px at the moment of deletion
NORMAL_COLOR = "#e8e8ea"

# preset name -> (idle->blur secs, idle->delete secs, session minutes)
PRESETS = {
    "Gentle": (5.0, 10.0, 5),
    "Standard": (3.0, 7.0, 10),
    "Hardcore": (2.0, 5.0, 15),
    "Custom": None,  # resolved from settings
}

FALLBACK_FONTS = [
    "Cascadia Code",
    "Consolas",
    "Courier New",
    "Segoe UI",
    "Arial",
    "Times New Roman",
    "Georgia",
]

MARK_GUTTER_WIDTH = 18


class MarkGutter(QWidget):
    def __init__(self, editor: "DangerEditor"):
        super().__init__(editor)
        self.editor = editor
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedWidth(MARK_GUTTER_WIDTH)

    def paintEvent(self, event):
        self.editor.paint_mark_gutter(event)


# --------------------------------------------------------------------------- #
# Editor with editing modes + ephemeral session marks
# --------------------------------------------------------------------------- #
class DangerEditor(QPlainTextEdit):
    """QPlainTextEdit with Hemingway/Typewriter modes and drawn session marks."""

    _BLOCKED_KEYS = {
        Qt.Key_Backspace,
        Qt.Key_Delete,
        Qt.Key_Left,
        Qt.Key_Right,
        Qt.Key_Up,
        Qt.Key_Down,
        Qt.Key_Home,
        Qt.Key_End,
        Qt.Key_PageUp,
        Qt.Key_PageDown,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hemingway = False
        self.typewriter = False
        self.show_marks = True
        self.marks: list[dict] = []  # {"cursor": QTextCursor, "kind": str}
        self.mark_gutter = MarkGutter(self)
        self._apply_editor_margins()
        self.setCenterOnScroll(True)
        self.cursorPositionChanged.connect(self._on_cursor_moved)
        self.verticalScrollBar().valueChanged.connect(self.mark_gutter.update)

    # ----- typewriter ----------------------------------------------------- #
    def _on_cursor_moved(self):
        if self.typewriter:
            self._center_caret()

    def set_typewriter(self, on: bool):
        self.typewriter = on
        self.setCenterOnScroll(on)
        if on:
            QTimer.singleShot(0, self._center_caret)

    def _apply_editor_margins(self):
        self.setViewportMargins(MARK_GUTTER_WIDTH, 0, 0, 0)

    def _apply_typewriter_margins(self):
        """Keep the marker gutter while avoiding vertical typewriter margins."""
        m = self.viewportMargins()
        if m.left() != MARK_GUTTER_WIDTH or m.top() or m.bottom() or m.right():
            self._apply_editor_margins()

    def _center_caret(self):
        """Scroll so the caret line is vertically centered in the viewport."""
        bar = self.verticalScrollBar()
        line_h = max(1, self.fontMetrics().lineSpacing())
        visible_blocks = max(1, self.viewport().height() // line_h)
        target = self.textCursor().blockNumber() - visible_blocks // 2
        bar.setValue(max(bar.minimum(), min(bar.maximum(), target)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_typewriter_margins()
        vp = self.viewport().geometry()
        self.mark_gutter.setGeometry(0, vp.y(), MARK_GUTTER_WIDTH, vp.height())
        self.mark_gutter.update()
        if self.typewriter:
            QTimer.singleShot(0, self._center_caret)

    # ----- hemingway ------------------------------------------------------ #
    def keyPressEvent(self, event):
        if self.hemingway:
            if event.key() in self._BLOCKED_KEYS:
                return
            if (
                event.matches(QKeySequence.Undo)
                or event.matches(QKeySequence.Redo)
                or event.matches(QKeySequence.Cut)
            ):
                return
            cursor = self.textCursor()
            end = self.document().characterCount() - 1
            if cursor.hasSelection() or cursor.position() != end:
                cursor.movePosition(QTextCursor.End)
                self.setTextCursor(cursor)
        super().keyPressEvent(event)
        if self.typewriter:
            self._center_caret()

    def mousePressEvent(self, event):
        if self.hemingway:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.setTextCursor(cursor)
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.hemingway:
            return
        super().mouseDoubleClickEvent(event)

    # ----- marks ---------------------------------------------------------- #
    def add_mark(self, cursor: QTextCursor, kind: str) -> QTextCursor:
        cursor.setKeepPositionOnInsert(True)
        self.marks.append({"cursor": cursor, "kind": kind})
        self.mark_gutter.update()
        return cursor

    def clear_marks(self):
        self.marks.clear()
        self.mark_gutter.update()

    def paint_mark_gutter(self, event):
        if not self.show_marks or not self.marks:
            return
        painter = QPainter(self.mark_gutter)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont("Segoe UI")
        font.setPointSize(8)
        painter.setFont(font)
        height = self.mark_gutter.height()
        doc = self.document()
        offset = self.contentOffset()
        line_slots: dict[int, int] = {}
        for mark in self.marks:
            block = doc.findBlock(mark["cursor"].position())
            if not block.isValid():
                continue
            geo = self.blockBoundingGeometry(block).translated(offset)
            base_y = int(
                geo.top() + min(geo.height() / 2, self.fontMetrics().height() / 2)
            )
            slot = line_slots.get(base_y, 0)
            line_slots[base_y] = slot + 1
            if slot == 0:
                y = base_y - 5
            elif slot == 1:
                y = base_y + 5
            else:
                y = base_y
            if y < -8 or y > height + 8:
                continue  # off-screen
            if mark["kind"] == "start":
                color = QColor("#16a34a")
            else:
                color = QColor("#6b7280")
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(color))
            flag = QPainterPath()
            flag.moveTo(4, y - 5)
            flag.lineTo(13, y)
            flag.lineTo(4, y + 5)
            flag.closeSubpath()
            painter.drawPath(flag)
        painter.end()


# --------------------------------------------------------------------------- #
# Reusable -/+ counter for menus
# --------------------------------------------------------------------------- #
class CounterWidget(QWidget):
    def __init__(self, label, value, lo, hi, on_change, parent=None):
        super().__init__(parent)
        self.value = value
        self.lo = lo
        self.hi = hi
        self.on_change = on_change

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 4, 14, 4)
        lay.setSpacing(8)
        lay.addWidget(QLabel(label))
        lay.addStretch(1)

        self.minus = QPushButton("\u2212")
        self.minus.setObjectName("counterBtn")
        self.minus.setFixedSize(26, 26)
        self.val = QLabel(str(value))
        self.val.setMinimumWidth(26)
        self.val.setAlignment(Qt.AlignCenter)
        self.plus = QPushButton("+")
        self.plus.setObjectName("counterBtn")
        self.plus.setFixedSize(26, 26)

        self.minus.clicked.connect(lambda: self._step(-1))
        self.plus.clicked.connect(lambda: self._step(1))

        lay.addWidget(self.minus)
        lay.addWidget(self.val)
        lay.addWidget(self.plus)

    def _step(self, delta):
        self.value = max(self.lo, min(self.hi, self.value + delta))
        self.val.setText(str(self.value))
        self.on_change(self.value)


# --------------------------------------------------------------------------- #
# Toast notification
# --------------------------------------------------------------------------- #
class Toast(QWidget):
    """A small frameless popup that fades in, waits, then fades out."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._label = QLabel("", self)
        self._label.setAlignment(Qt.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)

        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)

        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

    def show_message(self, text: str, kind: str = "info", duration: int = 1800):
        bg = {
            "info": "#2563eb",
            "success": "#16a34a",
            "danger": "#dc2626",
        }.get(kind, "#2563eb")
        self._label.setStyleSheet(
            f"""
            QLabel {{
                background-color: {bg};
                color: white;
                font-size: 15px;
                font-weight: 600;
                padding: 12px 22px;
                border-radius: 12px;
            }}
            """
        )
        self._label.setText(text)
        self.adjustSize()
        self._reposition()

        self._fade.stop()
        self._opacity.setOpacity(0.0)
        self.show()
        self.raise_()
        self._fade.setDuration(180)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.start()
        self._hide_timer.start(duration)

    def _reposition(self):
        parent = self.parentWidget()
        if not parent:
            return
        geo = parent.geometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + int(geo.height() * 0.12)
        self.move(QPoint(x, y))

    def _fade_out(self):
        self._fade.stop()
        self._fade.setDuration(320)
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.InCubic)
        self._fade.start()
        self._fade.finished.connect(self._maybe_hide)

    def _maybe_hide(self):
        if self._opacity.opacity() <= 0.01:
            self.hide()


# --------------------------------------------------------------------------- #
# Custom timing dialog
# --------------------------------------------------------------------------- #
class CustomTimingDialog(QDialog):
    def __init__(self, parent, blur_s: float, delete_s: float, session_m: int):
        super().__init__(parent)
        self.setWindowTitle("Custom timing")
        self.setModal(True)

        self.blur = QSpinBox()
        self.blur.setRange(1, 60)
        self.blur.setSuffix(" s")
        self.blur.setValue(int(blur_s))

        self.delete = QSpinBox()
        self.delete.setRange(2, 120)
        self.delete.setSuffix(" s")
        self.delete.setValue(int(delete_s))

        self.session = QSpinBox()
        self.session.setRange(1, 240)
        self.session.setSuffix(" min")
        self.session.setValue(int(session_m))

        form = QFormLayout()
        form.addRow("Idle before blur:", self.blur)
        form.addRow("Idle before deletion:", self.delete)
        form.addRow("Session length:", self.session)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._validate_accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _validate_accept(self):
        if self.delete.value() <= self.blur.value():
            QMessageBox.warning(
                self,
                "Invalid timing",
                "Deletion delay must be greater than the blur delay.",
            )
            return
        self.accept()

    def values(self) -> tuple[float, float, int]:
        return (
            float(self.blur.value()),
            float(self.delete.value()),
            int(self.session.value()),
        )


class FontDialog(QDialog):
    def __init__(self, parent, families: list[str], current_family: str):
        super().__init__(parent)
        self.setWindowTitle("Choose font")
        self.setModal(True)
        self.resize(360, 420)
        self.selected_family = current_family

        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for family in families:
            item = QListWidgetItem(family)
            item.setFont(QFont(family, 11))
            self.list.addItem(item)
            if family == current_family:
                self.list.setCurrentItem(item)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.list.itemDoubleClicked.connect(lambda _=None: self.accept())

        lay = QVBoxLayout(self)
        lay.addWidget(self.list)
        lay.addWidget(buttons)

    def accept(self):
        item = self.list.currentItem()
        if item:
            self.selected_family = item.text()
        super().accept()


class SessionHistoryDialog(QDialog):
    def __init__(self, parent, entries: list[dict], log_path: str):
        super().__init__(parent)
        self.setWindowTitle("Session history")
        self.resize(860, 520)

        path_label = QLabel(f"Log: {os.path.normpath(log_path)}")
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(8)
        self.tree.setHeaderLabels(
            [
                "Time",
                "Result",
                "Mode",
                "Words",
                "Deletes",
                "WPM",
                "Avg s/word",
                "Duration",
            ]
        )
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.setAlternatingRowColors(True)

        self._populate(entries)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(path_label)
        lay.addWidget(self.tree, 1)
        lay.addWidget(buttons)

    def _populate(self, entries: list[dict]):
        if not entries:
            item = QTreeWidgetItem(["No sessions logged yet."])
            item.setForeground(0, QBrush(QColor("#8a8a93")))
            self.tree.addTopLevelItem(item)
            return

        entries = sorted(entries, key=lambda e: e.get("started_at", ""), reverse=True)
        by_date: dict[str, list[dict]] = {}
        for entry in entries:
            dt = self._parse_dt(entry.get("started_at", ""))
            date_key = dt.strftime("%A, %B %d, %Y") if dt else "Unknown date"
            by_date.setdefault(date_key, []).append(entry)

        for date_key, day_entries in by_date.items():
            parent = QTreeWidgetItem([date_key])
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            parent.setForeground(0, QBrush(QColor("#e8e8ea")))
            self.tree.addTopLevelItem(parent)
            for entry in day_entries:
                child = QTreeWidgetItem(self._entry_columns(entry))
                color = self._entry_color(entry)
                for col in range(self.tree.columnCount()):
                    child.setForeground(col, QBrush(color))
                parent.addChild(child)
        self.tree.expandAll()

    def _entry_columns(self, entry: dict) -> list[str]:
        started = self._parse_dt(entry.get("started_at", ""))
        time_s = started.strftime("%I:%M %p").lstrip("0") if started else "?"
        duration_s = int(round(float(entry.get("duration_seconds", 0))))
        mins, secs = divmod(max(0, duration_s), 60)
        outcome = str(entry.get("outcome", "stopped")).capitalize()
        return [
            time_s,
            outcome,
            str(entry.get("preset", "")),
            str(entry.get("words", 0)),
            str(entry.get("deletes", 0)),
            str(entry.get("wpm", 0)),
            f"{float(entry.get('avg_word_interval', 0.0)):.2f}",
            f"{mins}:{secs:02d}",
        ]

    @staticmethod
    def _entry_color(entry: dict) -> QColor:
        if int(entry.get("deletes", 0)) > 0:
            return QColor("#ef4444")
        if entry.get("outcome") == "completed":
            return QColor("#16a34a")
        return QColor("#f59e0b")

    @staticmethod
    def _parse_dt(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Write or Die")
        self.resize(960, 700)

        self.settings = QSettings(APP_ORG, APP_NAME)
        self.history_path = self._history_file_path()

        # session state
        self.session_active = False
        self.session_end_ms = 0.0
        self.last_keystroke_ms = 0.0
        self.current_file: str | None = None
        self.session_start_cursor: QTextCursor | None = None
        self.session_started_at = ""

        # per-session stats
        self.session_start_ms = 0.0
        self.session_words = 0
        self.session_deletes = 0
        self.word_times: list[float] = []
        self._prev_word_count = 0
        self.last_session_summary = ""

        # timing presets
        self.custom_blur = float(self.settings.value("custom/blur", 3.0))
        self.custom_delete = float(self.settings.value("custom/delete", 7.0))
        self.custom_session = int(self.settings.value("custom/session", 10))
        self.current_preset = str(self.settings.value("preset", "Standard"))
        if self.current_preset not in PRESETS:
            self.current_preset = "Standard"

        # font + focus settings
        self.font_family = str(self.settings.value("font/family", "Cascadia Code"))
        self.font_size = int(self.settings.value("font/size", 13))
        self.focus_words = int(self.settings.value("focus/words", 0))
        self.focus_sentences = int(self.settings.value("focus/sentences", 1))
        self.dark_mode = self._bool_setting("view/dark_mode", True)
        self.disable_stop = self._bool_setting(
            "session/disable_stop", self.current_preset == "Hardcore"
        )

        # focus-mode reveal animation state
        self._focus_start: int | None = None
        self._focus_render_start: int | None = None
        self._focus_render_end: int | None = None
        self._focus_selection: QTextEdit.ExtraSelection | None = None
        self._danger_selection: QTextEdit.ExtraSelection | None = None
        self.focus_anim = QVariantAnimation(self)
        self.focus_anim.setDuration(180)
        self.focus_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.focus_anim.valueChanged.connect(
            lambda v: self._paint_focus_span(float(v))
        )

        self._build_ui()
        self._build_menus()
        self._apply_style()
        self._apply_font()
        self._load_view_state()

        self.toast = Toast(self)

        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self._sync_preset_widgets()
        self._update_word_count()

    def _bool_setting(self, key: str, default: bool) -> bool:
        v = self.settings.value(key, default)
        return v in (True, "true", "True", 1, "1")

    # ----- timing helpers ------------------------------------------------- #
    def _active_timing(self) -> tuple[float, float, int]:
        if self.current_preset == "Custom":
            return (self.custom_blur, self.custom_delete, self.custom_session)
        return PRESETS[self.current_preset]

    # ----- UI construction ------------------------------------------------ #
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)

        self.start_btn = QPushButton("Start session")
        self.start_btn.setObjectName("startButton")
        self.start_btn.clicked.connect(self.toggle_session)
        header.addWidget(self.start_btn)

        header.addStretch(1)

        self.countdown_label = QLabel("")
        self.countdown_label.setObjectName("countdownLabel")
        self.countdown_label.setMinimumWidth(56)
        self.countdown_label.setAlignment(Qt.AlignCenter)
        header.addWidget(self.countdown_label)

        self.timer_label = QLabel("10:00")
        self.timer_label.setObjectName("timerLabel")
        header.addWidget(self.timer_label)

        root.addLayout(header)

        self.editor = DangerEditor()
        self.editor.setPlaceholderText(
            "Press Start and write like your words depend on it…"
        )
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.cursorPositionChanged.connect(self._update_focus)
        root.addWidget(self.editor, 1)

        self.blur_overlay = QLabel(self.editor.viewport())
        self.blur_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.blur_overlay.setScaledContents(True)
        self.blur_overlay.hide()
        self.blur_effect = QGraphicsBlurEffect(self.blur_overlay)
        self.blur_effect.setBlurRadius(0.0)
        self.blur_overlay.setGraphicsEffect(self.blur_effect)

        self._setup_scrollbar()

        self.setCentralWidget(central)

        self.footer = QWidget()
        self.footer.setObjectName("footer")
        footer_lay = QHBoxLayout(self.footer)
        footer_lay.setContentsMargins(4, 0, 4, 0)
        footer_lay.setSpacing(12)
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("statsLabel")
        self.stats_label.setMinimumWidth(0)
        self.stats_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        footer_lay.addWidget(self.stats_label, 1)
        self.word_label = QLabel("0 words")
        self.word_label.setObjectName("wordLabel")
        self.word_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.word_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        footer_lay.addWidget(self.word_label, 0)
        root.addWidget(self.footer)

    # ----- auto-hiding scrollbar ------------------------------------------ #
    def _setup_scrollbar(self):
        bar = self.editor.verticalScrollBar()
        self.sb_opacity = QGraphicsOpacityEffect(bar)
        self.sb_opacity.setOpacity(0.0)
        bar.setGraphicsEffect(self.sb_opacity)

        self.sb_anim = QPropertyAnimation(self.sb_opacity, b"opacity", self)
        self.sb_anim.setDuration(220)

        self.sb_hide_timer = QTimer(self)
        self.sb_hide_timer.setSingleShot(True)
        self.sb_hide_timer.setInterval(1200)
        self.sb_hide_timer.timeout.connect(self._fade_scrollbar_out)

        bar.valueChanged.connect(self._on_editor_scrolled)
        self.editor.viewport().installEventFilter(self)

    def _on_editor_scrolled(self, _=0):
        self._hide_idle_blur()
        self._flash_scrollbar()

    def _flash_scrollbar(self):
        if not hasattr(self, "sb_opacity"):
            return
        self.sb_anim.stop()
        self.sb_anim.setStartValue(self.sb_opacity.opacity())
        self.sb_anim.setEndValue(1.0)
        self.sb_anim.start()
        self.sb_hide_timer.start()

    def _fade_scrollbar_out(self):
        self.sb_anim.stop()
        self.sb_anim.setStartValue(self.sb_opacity.opacity())
        self.sb_anim.setEndValue(0.0)
        self.sb_anim.start()

    def _sync_blur_overlay_geometry(self):
        if hasattr(self, "blur_overlay"):
            self.blur_overlay.setGeometry(self.editor.viewport().rect())

    def _hide_idle_blur(self):
        if hasattr(self, "blur_overlay"):
            self.blur_effect.setBlurRadius(0.0)
            self.blur_overlay.clear()
            self.blur_overlay.hide()
        self._danger_selection = None
        self._apply_extra_selections()

    def _set_idle_blur(self, radius: float):
        if radius <= 0.01:
            self._hide_idle_blur()
            return
        self._paint_danger_span(min(1.0, radius / MAX_BLUR))

    def eventFilter(self, obj, event):
        if obj is self.editor.viewport() and event.type() in (
            event.Type.MouseMove,
            event.Type.Wheel,
            event.Type.Enter,
        ):
            self._hide_idle_blur()
            self._flash_scrollbar()
        elif obj is self.editor.viewport() and event.type() == event.Type.Resize:
            self._sync_blur_overlay_geometry()
            self._hide_idle_blur()
        return super().eventFilter(obj, event)

    def _build_menus(self):
        mb = self.menuBar()

        # File ------------------------------------------------------------- #
        file_menu = mb.addMenu("&File")
        act_new = QAction("New", self)
        act_new.setShortcut(QKeySequence.New)
        act_new.triggered.connect(self.new_file)
        file_menu.addAction(act_new)

        act_open = QAction("Open…", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self.open_file)
        file_menu.addAction(act_open)

        act_save = QAction("Save", self)
        act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self.save_file)
        file_menu.addAction(act_save)

        act_save_as = QAction("Save As…", self)
        act_save_as.setShortcut(QKeySequence.SaveAs)
        act_save_as.triggered.connect(self.save_file_as)
        file_menu.addAction(act_save_as)

        file_menu.addSeparator()
        act_exit = QAction("Exit", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # Session ---------------------------------------------------------- #
        session_menu = mb.addMenu("&Session")
        preset_menu = session_menu.addMenu("Mode")
        self.preset_group = QActionGroup(self)
        self.preset_group.setExclusive(True)
        self.preset_actions: dict[str, QAction] = {}
        for name in PRESETS:
            act = QAction(self._preset_label(name), self, checkable=True)
            act.setData(name)
            act.triggered.connect(lambda _=False, n=name: self._choose_preset(n))
            self.preset_group.addAction(act)
            preset_menu.addAction(act)
            self.preset_actions[name] = act

        session_menu.addSeparator()
        act_history = QAction("History...", self)
        act_history.triggered.connect(self.show_session_history)
        session_menu.addAction(act_history)

        session_menu.addSeparator()
        self.act_disable_stop = QAction(
            "Disable Stop during session", self, checkable=True
        )
        self.act_disable_stop.setChecked(self.disable_stop)
        self.act_disable_stop.triggered.connect(self._on_disable_stop)
        session_menu.addAction(self.act_disable_stop)

        session_menu.addSeparator()
        self.act_start = QAction("Start / Stop", self)
        self.act_start.setShortcut("Ctrl+Return")
        self.act_start.triggered.connect(self.toggle_session)
        session_menu.addAction(self.act_start)

        # Format ----------------------------------------------------------- #
        format_menu = mb.addMenu("F&ormat")
        self.font_families = list(QFontDatabase.families()) or FALLBACK_FONTS
        if self.font_family not in self.font_families:
            self.font_families.insert(0, self.font_family)
        self.act_choose_font = QAction(f"Font... ({self.font_family})", self)
        self.act_choose_font.triggered.connect(self._open_font_dialog)
        format_menu.addAction(self.act_choose_font)

        size_action = QWidgetAction(self)
        size_action.setDefaultWidget(
            CounterWidget("Font size", self.font_size, 6, 48, self._on_size_changed)
        )
        format_menu.addAction(size_action)

        # View ------------------------------------------------------------- #
        view_menu = mb.addMenu("&View")

        self.act_dark_mode = QAction("Dark mode", self, checkable=True)
        self.act_dark_mode.setChecked(self.dark_mode)
        self.act_dark_mode.triggered.connect(self._on_dark_mode)
        view_menu.addAction(self.act_dark_mode)
        view_menu.addSeparator()

        self.act_hide_text = QAction("Hide all text", self, checkable=True)
        self.act_hide_text.triggered.connect(self._on_visibility_toggle)
        view_menu.addAction(self.act_hide_text)

        focus_menu = view_menu.addMenu("Focus mode")
        self.act_focus = QAction("Enable focus mode", self, checkable=True)
        self.act_focus.triggered.connect(self._on_visibility_toggle)
        focus_menu.addAction(self.act_focus)
        words_action = QWidgetAction(self)
        words_action.setDefaultWidget(
            CounterWidget(
                "Words behind cursor", self.focus_words, 0, 50,
                self._on_focus_words,
            )
        )
        focus_menu.addAction(words_action)
        sent_action = QWidgetAction(self)
        sent_action.setDefaultWidget(
            CounterWidget(
                "Sentences behind cursor", self.focus_sentences, 0, 10,
                self._on_focus_sentences,
            )
        )
        focus_menu.addAction(sent_action)

        view_menu.addSeparator()
        self.act_hemingway = QAction(
            "Hemingway mode (no backspace/cursor)", self, checkable=True
        )
        self.act_hemingway.triggered.connect(self._on_hemingway)
        view_menu.addAction(self.act_hemingway)

        self.act_typewriter = QAction(
            "Typewriter mode (center line)", self, checkable=True
        )
        self.act_typewriter.triggered.connect(self._on_typewriter)
        view_menu.addAction(self.act_typewriter)

        view_menu.addSeparator()
        self.act_show_timer = QAction("Show timer", self, checkable=True)
        self.act_show_timer.setChecked(True)
        self.act_show_timer.triggered.connect(self._apply_indicator_visibility)
        view_menu.addAction(self.act_show_timer)

        self.act_show_countdown = QAction(
            "Show deletion countdown", self, checkable=True
        )
        self.act_show_countdown.setChecked(True)
        self.act_show_countdown.triggered.connect(
            self._apply_indicator_visibility
        )
        view_menu.addAction(self.act_show_countdown)

        self.act_show_marks = QAction("Show session marks", self, checkable=True)
        self.act_show_marks.setChecked(True)
        self.act_show_marks.triggered.connect(self._on_show_marks)
        view_menu.addAction(self.act_show_marks)

    # ----- preset wiring -------------------------------------------------- #
    def _preset_label(self, name: str) -> str:
        if name == "Custom":
            blur_s, delete_s, session_m = (
                self.custom_blur,
                self.custom_delete,
                self.custom_session,
            )
            return (
                f"Custom... - blur {blur_s:g}s / delete {delete_s:g}s / "
                f"{session_m:g} min"
            )
        blur_s, delete_s, session_m = PRESETS[name]
        return f"{name} - blur {blur_s:g}s / delete {delete_s:g}s / {session_m:g} min"

    def _choose_preset(self, name: str):
        if name == "Custom":
            dlg = CustomTimingDialog(
                self, self.custom_blur, self.custom_delete, self.custom_session
            )
            if dlg.exec() == QDialog.Accepted:
                self.custom_blur, self.custom_delete, self.custom_session = (
                    dlg.values()
                )
                self.settings.setValue("custom/blur", self.custom_blur)
                self.settings.setValue("custom/delete", self.custom_delete)
                self.settings.setValue("custom/session", self.custom_session)
                self.current_preset = "Custom"
            else:
                self._sync_preset_widgets()
                return
        else:
            self.current_preset = name

        self.settings.setValue("preset", self.current_preset)
        if self.current_preset == "Hardcore":
            self.disable_stop = True
            self.act_disable_stop.setChecked(True)
            self.settings.setValue("session/disable_stop", True)
        self._sync_preset_widgets()
        if not self.session_active:
            self._reset_timer_label()

    def _apply_stop_lock_state(self):
        locked = self.session_active and self.disable_stop
        self.start_btn.setEnabled(not locked)
        self.act_start.setEnabled(not locked)

    def _on_disable_stop(self):
        self.disable_stop = self.act_disable_stop.isChecked()
        self.settings.setValue("session/disable_stop", self.disable_stop)
        self._apply_stop_lock_state()

    def _sync_preset_widgets(self):
        for name, act in self.preset_actions.items():
            act.setText(self._preset_label(name))
        act = self.preset_actions.get(self.current_preset)
        if act:
            act.setChecked(True)

    def _reset_timer_label(self):
        _, _, session_m = self._active_timing()
        self.timer_label.setText(f"{session_m:02d}:00")
        self.countdown_label.setText("")

    # ----- session control ------------------------------------------------ #
    def toggle_session(self):
        if self.session_active:
            if self.disable_stop:
                return
            self._stop_session(success=False, silent=True)
        else:
            self._start_session()

    def _start_session(self):
        _, _, session_m = self._active_timing()
        now = time.monotonic() * 1000.0
        self.session_started_at = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        self.session_active = True
        self.session_end_ms = now + session_m * 60_000.0
        self.last_keystroke_ms = now

        # reset per-session stats
        self.session_start_ms = now
        self.session_words = 0
        self.session_deletes = 0
        self.word_times = []
        self._prev_word_count = len(self.editor.toPlainText().split())

        # protection boundary at the current end of the document
        boundary = self.editor.textCursor()
        boundary.movePosition(QTextCursor.End)
        self.session_start_cursor = self.editor.add_mark(boundary, "start")

        self.start_btn.setText("Stop session")
        self.start_btn.setProperty("running", True)
        self._repolish(self.start_btn)
        self._apply_stop_lock_state()
        for act in self.preset_actions.values():
            act.setEnabled(False)
        self._update_base_color()
        self._update_focus()
        self.editor.setFocus()
        self.toast.show_message("Session started — keep writing!", "info")

    def _stop_session(self, success: bool, silent: bool = False):
        # finalize stats while the boundary cursor is still valid
        now = time.monotonic() * 1000.0
        self.session_words = self._session_word_count()
        elapsed_min = max(1e-6, (now - self.session_start_ms) / 60_000.0)
        wpm = int(self.session_words / elapsed_min)
        self.last_session_summary = (
            f"Last: {self.session_words} w \u00b7 {self.session_deletes} del "
            f"\u00b7 {self._avg_word_interval():.2f} s/word \u00b7 {wpm} wpm"
        )
        self._append_session_history(
            {
                "version": 1,
                "started_at": self.session_started_at,
                "ended_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "outcome": "completed" if success else "stopped",
                "preset": self.current_preset,
                "blur_seconds": self._active_timing()[0],
                "delete_seconds": self._active_timing()[1],
                "session_minutes": self._active_timing()[2],
                "duration_seconds": round((now - self.session_start_ms) / 1000.0, 2),
                "words": self.session_words,
                "deletes": self.session_deletes,
                "avg_word_interval": round(self._avg_word_interval(), 3),
                "wpm": wpm,
            }
        )

        self.session_active = False

        # stop mark at the current end
        stop = self.editor.textCursor()
        stop.movePosition(QTextCursor.End)
        self.editor.add_mark(stop, "stop")
        self.session_start_cursor = None
        self.stats_label.setText(self.last_session_summary)

        self.start_btn.setText("Start session")
        self.start_btn.setProperty("running", False)
        self.start_btn.setEnabled(True)
        self.act_start.setEnabled(True)
        self._repolish(self.start_btn)
        for act in self.preset_actions.values():
            act.setEnabled(True)
        self._hide_idle_blur()
        self.countdown_label.setText("")
        self._reset_timer_label()

        # reveal text again (hide/focus are session-only)
        self._update_base_color()
        self._update_focus()

        if success:
            text = self.editor.toPlainText()
            QApplication.clipboard().setText(text)
            self.toast.show_message("Copied to clipboard \u2713", "success", 2200)
        elif not silent:
            self.toast.show_message("Session stopped", "info")

    # ----- engine tick ---------------------------------------------------- #
    def _tick(self):
        if not self.session_active:
            return

        now = time.monotonic() * 1000.0
        blur_s, delete_s, _ = self._active_timing()
        idle = (now - self.last_keystroke_ms) / 1000.0

        remaining = max(0.0, (self.session_end_ms - now) / 1000.0)
        mins = int(remaining) // 60
        secs = int(remaining) % 60
        self.timer_label.setText(f"{mins:02d}:{secs:02d}")

        self._update_stats(now)

        if remaining <= 0.0:
            self._stop_session(success=True)
            return

        if idle < blur_s:
            self._hide_idle_blur()
            self.countdown_label.setText("")
        elif idle < delete_s:
            frac = (idle - blur_s) / max(0.001, (delete_s - blur_s))
            self._set_idle_blur(MAX_BLUR * frac)
            left = delete_s - idle
            self.countdown_label.setText(f"{left:0.1f}s")
        else:
            self._wipe_session_text()
            self.session_deletes += 1
            self._hide_idle_blur()
            self.last_keystroke_ms = now
            self.countdown_label.setText("")
            self.toast.show_message("DELETED \u2014 keep up!", "danger", 1400)

    def _wipe_session_text(self):
        """Delete only text written since the session start boundary."""
        self.editor.blockSignals(True)
        if self.session_start_cursor is not None:
            start = self.session_start_cursor.position()
            cursor = self.editor.textCursor()
            cursor.setPosition(start)
            cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            self.editor.setTextCursor(cursor)
        else:
            self.editor.clear()
        self.editor.blockSignals(False)
        self._prev_word_count = len(self.editor.toPlainText().split())
        self._update_word_count()
        self._update_focus()

    # ----- text events ---------------------------------------------------- #
    def _on_text_changed(self):
        mono = time.monotonic()
        self.last_keystroke_ms = mono * 1000.0
        if self.session_active:
            self._hide_idle_blur()
            self.countdown_label.setText("")
            count = len(self.editor.toPlainText().split())
            if count > self._prev_word_count:
                self.word_times.append(mono)
            self._prev_word_count = count
        self._update_word_count()
        self._update_focus()

    def _update_word_count(self):
        text = self.editor.toPlainText().strip()
        n = len(text.split()) if text else 0
        self.word_label.setText(f"{n} word{'s' if n != 1 else ''}")

    # ----- session stats -------------------------------------------------- #
    def _session_word_count(self) -> int:
        if self.session_start_cursor is None:
            return 0
        cur = self.editor.textCursor()
        cur.setPosition(self.session_start_cursor.position())
        cur.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        txt = cur.selectedText().strip()
        return len(txt.split()) if txt else 0

    def _avg_word_interval(self) -> float:
        if len(self.word_times) < 2:
            return 0.0
        diffs = [b - a for a, b in zip(self.word_times, self.word_times[1:])]
        return sum(diffs) / len(diffs)

    def _update_stats(self, now_ms: float):
        self.session_words = self._session_word_count()
        elapsed_min = max(1e-6, (now_ms - self.session_start_ms) / 60_000.0)
        wpm = int(self.session_words / elapsed_min)
        self.stats_label.setText(
            f"S: {self.session_words} w \u00b7 {self.session_deletes} del "
            f"\u00b7 {self._avg_word_interval():.2f} s/word \u00b7 {wpm} wpm"
        )

    # ----- session history ----------------------------------------------- #
    def _history_file_path(self) -> str:
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        if not base:
            base = os.path.join(os.path.expanduser("~"), ".write-or-die")
        try:
            os.makedirs(base, exist_ok=True)
        except OSError:
            base = os.path.abspath(".")
        return os.path.normpath(os.path.join(base, "session-history.jsonl"))

    def _append_session_history(self, entry: dict):
        try:
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=True) + "\n")
        except OSError as exc:
            self.toast.show_message(f"History log failed: {exc}", "danger", 2600)

    def _load_session_history(self) -> list[dict]:
        if not os.path.exists(self.history_path):
            return []
        entries: list[dict] = []
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict):
                        entries.append(entry)
        except OSError as exc:
            QMessageBox.warning(self, "History unavailable", str(exc))
        return entries

    def show_session_history(self):
        dlg = SessionHistoryDialog(
            self, self._load_session_history(), self.history_path
        )
        dlg.setStyleSheet(self.styleSheet())
        dlg.exec()

    # ----- visibility / focus engine -------------------------------------- #
    def _focus_active(self) -> bool:
        return (
            self.session_active
            and self.act_focus.isChecked()
            and not self.act_hide_text.isChecked()
        )

    def _hidden_active(self) -> bool:
        return self.session_active and (
            self.act_hide_text.isChecked() or self.act_focus.isChecked()
        )

    def _normal_text_color(self) -> str:
        return "#e8e8ea" if self.dark_mode else "#202124"

    def _update_base_color(self):
        self._apply_editor_style()

    def _apply_editor_style(self):
        """Single editor stylesheet combining text color + font.

        A stylesheet-set font beats the QWidget font cascaded from STYLE, so the
        chosen family/size actually takes effect (plain setFont() was ignored).
        """
        color = "transparent" if self._hidden_active() else self._normal_text_color()
        self.editor.setStyleSheet(
            "QPlainTextEdit {{"
            " color: {color};"
            " font-family: '{family}';"
            " font-size: {size}pt;"
            " }}".format(color=color, family=self.font_family, size=self.font_size)
        )

    def _update_focus(self):
        if not self._focus_active():
            self.focus_anim.stop()
            self._focus_start = None
            self._focus_render_start = None
            self._focus_render_end = None
            self._focus_selection = None
            self._apply_extra_selections()
            return

        text = self.editor.toPlainText()
        pos = self.editor.textCursor().position()
        starts = []
        if self.focus_words > 0:
            starts.append(self._start_last_words(text, pos, self.focus_words))
        if self.focus_sentences > 0:
            starts.append(
                self._start_current_sentences(text, pos, self.focus_sentences)
            )
        if not starts:  # both disabled -> default to current sentence
            starts.append(self._start_current_sentences(text, pos, 1))
        start = min(starts)

        prev_start = self._focus_start
        if start < pos:
            # there is text to reveal
            self._focus_render_start = start
            self._focus_render_end = pos
            if start != prev_start:
                self._start_focus_anim(0.0, 1.0)  # new span fades in
            else:
                self.focus_anim.stop()
                self._paint_focus_span(1.0)  # same sentence, stay revealed
        else:
            # nothing to reveal now (e.g. a sentence just ended) -> fade out
            if (
                self._focus_render_start is not None
                and self._focus_render_end is not None
                and self._focus_render_start < self._focus_render_end
            ):
                self._start_focus_anim(1.0, 0.0)
            else:
                self.focus_anim.stop()
                self._focus_selection = None
                self._apply_extra_selections()
        self._focus_start = start

    def _start_focus_anim(self, frm: float, to: float):
        self.focus_anim.stop()
        self.focus_anim.setStartValue(float(frm))
        self.focus_anim.setEndValue(float(to))
        self.focus_anim.start()

    def _paint_focus_span(self, alpha_frac: float):
        if (
            not self._focus_active()
            or self._focus_render_start is None
            or self._focus_render_end is None
            or self._focus_render_start >= self._focus_render_end
        ):
            self._focus_selection = None
            self._apply_extra_selections()
            return
        cursor = self.editor.textCursor()
        cursor.setPosition(self._focus_render_start)
        cursor.setPosition(self._focus_render_end, QTextCursor.KeepAnchor)
        color = QColor(self._normal_text_color())
        color.setAlpha(max(0, min(255, int(alpha_frac * 255))))
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = fmt
        self._focus_selection = selection
        self._apply_extra_selections()

    def _paint_danger_span(self, frac: float):
        if self.session_start_cursor is None:
            self._danger_selection = None
            self._apply_extra_selections()
            return
        start = self.session_start_cursor.position()
        end = self.editor.document().characterCount() - 1
        if end <= start:
            self._danger_selection = None
            self._apply_extra_selections()
            return
        cursor = self.editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        fg = QColor(self._normal_text_color())
        fg.setAlpha(max(30, int((1.0 - frac) * 255)))
        bg = QColor("#ef4444")
        bg.setAlpha(min(110, int(30 + frac * 80)))
        fmt = QTextCharFormat()
        fmt.setForeground(fg)
        fmt.setBackground(bg)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = fmt
        self._danger_selection = selection
        self._apply_extra_selections()

    def _apply_extra_selections(self):
        selections = []
        if getattr(self, "_danger_selection", None) is not None:
            selections.append(self._danger_selection)
        if getattr(self, "_focus_selection", None) is not None:
            selections.append(self._focus_selection)
        self.editor.setExtraSelections(selections)

    @staticmethod
    def _start_current_sentences(text: str, pos: int, n: int) -> int:
        enders = ".!?\n"
        found = 0
        start = 0
        j = pos - 1
        while j >= 0:
            if text[j] in enders:
                found += 1
                if found == n:
                    start = j + 1
                    break
            j -= 1
        while start < pos and text[start].isspace():
            start += 1
        return start

    @staticmethod
    def _start_last_words(text: str, pos: int, n: int) -> int:
        words = list(re.finditer(r"\S+", text[:pos]))
        if not words:
            return pos
        if len(words) <= n:
            return words[0].start()
        return words[-n].start()

    # ----- view toggle handlers ------------------------------------------- #
    def _on_visibility_toggle(self):
        self.settings.setValue("view/hide_text", self.act_hide_text.isChecked())
        self.settings.setValue("focus/enabled", self.act_focus.isChecked())
        self._update_base_color()
        self._update_focus()

    def _on_focus_words(self, value: int):
        self.focus_words = value
        self.settings.setValue("focus/words", value)
        self._update_focus()

    def _on_focus_sentences(self, value: int):
        self.focus_sentences = value
        self.settings.setValue("focus/sentences", value)
        self._update_focus()

    def _on_hemingway(self):
        self.editor.hemingway = self.act_hemingway.isChecked()
        self.settings.setValue("mode/hemingway", self.editor.hemingway)

    def _on_typewriter(self):
        self.editor.set_typewriter(self.act_typewriter.isChecked())
        self.settings.setValue("mode/typewriter", self.editor.typewriter)

    def _on_show_marks(self):
        self.editor.show_marks = self.act_show_marks.isChecked()
        self.editor.mark_gutter.update()
        self.settings.setValue("view/show_marks", self.editor.show_marks)

    def _on_dark_mode(self):
        self.dark_mode = self.act_dark_mode.isChecked()
        self.settings.setValue("view/dark_mode", self.dark_mode)
        self._apply_style()
        self._apply_editor_style()
        self._update_focus()
        self.editor.mark_gutter.update()

    def _apply_indicator_visibility(self):
        self.timer_label.setVisible(self.act_show_timer.isChecked())
        self.countdown_label.setVisible(self.act_show_countdown.isChecked())
        self.settings.setValue("view/show_timer", self.act_show_timer.isChecked())
        self.settings.setValue(
            "view/show_countdown", self.act_show_countdown.isChecked()
        )

    # ----- font ----------------------------------------------------------- #
    def _open_font_dialog(self):
        dlg = FontDialog(self, self.font_families, self.font_family)
        if dlg.exec() == QDialog.Accepted:
            self._choose_font(dlg.selected_family)

    def _choose_font(self, family: str):
        self.font_family = family
        self.settings.setValue("font/family", self.font_family)
        self._sync_font_action()
        self._apply_editor_style()

    def _on_size_changed(self, value: int):
        self.font_size = value
        self.settings.setValue("font/size", value)
        self._apply_editor_style()

    def _sync_font_action(self):
        self.act_choose_font.setText(f"Font... ({self.font_family})")

    def _apply_font(self):
        self._sync_font_action()
        self._apply_editor_style()

    # ----- settings load -------------------------------------------------- #
    def _load_view_state(self):
        def b(key, default):
            v = self.settings.value(key, default)
            return v in (True, "true", "True", 1, "1")

        self.act_hide_text.setChecked(b("view/hide_text", False))
        self.act_focus.setChecked(b("focus/enabled", False))
        self.act_show_timer.setChecked(b("view/show_timer", True))
        self.act_show_countdown.setChecked(b("view/show_countdown", True))
        self.act_show_marks.setChecked(b("view/show_marks", True))
        self.act_hemingway.setChecked(b("mode/hemingway", False))
        self.act_typewriter.setChecked(b("mode/typewriter", False))

        self.editor.hemingway = self.act_hemingway.isChecked()
        self.editor.set_typewriter(self.act_typewriter.isChecked())
        self.editor.show_marks = self.act_show_marks.isChecked()

        self._update_base_color()
        self._update_focus()
        self._apply_indicator_visibility()
        self._reset_timer_label()

    # ----- file operations ------------------------------------------------ #
    def new_file(self):
        if not self._maybe_discard():
            return
        self.editor.clear()
        self.editor.clear_marks()
        self.current_file = None
        self.setWindowTitle("Write or Die")

    def open_file(self):
        if not self._maybe_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Markdown", "", "Markdown (*.md *.markdown *.txt);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.editor.setPlainText(f.read())
        except OSError as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self.editor.clear_marks()
        self.current_file = path
        self.setWindowTitle(f"Write or Die — {path}")

    def save_file(self) -> bool:
        if not self.current_file:
            return self.save_file_as()
        return self._write_to(self.current_file)

    def save_file_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Markdown", "untitled.md", "Markdown (*.md);;All files (*)"
        )
        if not path:
            return False
        if self._write_to(path):
            self.current_file = path
            self.setWindowTitle(f"Write or Die — {path}")
            return True
        return False

    def _write_to(self, path: str) -> bool:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self.toast.show_message("Saved \u2713", "success", 1200)
        return True

    def _maybe_discard(self) -> bool:
        if not self.editor.toPlainText().strip():
            return True
        resp = QMessageBox.question(
            self,
            "Discard changes?",
            "Discard the current text?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return resp == QMessageBox.Yes

    # ----- misc ----------------------------------------------------------- #
    @staticmethod
    def _repolish(w: QWidget):
        w.style().unpolish(w)
        w.style().polish(w)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "toast"):
            self.toast._reposition()
        self._sync_blur_overlay_geometry()
        self._hide_idle_blur()

    def closeEvent(self, event):
        self.settings.sync()
        super().closeEvent(event)

    def _apply_style(self):
        self.setStyleSheet(STYLE if self.dark_mode else LIGHT_STYLE)


STYLE = """
QMainWindow, QWidget {
    background-color: #16161a;
    color: #e8e8ea;
}
QMenuBar, QMenu, QLabel, QPushButton, QSpinBox, QDialog, QTreeWidget,
QHeaderView, QToolTip {
    font-family: 'Segoe UI', sans-serif;
    font-size: 14px;
}
QPlainTextEdit {
    background-color: #1e1e24;
    color: #e8e8ea;
    border: 1px solid #2c2c35;
    border-radius: 12px;
    padding: 14px;
    selection-background-color: #3b82f6;
}
QLabel { color: #c8c8cf; }
#footer {
    background-color: #16161a;
}
#statsLabel { color: #6f7079; }
#wordLabel { color: #8a8a93; }
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 4px 2px 4px 0;
}
QScrollBar::handle:vertical {
    background: #3a3a44;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: #565664; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal { height: 0; }
#timerLabel {
    font-size: 22px;
    font-weight: 700;
    color: #e8e8ea;
    padding: 2px 8px;
}
#countdownLabel {
    font-size: 22px;
    font-weight: 800;
    color: #ef4444;
}
QPushButton {
    background-color: #3b82f6;
    color: white;
    border: none;
    border-radius: 10px;
    padding: 9px 18px;
    font-weight: 600;
}
QPushButton:hover { background-color: #2f6fe0; }
QPushButton:pressed { background-color: #2861c9; }
QPushButton#startButton[running="true"] {
    background-color: #dc2626;
}
QPushButton#startButton[running="true"]:hover {
    background-color: #c01f1f;
}
QPushButton#counterBtn {
    background-color: #2c2c35;
    border-radius: 6px;
    padding: 0;
    font-weight: 700;
    font-size: 15px;
}
QPushButton#counterBtn:hover { background-color: #3b82f6; }
QMenuBar { background-color: #16161a; padding: 2px; }
QMenuBar::item { padding: 6px 12px; border-radius: 6px; }
QMenuBar::item:selected { background-color: #2c2c35; }
QMenu {
    background-color: #1e1e24;
    border: 1px solid #2c2c35;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item { padding: 7px 24px; border-radius: 6px; }
QMenu::item:selected { background-color: #3b82f6; }
QMenu::separator { height: 1px; background: #2c2c35; margin: 6px 8px; }
QTreeWidget {
    background-color: #1e1e24;
    alternate-background-color: #22222a;
    border: 1px solid #2c2c35;
    border-radius: 8px;
    outline: none;
}
QTreeWidget::item {
    padding: 4px 6px;
}
QTreeWidget::item:selected {
    background-color: #2f3d5c;
}
QHeaderView::section {
    background-color: #2c2c35;
    color: #e8e8ea;
    border: none;
    border-right: 1px solid #3a3a44;
    padding: 6px 8px;
}
QSpinBox {
    background-color: #1e1e24;
    border: 1px solid #2c2c35;
    border-radius: 6px;
    padding: 4px 6px;
}
QDialog { background-color: #16161a; }
"""

LIGHT_STYLE = (
    STYLE
    .replace("#16161a", "#f5f6f8")
    .replace("#1e1e24", "#ffffff")
    .replace("#e8e8ea", "#202124")
    .replace("#c8c8cf", "#3f434a")
    .replace("#6f7079", "#6b7280")
    .replace("#8a8a93", "#4b5563")
    .replace("#2c2c35", "#d8dde6")
    .replace("#3a3a44", "#c4cad4")
    .replace("#565664", "#9ca3af")
    .replace("#22222a", "#f0f2f5")
    .replace("#2f3d5c", "#dbeafe")
)


def resource_path(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def make_icon() -> QIcon:
    """Load the editable SVG icon, falling back to the generated painter icon."""
    svg_path = resource_path("assets", "write-or-die-icon.svg")
    if os.path.exists(svg_path):
        icon = QIcon(svg_path)
        if not icon.isNull():
            return icon

    pix = QPixmap(256, 256)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)

    # rounded dark tile
    p.setBrush(QBrush(QColor("#1e1e24")))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(8, 8, 240, 240, 48, 48)

    # one bold written line
    p.save()
    p.translate(30, 170)
    p.rotate(-8)
    pen = QPen(QColor("#ef4444"))
    pen.setWidth(20)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(0, 0)
    path.cubicTo(28, -38, 56, 38, 86, 0)
    path.cubicTo(116, -38, 146, 38, 178, 0)
    p.drawPath(path)
    p.restore()

    # oversized fountain pen with the nib landing on the squiggle
    p.save()
    p.translate(176, 84)
    p.rotate(45)
    p.setPen(Qt.NoPen)
    # barrel
    p.setBrush(QBrush(QColor("#c7ccd6")))
    p.drawRoundedRect(-34, -140, 68, 168, 28, 28)
    # grip band
    p.setBrush(QBrush(QColor("#9aa1ad")))
    p.drawRoundedRect(-34, -46, 68, 34, 10, 10)
    # nib (triangle) pointing down toward the squiggle
    nib = QPainterPath()
    nib.moveTo(-34, -12)
    nib.lineTo(34, -12)
    nib.lineTo(0, 76)
    nib.closeSubpath()
    p.setBrush(QBrush(QColor("#e8eaef")))
    p.drawPath(nib)
    # nib slit + breather hole
    slit = QPen(QColor("#1e1e24"))
    slit.setWidth(7)
    p.setPen(slit)
    p.drawLine(0, 8, 0, 62)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor("#1e1e24")))
    p.drawEllipse(-9, -9, 18, 18)
    p.restore()

    # compact caution badge, kept separate from the pen/squiggle composition
    badge = QPainterPath()
    badge.moveTo(202, 156)
    badge.lineTo(238, 226)
    badge.lineTo(166, 226)
    badge.closeSubpath()
    p.setPen(QPen(QColor("#1e1e24"), 8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(QBrush(QColor("#facc15")))
    p.drawPath(badge)
    p.setPen(QPen(QColor("#dc2626"), 8, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(202, 181, 202, 207)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor("#dc2626")))
    p.drawEllipse(197, 214, 10, 10)

    p.end()
    return QIcon(pix)


def main():
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)

    icon = make_icon()
    app.setWindowIcon(icon)

    win = MainWindow()
    win.setWindowIcon(icon)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
