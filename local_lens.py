import sys
import os
import tempfile
import torch
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QScrollArea, QFileDialog, QGraphicsView,
    QGraphicsScene, QComboBox, QLineEdit, QFrame, QSizePolicy, QGraphicsPolygonItem
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPointF, QTimer
from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor, QPolygonF, QFont, QCursor

# ──────────────────────────────────────────────────────────────
# LANGUAGE CONFIGURATION
# ──────────────────────────────────────────────────────────────
# You can easily add more languages here before your GitHub release.
# Note: EasyOCR codes are usually ISO 639-1 (e.g., 'en', 'uk', 'ru', 'ja', 'de', 'fr', 'es', 'pl')
LANG_PROFILES = {
    "English": ["en"],
    "Ukrainian": ["uk"],
    "Ukrainian + English": ["uk", "en"],
    "Russian": ["ru"],
    "Russian + English": ["ru", "en"],
    "Japanese": ["ja"],
    "Japanese + English": ["ja", "en"],
    "German + English": ["de", "en"],
    "French + English": ["fr", "en"],
    "Spanish + English": ["es", "en"],
    "Polish + English": ["pl", "en"],
    "Korean + English": ["ko", "en"],
    "Custom (type codes)...": [] # Special case triggers manual input
}

# ──────────────────────────────────────────────────────────────
# BACKGROUND THREAD — OCR Loader
# ──────────────────────────────────────────────────────────────
class OCRLoaderThread(QThread):
    loaded  = pyqtSignal(object, str)   # (reader, device_label)
    failed  = pyqtSignal(str)

    def __init__(self, langs):
        super().__init__()
        self.langs = langs

    def run(self):
        try:
            import easyocr
            use_gpu = torch.cuda.is_available()
            try:
                reader = easyocr.Reader(self.langs, gpu=use_gpu)
                device = "GPU (CUDA)" if use_gpu else "CPU"
            except Exception:
                reader = easyocr.Reader(self.langs, gpu=False)
                device = "CPU (fallback)"
            self.loaded.emit(reader, device)
        except Exception as e:
            self.failed.emit(str(e))

# ──────────────────────────────────────────────────────────────
# BACKGROUND THREAD — OCR Process
# ──────────────────────────────────────────────────────────────
class OCRProcessThread(QThread):
    result  = pyqtSignal(list)          # list of {bbox, text, prob}
    failed  = pyqtSignal(str)

    def __init__(self, reader, image_path):
        super().__init__()
        self.reader     = reader
        self.image_path = image_path

    def run(self):
        try:
            raw = self.reader.readtext(self.image_path, detail=1)
            boxes = [
                {"bbox": item[0], "text": item[1], "prob": item[2]}
                for item in raw if item[2] > 0.3
            ]
            self.result.emit(boxes)
        except Exception as e:
            self.failed.emit(str(e))

# ──────────────────────────────────────────────────────────────
# INTERACTIVE POLYGON (Click to copy directly from image)
# ──────────────────────────────────────────────────────────────
class TextPolygon(QGraphicsPolygonItem):
    def __init__(self, poly, text, on_copy_callback):
        super().__init__(poly)
        self.text = text
        self.on_copy_callback = on_copy_callback
        
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.normal_pen = QPen(QColor(99, 179, 255, 200), 2)
        self.hover_pen = QPen(QColor(255, 255, 255, 255), 2)
        self.normal_brush = QColor(99, 179, 255, 40)
        self.hover_brush = QColor(99, 179, 255, 120)
        
        self.setPen(self.normal_pen)
        self.setBrush(self.normal_brush)
        self.setToolTip(f"Click to copy: {text}")

    def hoverEnterEvent(self, event):
        self.setPen(self.hover_pen)
        self.setBrush(self.hover_brush)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setPen(self.normal_pen)
        self.setBrush(self.normal_brush)
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            QApplication.clipboard().setText(self.text)
            if self.on_copy_callback:
                self.on_copy_callback(self.text)
        super().mousePressEvent(event)

# ──────────────────────────────────────────────────────────────
# IMAGE VIEW — drag-and-drop + overlay polygons
# ──────────────────────────────────────────────────────────────
class ImageView(QGraphicsView):
    file_dropped = pyqtSignal(str)
    SUPPORTED = ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tiff')

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setAcceptDrops(True)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QGraphicsView {
                background: #13131f;
                border: 2px dashed #3a3a5c;
                border-radius: 12px;
            }
        """)
        self._placeholder()

    def _placeholder(self):
        self._scene.clear()
        lbl = self._scene.addText("Drop an image here\nPress Ctrl+V to paste\nor click Browse")
        lbl.setDefaultTextColor(QColor("#4a4a6a"))
        font = QFont("Segoe UI", 14)
        lbl.setFont(font)
        self.setSceneRect(self._scene.itemsBoundingRect())

    def load_image(self, path: str):
        self._scene.clear()
        pix = QPixmap(path)
        self._scene.addPixmap(pix)
        self.setSceneRect(0, 0, pix.width(), pix.height())
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def draw_boxes(self, boxes, on_copy_cb=None):
        for box in boxes:
            pts   = box["bbox"]
            poly  = QPolygonF([QPointF(x, y) for x, y in pts])
            item  = TextPolygon(poly, box["text"], on_copy_cb)
            self._scene.addItem(item)

    def clear_boxes(self):
        for item in self._scene.items():
            if isinstance(item, TextPolygon):
                self._scene.removeItem(item)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._scene.items():
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.accept()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith(self.SUPPORTED):
                self.file_dropped.emit(path)

# ──────────────────────────────────────────────────────────────
# SINGLE TEXT CARD (right panel)
# ──────────────────────────────────────────────────────────────
class TextCard(QFrame):
    def __init__(self, text: str, index: int):
        super().__init__()
        self.text = text
        self.setObjectName("TextCard")
        self.setStyleSheet("""
            #TextCard {
                background: #1c1c2e; border: 1px solid #2e2e4a;
                border-radius: 8px; padding: 2px;
            }
            #TextCard:hover { border: 1px solid #5a7fba; background: #202036; }
        """)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 8, 8)
        row.setSpacing(8)

        badge = QLabel(str(index))
        badge.setFixedWidth(24)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet("color: #4a4a6a; font-size: 11px; font-family: 'Segoe UI', monospace;")
        row.addWidget(badge)

        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #d0d8f0; font-size: 13px; font-family: 'Segoe UI', Arial;")
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(lbl, stretch=1)

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setFixedWidth(54)
        self._copy_btn.setFixedHeight(28)
        self._copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._copy_btn.setStyleSheet("""
            QPushButton {
                background: #2a2a45; color: #8899cc; border: 1px solid #3a3a5c;
                border-radius: 5px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background: #3a4a7a; color: #c0d0ff; border-color: #5a7fba; }
        """)
        self._copy_btn.clicked.connect(self._copy)
        row.addWidget(self._copy_btn)

    def _copy(self):
        QApplication.clipboard().setText(self.text)
        self._copy_btn.setText("✓")
        QTimer.singleShot(1500, lambda: self._copy_btn.setText("Copy"))

# ──────────────────────────────────────────────────────────────
# MAIN WINDOW
# ──────────────────────────────────────────────────────────────
class LocalLens(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Local Lens")
        self.resize(1050, 680)
        self.setMinimumSize(800, 520)

        self._reader       = None
        self._image_path   = None
        self._ocr_thread   = None
        self._load_thread  = None
        self._current_langs = []

        self._apply_theme()
        self._build_ui()
        self._init_default_ocr()

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #0f0f1a; color: #c8d0e8; font-family: 'Segoe UI', Arial, sans-serif;
            }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: #13131f; width: 6px; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #3a3a5c; border-radius: 3px; min-height: 20px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QComboBox, QLineEdit {
                background: #1c1c2e; color: #c8d0e8; border: 1px solid #3a3a5c;
                border-radius: 6px; padding: 6px 10px; font-size: 13px;
            }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox QAbstractItemView {
                background: #1c1c2e; color: #c8d0e8;
                selection-background-color: #2e2e4a; border: 1px solid #3a3a5c;
            }
        """)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # ── TOP BAR ──
        topbar = QWidget()
        topbar.setFixedHeight(52)
        topbar.setStyleSheet("background: #0d0d18; border-bottom: 1px solid #1e1e30;")
        trow = QHBoxLayout(topbar)
        trow.setContentsMargins(20, 0, 20, 0)
        trow.setSpacing(10)

        title = QLabel("Local Lens")
        title.setStyleSheet("font-size: 17px; font-weight: 700; color: #e0e8ff; letter-spacing: 0.5px;")
        trow.addWidget(title)

        self._status = QLabel("Ready")
        self._status.setStyleSheet("font-size: 12px; color: #5a6888;")
        trow.addWidget(self._status)

        trow.addStretch()

        lang_lbl = QLabel("Language:")
        lang_lbl.setStyleSheet("font-size: 12px; color: #5a6888;")
        trow.addWidget(lang_lbl)

        # Dropdown for presets
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(LANG_PROFILES.keys())
        self._lang_combo.setFixedWidth(160)
        self._lang_combo.currentTextChanged.connect(self._on_combo_changed)
        trow.addWidget(self._lang_combo)

        # Hidden custom input field
        self._custom_input = QLineEdit()
        self._custom_input.setPlaceholderText("e.g. it, pt")
        self._custom_input.setFixedWidth(100)
        self._custom_input.hide()
        self._custom_input.returnPressed.connect(self._apply_custom_langs)
        trow.addWidget(self._custom_input)

        # Hidden apply button for custom input
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setFixedHeight(34)
        self._apply_btn.hide()
        self._apply_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._apply_btn.setStyleSheet("""
            QPushButton { background: #2a2a45; color: #8899cc; border: 1px solid #3a3a5c;
                          border-radius: 6px; font-size: 12px; padding: 0 10px; }
            QPushButton:hover { background: #3a4a7a; color: #c0d0ff; border-color: #5a7fba; }
        """)
        self._apply_btn.clicked.connect(self._apply_custom_langs)
        trow.addWidget(self._apply_btn)

        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setFixedHeight(34)
        self._browse_btn.setFixedWidth(80)
        self._browse_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._browse_btn.setStyleSheet("""
            QPushButton { background: #2e4a8a; color: #c8d8ff; border: none;
                          border-radius: 6px; font-size: 13px; font-weight: 600; }
            QPushButton:hover { background: #3a5aaa; }
            QPushButton:disabled { background: #1e1e30; color: #3a3a5c; }
        """)
        self._browse_btn.clicked.connect(self._browse)
        trow.addWidget(self._browse_btn)

        self._scan_btn = QPushButton("Scan")
        self._scan_btn.setFixedHeight(34)
        self._scan_btn.setFixedWidth(70)
        self._scan_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._scan_btn.setEnabled(False)
        self._scan_btn.setStyleSheet("""
            QPushButton { background: #4a7fa0; color: #e0f0ff; border: none;
                          border-radius: 6px; font-size: 13px; font-weight: 700; }
            QPushButton:hover { background: #5a9fc0; }
            QPushButton:disabled { background: #1e1e30; color: #3a3a5c; }
        """)
        self._scan_btn.clicked.connect(self._scan)
        trow.addWidget(self._scan_btn)

        main.addWidget(topbar)

        # ── BODY ──
        body = QWidget()
        body_row = QHBoxLayout(body)
        body_row.setContentsMargins(16, 16, 16, 16)
        body_row.setSpacing(16)

        self._img_view = ImageView()
        self._img_view.file_dropped.connect(self._load_image)
        body_row.addWidget(self._img_view, stretch=6)

        right = QWidget()
        right.setFixedWidth(320)
        right_col = QVBoxLayout(right)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(8)

        hdr = QWidget()
        hdr.setStyleSheet("background: #13131f; border-radius: 8px;")
        hdr_row = QHBoxLayout(hdr)
        hdr_row.setContentsMargins(12, 8, 12, 8)

        hdr_lbl = QLabel("Extracted Text")
        hdr_lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #8899bb;")
        hdr_row.addWidget(hdr_lbl)
        hdr_row.addStretch()

        self._copy_all_btn = QPushButton("Copy All")
        self._copy_all_btn.setFixedHeight(26)
        self._copy_all_btn.setEnabled(False)
        self._copy_all_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._copy_all_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #5a7fba; border: 1px solid #2e2e4a;
                          border-radius: 5px; font-size: 11px; padding: 0 8px; }
            QPushButton:hover { color: #8ab0ff; border-color: #4a5a8a; }
            QPushButton:disabled { color: #2e2e4a; border-color: #1e1e30; }
        """)
        self._copy_all_btn.clicked.connect(self._copy_all)
        hdr_row.addWidget(self._copy_all_btn)
        right_col.addWidget(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent;")

        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet("background: transparent;")
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch()

        self._empty_lbl = QLabel("No text detected yet.\nLoad an image and press Scan.")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet("color: #2e2e4a; font-size: 12px; padding: 40px 0;")
        self._cards_layout.insertWidget(0, self._empty_lbl)

        scroll.setWidget(self._cards_widget)
        right_col.addWidget(scroll, stretch=1)
        body_row.addWidget(right, stretch=0)
        main.addWidget(body, stretch=1)

        # ── STATUS BAR ──
        statusbar = QWidget()
        statusbar.setFixedHeight(28)
        statusbar.setStyleSheet("background: #0d0d18; border-top: 1px solid #1a1a2a;")
        srow = QHBoxLayout(statusbar)
        srow.setContentsMargins(20, 0, 20, 0)

        self._bar_left = QLabel("Ready")
        self._bar_left.setStyleSheet("font-size: 11px; color: #3a3a5c;")
        srow.addWidget(self._bar_left)
        srow.addStretch()

        self._bar_right = QLabel("Local · Offline")
        self._bar_right.setStyleSheet("font-size: 11px; color: #3a3a5c;")
        srow.addWidget(self._bar_right)
        main.addWidget(statusbar)

    # ── Clipboard Event ───────────────────────────────────────
    def keyPressEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V:
            self._paste_from_clipboard()
        super().keyPressEvent(event)

    def _paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime.hasImage():
            img = clipboard.image()
            fd, path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            img.save(path, "PNG")
            self._load_image(path)
            self._bar_left.setText("Image pasted from clipboard")

    # ── Language Logic ─────────────────────────────────────────
    def _init_default_ocr(self):
        # Default load is whatever is first in the combobox
        default_key = self._lang_combo.currentText()
        self._current_langs = LANG_PROFILES[default_key]
        self._load_ocr(self._current_langs)

    def _on_combo_changed(self, text):
        if text == "Custom (type codes)...":
            self._custom_input.show()
            self._apply_btn.show()
            # Don't load yet, wait for user to click Apply
        else:
            self._custom_input.hide()
            self._apply_btn.hide()
            langs = LANG_PROFILES[text]
            if langs != self._current_langs:
                self._current_langs = langs
                self._load_ocr(langs)

    def _apply_custom_langs(self):
        raw_text = self._custom_input.text()
        langs = [lang.strip().lower() for lang in raw_text.split(',') if lang.strip()]
        if not langs:
            langs = ["en"]
            self._custom_input.setText("en")
        
        if langs != self._current_langs:
            self._current_langs = langs
            self._load_ocr(langs)

    # ── OCR Loading ───────────────────────────────────────────
    def _load_ocr(self, langs):
        self._set_ready(False)
        self._status.setText(f"Loading OCR engine ({', '.join(langs)})…")
        self._load_thread = OCRLoaderThread(langs)
        self._load_thread.loaded.connect(self._on_ocr_loaded)
        self._load_thread.failed.connect(self._on_error)
        self._load_thread.start()

    def _on_ocr_loaded(self, reader, device):
        self._reader = reader
        self._status.setText(f"Ready  ·  {device}")
        self._bar_right.setText(f"Local · {device}")
        self._set_ready(True)

    # ── Image Loading ─────────────────────────────────────────
    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tiff)"
        )
        if path:
            self._load_image(path)

    def _load_image(self, path: str):
        self._image_path = path
        self._img_view.load_image(path)
        self._clear_results()
        short = os.path.basename(path)
        self._bar_left.setText(short)
        self._scan_btn.setEnabled(bool(self._reader))

    # ── Scan ──────────────────────────────────────────────────
    def _scan(self):
        if not self._reader or not self._image_path:
            return
        self._set_ready(False)
        self._status.setText("Scanning…")
        self._bar_left.setText("Scanning image…")
        self._img_view.clear_boxes()
        self._clear_results()

        self._ocr_thread = OCRProcessThread(self._reader, self._image_path)
        self._ocr_thread.result.connect(self._on_scan_done)
        self._ocr_thread.failed.connect(self._on_error)
        self._ocr_thread.start()

    def _on_scan_done(self, boxes):
        self._set_ready(True)
        count = len(boxes)
        self._status.setText(f"Found {count} text region{'s' if count != 1 else ''}")
        self._bar_left.setText(f"{count} regions detected")

        if not boxes:
            self._empty_lbl.setText("No text found in this image.")
            return

        self._empty_lbl.hide()
        self._img_view.draw_boxes(boxes, self._on_polygon_copied)

        for i, box in enumerate(boxes, 1):
            card = TextCard(box["text"], i)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)

        self._copy_all_btn.setEnabled(True)

    def _on_polygon_copied(self, text):
        short_text = text[:30] + "..." if len(text) > 30 else text
        self._bar_left.setText(f"Copied: {short_text}")

    # ── Copy All ──────────────────────────────────────────────
    def _copy_all(self):
        texts = []
        for i in range(self._cards_layout.count()):
            item = self._cards_layout.itemAt(i)
            if item and isinstance(item.widget(), TextCard):
                texts.append(item.widget().text)
        if texts:
            QApplication.clipboard().setText("\n".join(texts))
            self._copy_all_btn.setText("✓ Copied")
            QTimer.singleShot(1800, lambda: self._copy_all_btn.setText("Copy All"))

    # ── Helpers ───────────────────────────────────────────────
    def _clear_results(self):
        for i in reversed(range(self._cards_layout.count())):
            item = self._cards_layout.itemAt(i)
            if item and isinstance(item.widget(), TextCard):
                item.widget().deleteLater()
                self._cards_layout.removeItem(item)
        self._empty_lbl.setText("No text detected yet.\nLoad an image and press Scan.")
        self._empty_lbl.show()
        self._copy_all_btn.setEnabled(False)

    def _set_ready(self, ready: bool):
        self._scan_btn.setEnabled(ready and bool(self._image_path))
        self._browse_btn.setEnabled(True)
        self._lang_combo.setEnabled(ready)
        self._custom_input.setEnabled(ready)
        self._apply_btn.setEnabled(ready)

    def _on_error(self, msg: str):
        self._status.setText(f"Error: {msg}")
        self._bar_left.setText("Error — see status bar")
        self._set_ready(True)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Local Lens")
    window = LocalLens()
    window.show()
    sys.exit(app.exec())