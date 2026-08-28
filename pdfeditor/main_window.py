"""Main application window: menus, toolbar, thumbnails, and status bar."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QEvent, QMimeData, QPoint, QSettings, Qt, Signal
from PySide6.QtGui import (
    QAction, QColor, QCursor, QDrag, QGuiApplication, QIcon, QImage, QKeySequence,
    QPainter, QPixmap,
)
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import (
    ai, convert, external, imaging, ocr, operations as ops, production, signing,
)
from .document import DocumentError, PdfDocument
from .viewer import PageView, Tool

_PDF_FILTER = "PDF files (*.pdf);;All files (*)"


# In-process registry for a tab being dragged between windows. The drag's
# MIME payload carries only a token; the widget itself is looked up here.
_TAB_MIME = "application/x-pdfeditor-tab"
_DRAG_TABS: dict = {}


class _DetachableTabBar(QTabBar):
    """A tab bar whose tabs can be reordered, dragged between windows, or
    torn off into a new window."""

    detach_requested = Signal(int, QPoint)
    menu_requested = Signal(int, QPoint)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_index = -1
        self.setAcceptDrops(True)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        index = self.tabAt(event.pos())
        if index >= 0:
            self.menu_requested.emit(index, event.globalPos())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_index = self.tabAt(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        # Dragging a tab clear of the bar starts a cross-window drag; drop it
        # on another window's tab bar to move it there, or in empty space to
        # tear it off into a new window. Horizontal drags still reorder.
        if self._drag_index >= 0 and (event.buttons() & Qt.LeftButton):
            gy = event.globalPosition().toPoint().y()
            top = self.mapToGlobal(QPoint(0, 0)).y()
            if gy < top - 30 or gy > top + self.height() + 30:
                index = self._drag_index
                self._drag_index = -1
                self._start_cross_drag(index)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_index = -1
        super().mouseReleaseEvent(event)

    def _start_cross_drag(self, index: int) -> None:
        tabw = self.parent()
        main = getattr(tabw, "main_window", None)
        tab = tabw.widget(index)
        if tab is None or main is None:
            return
        token = str(id(tab))
        _DRAG_TABS[token] = {
            "tab": tab, "source": main,
            "title": tabw.tabText(index), "tip": tabw.tabToolTip(index),
            "consumed": False,
        }
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_TAB_MIME, token.encode())
        drag.setMimeData(mime)
        try:
            drag.setPixmap(self.grab(self.tabRect(index)))
        except Exception:  # pragma: no cover
            pass
        self.releaseMouse()
        drag.exec(Qt.MoveAction)
        entry = _DRAG_TABS.pop(token, None)
        if entry and not entry["consumed"]:
            # Dropped somewhere that didn't accept it -> new window, but only
            # if it isn't already the sole tab of its window.
            if tabw.count() > 1:
                main._detach_widget(tab, entry["title"], entry["tip"], QCursor.pos())

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(_TAB_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(_TAB_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(_TAB_MIME):
            return
        token = bytes(event.mimeData().data(_TAB_MIME).data()).decode()
        entry = _DRAG_TABS.get(token)
        target = getattr(self.parent(), "main_window", None)
        if entry is None or target is None:
            return
        index = self.tabAt(event.position().toPoint())
        if index < 0:
            index = self.count()
        target._accept_tab(entry["tab"], entry["title"], entry["tip"], index)
        entry["consumed"] = True
        event.setDropAction(Qt.MoveAction)
        event.acceptProposedAction()


class _DetachableTabWidget(QTabWidget):
    """A tab widget with closable, movable, detachable document tabs."""

    detach_requested = Signal(int, QPoint)
    menu_requested = Signal(int, QPoint)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.main_window = None  # set by MainWindow
        bar = _DetachableTabBar(self)
        self.setTabBar(bar)
        self.setMovable(True)
        self.setTabsClosable(True)
        self.setDocumentMode(True)
        self.setElideMode(Qt.ElideRight)
        bar.detach_requested.connect(self.detach_requested)
        bar.menu_requested.connect(self.menu_requested)


class DocumentTab(QWidget):
    """Everything specific to one open PDF: its view, thumbnail strip,
    Comments panel, undo/redo history and per-document state.

    Cross-tab signals call through ``self.win`` (the owning window) via
    small lambdas, so moving a tab to another window only needs
    ``self.win`` reassigned — no reconnecting.
    """

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.doc: Optional[PdfDocument] = None
        self.pending_signature: Optional[str] = None
        self.undo: list[bytes] = []
        self.redo: list[bytes] = []
        self.left_size = 190
        self.right_size = 260

        self.view = PageView()
        self.view.edited.connect(lambda: self.win._on_edited())
        self.view.place_requested.connect(lambda p, x, y: self.win._on_place_requested(p, x, y))
        self.view.rect_selected.connect(lambda p, a, b, c, d: self.win._on_rect_selected(p, a, b, c, d))
        self.view.edit_started.connect(lambda: self.win._checkpoint())
        self.view.area_copied.connect(lambda: self.win._on_area_copied())
        self.view.objects_changed.connect(lambda: self.win._on_objects_changed())
        self.view.annotations_changed.connect(lambda: self.win._on_annotations_changed())
        self.view.annot_edit_requested.connect(lambda pg, xr: self.win._on_annot_edit_requested(pg, xr))
        self.view.edit_text_requested.connect(lambda pg, x, y: self.win._on_edit_text_requested(pg, x, y))
        self.view.page_changed.connect(lambda i: self.win._on_visible_page_changed(i))
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(lambda pos: self.win._show_context_menu(pos))
        self.view.installEventFilter(self)  # Ctrl+wheel zoom

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.view)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setWidgetResizable(False)
        self.scroll.verticalScrollBar().valueChanged.connect(self.view.notify_scrolled)

        self.thumbs = QListWidget()
        self.thumbs.setMinimumWidth(96)
        self.thumbs.setIconSize(QPixmap(140, 180).size())
        self.thumbs.currentRowChanged.connect(lambda r: self.win._on_thumb_selected(r))
        self.thumbs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(lambda pos: self.win._thumb_context_menu(pos))

        self.thumb_rail = QToolButton()
        self.thumb_rail.setObjectName("thumbRail")
        self.thumb_rail.setAutoRaise(True)
        self.thumb_rail.setIcon(win._icon("prev"))
        self.thumb_rail.setToolTip("Hide page thumbnails")
        self.thumb_rail.setCursor(Qt.PointingHandCursor)
        self.thumb_rail.setFixedWidth(18)
        self.thumb_rail.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.thumb_rail.clicked.connect(lambda: self.win._toggle_thumbnails())

        self.comments_panel = self._build_comments_panel(win)

        self.comments_rail = QToolButton()
        self.comments_rail.setObjectName("thumbRail")
        self.comments_rail.setAutoRaise(True)
        self.comments_rail.setIcon(win._icon("next"))
        self.comments_rail.setToolTip("Hide comments")
        self.comments_rail.setCursor(Qt.PointingHandCursor)
        self.comments_rail.setFixedWidth(18)
        self.comments_rail.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.comments_rail.clicked.connect(lambda: self.win._toggle_comments())

        self.left_wrap = QWidget()
        lw = QHBoxLayout(self.left_wrap)
        lw.setContentsMargins(0, 0, 0, 0)
        lw.setSpacing(0)
        lw.addWidget(self.thumbs, 1)
        lw.addWidget(self.thumb_rail)

        self.right_wrap = QWidget()
        rw = QHBoxLayout(self.right_wrap)
        rw.setContentsMargins(0, 0, 0, 0)
        rw.setSpacing(0)
        rw.addWidget(self.comments_rail)
        rw.addWidget(self.comments_panel, 1)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("mainSplitter")
        self.splitter.setHandleWidth(5)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.left_wrap)
        self.splitter.addWidget(self.scroll)
        self.splitter.addWidget(self.right_wrap)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([190, 820, 260])

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.splitter)

        # Start with both side panels collapsed to just their rails; the user
        # expands them with the rails, the View menu, or F9 / F10.
        self.thumbs.setVisible(False)
        self.comments_panel.setVisible(False)
        self.thumb_rail.setIcon(win._icon("next"))
        self.thumb_rail.setToolTip("Show page thumbnails")
        self.comments_rail.setIcon(win._icon("prev"))
        self.comments_rail.setToolTip("Show comments")
        self.splitter.setSizes([18, 1000, 18])

    def _build_comments_panel(self, win: "MainWindow") -> QWidget:
        panel = QWidget()
        panel.setObjectName("commentsPanel")
        panel.setMinimumWidth(150)
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        header = QWidget()
        hb = QHBoxLayout(header)
        hb.setContentsMargins(10, 8, 8, 8)
        title = QLabel("Comments")
        title.setObjectName("commentsTitle")
        hb.addWidget(title)
        hb.addStretch(1)
        author_btn = QToolButton()
        author_btn.setText("Author…")
        author_btn.setToolTip("Set the author name for new comments")
        author_btn.setCursor(Qt.PointingHandCursor)
        author_btn.clicked.connect(lambda: self.win._set_comment_author())
        hb.addWidget(author_btn)
        col.addWidget(header)

        self.comments = QListWidget()
        self.comments.setObjectName("commentsList")
        self.comments.setWordWrap(True)
        self.comments.itemClicked.connect(lambda it: self.win._on_comment_clicked(it))
        self.comments.itemDoubleClicked.connect(lambda it: self.win._on_comment_double_clicked(it))
        self.comments.setContextMenuPolicy(Qt.CustomContextMenu)
        self.comments.customContextMenuRequested.connect(lambda pos: self.win._comment_context_menu(pos))
        col.addWidget(self.comments, 1)

        self.comments_empty = QLabel(
            "No comments yet.\nUse the Note, Text or\nHighlight tools to add some."
        )
        self.comments_empty.setObjectName("commentsEmpty")
        self.comments_empty.setAlignment(Qt.AlignCenter)
        self.comments_empty.setWordWrap(True)
        col.addWidget(self.comments_empty)
        col.setStretchFactor(self.comments, 1)
        return panel

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.view and event.type() == QEvent.Wheel and self.doc:
            if event.modifiers() & Qt.ControlModifier:
                self.win._zoom_at_cursor(event)
                return True
        return super().eventFilter(obj, event)


class MainWindow(QMainWindow):
    """Top-level window tying the document model to the viewer widget."""

    # Keep detached windows alive (they'd be garbage-collected otherwise).
    _windows: list = []

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF Viewer & Editor")
        self.resize(1200, 800)
        if self not in MainWindow._windows:
            MainWindow._windows.append(self)

        self._max_history = 40
        # Author name stamped on new comments/notes/highlights (persisted).
        self._author = self._load_author()

        # One tab per open PDF; tabs can be reordered, closed, and torn off
        # into their own window.
        self._tabs = _DetachableTabWidget()
        self._tabs.main_window = self
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self._tabs.detach_requested.connect(self._detach_tab)
        self._tabs.menu_requested.connect(self._tab_menu)
        self.setCentralWidget(self._tabs)

        # Editable page indicator (added to the toolbar in _build_toolbar):
        # type a page number in the box (or use its arrows) to jump there. The
        # total page count sits in a separate, non-editable label after it.
        self._page_prefix = QLabel(" Page ")
        self._page_spin = QSpinBox()
        self._page_spin.setObjectName("pageSpin")
        self._page_spin.setMinimum(0)
        self._page_spin.setMaximum(0)
        self._page_spin.setKeyboardTracking(False)  # fire only on Enter/arrows
        self._page_spin.setAlignment(Qt.AlignHCenter)
        self._page_spin.setFixedWidth(72)
        self._page_spin.setToolTip("Current page — type a number to jump there")
        self._page_spin.valueChanged.connect(self._on_page_spin_changed)
        self._page_total = QLabel("/ 0 ")
        self._page_total.setObjectName("pageTotal")

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._update_enabled()

    # -- open documents (one per tab) -----------------------------------

    @property
    def _tab(self) -> Optional[DocumentTab]:
        w = self._tabs.currentWidget()
        return w if isinstance(w, DocumentTab) else None

    # These proxy the "current document" widgets/state so the rest of the
    # window can keep referring to self._doc, self._view, ... unchanged.
    @property
    def _doc(self) -> Optional[PdfDocument]:
        t = self._tab
        return t.doc if t else None

    @_doc.setter
    def _doc(self, value) -> None:
        t = self._tab
        if t is not None:
            t.doc = value

    @property
    def _view(self):
        t = self._tab
        return t.view if t else None

    @property
    def _thumbs(self):
        t = self._tab
        return t.thumbs if t else None

    @property
    def _comments(self):
        t = self._tab
        return t.comments if t else None

    @property
    def _comments_empty(self):
        t = self._tab
        return t.comments_empty if t else None

    @property
    def _comments_panel(self):
        t = self._tab
        return t.comments_panel if t else None

    @property
    def _comments_rail(self):
        t = self._tab
        return t.comments_rail if t else None

    @property
    def _thumb_rail(self):
        t = self._tab
        return t.thumb_rail if t else None

    @property
    def _scroll(self):
        t = self._tab
        return t.scroll if t else None

    @property
    def _splitter(self):
        t = self._tab
        return t.splitter if t else None

    @property
    def _undo(self) -> list:
        t = self._tab
        return t.undo if t else []

    @property
    def _redo(self) -> list:
        t = self._tab
        return t.redo if t else []

    @property
    def _pending_signature(self):
        t = self._tab
        return t.pending_signature if t else None

    @_pending_signature.setter
    def _pending_signature(self, value) -> None:
        t = self._tab
        if t is not None:
            t.pending_signature = value

    @property
    def _left_size(self) -> int:
        t = self._tab
        return t.left_size if t else 190

    @_left_size.setter
    def _left_size(self, value) -> None:
        t = self._tab
        if t is not None:
            t.left_size = value

    @property
    def _right_size(self) -> int:
        t = self._tab
        return t.right_size if t else 260

    @_right_size.setter
    def _right_size(self, value) -> None:
        t = self._tab
        if t is not None:
            t.right_size = value

    def _add_tab(self, doc: PdfDocument) -> None:
        """Open a document in a new tab and make it current."""
        tab = DocumentTab(self)
        index = self._tabs.addTab(tab, "…")
        self._tabs.setCurrentIndex(index)
        self._load_document(doc)

    def open_files_and_raise(self, paths: list) -> None:
        """Open files forwarded from a second launch, then surface this window."""
        for path in paths:
            if path.strip():
                self.open_document(path.strip())
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # -- UI construction ------------------------------------------------

    def _build_actions(self) -> None:
        self.act_new = QAction("&New", self, shortcut=QKeySequence.New, triggered=self.new_document)
        self.act_open = QAction("&Open…", self, shortcut=QKeySequence.Open, triggered=self.open_document)
        self.act_save = QAction("&Save", self, shortcut=QKeySequence.Save, triggered=self.save_document)
        self.act_save_as = QAction("Save &As…", self, shortcut=QKeySequence.SaveAs, triggered=self.save_document_as)
        self.act_print = QAction("&Print…", self, shortcut=QKeySequence.Print, triggered=self._print)
        self.act_quit = QAction("&Quit", self, shortcut=QKeySequence.Quit, triggered=self.close)

        self.act_undo = QAction("&Undo", self, triggered=self._undo_action)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_redo = QAction("&Redo", self, triggered=self._redo_action)
        self.act_redo.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Y")])

        self.act_zoom_in = QAction("Zoom &In", self, shortcut=QKeySequence.ZoomIn, triggered=lambda: self._zoom_by(1.25))
        self.act_zoom_out = QAction("Zoom &Out", self, shortcut=QKeySequence.ZoomOut, triggered=lambda: self._zoom_by(0.8))
        self.act_fit_width = QAction("&Fit Width", self, triggered=self._fit_width)
        self.act_toggle_thumbs = QAction("Toggle &Thumbnails", self, shortcut="F9", triggered=self._toggle_thumbnails)
        self.act_toggle_comments = QAction("Toggle &Comments", self, shortcut="F10", triggered=self._toggle_comments)
        self.act_set_author = QAction("Set Comment &Author…", self, triggered=self._set_comment_author)
        self.act_rename_author = QAction("&Rename Author in PDF…", self, triggered=self._rename_existing_author)

        self.act_prev = QAction("&Previous Page", self, shortcut="PgUp", triggered=lambda: self._go_page(self._view.page_index - 1))
        self.act_next = QAction("&Next Page", self, shortcut="PgDown", triggered=lambda: self._go_page(self._view.page_index + 1))
        self.act_goto = QAction("&Go to Page…", self, shortcut="Ctrl+G", triggered=self._goto_page)

        self.act_insert_page = QAction("Insert &Blank Page", self, triggered=self._insert_blank_page)
        self.act_delete_page = QAction("&Delete Page", self, triggered=self._delete_page)
        self.act_rotate_cw = QAction("Rotate &Right", self, triggered=lambda: self._rotate(90))
        self.act_rotate_ccw = QAction("Rotate &Left", self, triggered=lambda: self._rotate(-90))
        self.act_append = QAction("&Append PDF…", self, triggered=self._append_pdf)
        self.act_extract = QAction("E&xtract Current Page…", self, triggered=self._extract_page)

        self.act_search = QAction("&Find…", self, shortcut=QKeySequence.Find, triggered=self._search)
        self.act_copy = QAction("&Copy Selected Text", self, shortcut=QKeySequence.Copy, triggered=self._copy_text)
        self.act_add_image = QAction("Insert &Image…", self, triggered=lambda: self._set_tool(Tool.IMAGE))
        self.act_ink_color = QAction("Drawing &Color…", self, triggered=self._pick_ink_color)

        # Convert / combine
        self.act_combine = QAction("&Combine Files into PDF…", self, triggered=self._combine_files)
        self.act_images_to_pdf = QAction("Images to PD&F…", self, triggered=self._images_to_pdf)
        self.act_optimize = QAction("&Reduce File Size…", self, triggered=self._optimize)
        self.act_split = QAction("&Split Document…", self, triggered=self._split)
        self.act_compare = QAction("Co&mpare Two PDFs…", self, triggered=self._compare)

        # Document tools
        self.act_watermark = QAction("Add &Watermark…", self, triggered=self._add_watermark)
        self.act_header_footer = QAction("Add &Header/Footer…", self, triggered=self._add_header_footer)
        self.act_bates = QAction("Add &Bates Numbering…", self, triggered=self._add_bates)
        self.act_crop = QAction("Cro&p Page (drag)…", self, triggered=lambda: self._begin_tool(Tool.CROP, "Drag a rectangle to crop this page."))
        self.act_bookmark = QAction("Add Book&mark Here…", self, triggered=self._add_bookmark)
        self.act_link = QAction("Add Web &Link (drag)…", self, triggered=lambda: self._begin_tool(Tool.LINK, "Drag a rectangle for the clickable link area."))
        self.act_attach = QAction("Attach &File…", self, triggered=self._attach_file)
        self.act_extract_images = QAction("&Extract All Images…", self, triggered=self._extract_images)

        # Forms
        self.act_add_field = QAction("Add Text &Field…", self, triggered=self._add_form_field)
        self.act_fill_field = QAction("Fill Form Fiel&d…", self, triggered=self._fill_form_field)
        self.act_flatten = QAction("Fla&tten (bake in edits)…", self, triggered=self._flatten)

        # Security
        self.act_encrypt = QAction("&Password Protect (encrypt)…", self, triggered=self._encrypt)
        self.act_search_redact = QAction("Search && &Redact…", self, triggered=self._search_and_redact)
        self.act_sanitize = QAction("&Sanitize (remove hidden data)…", self, triggered=self._sanitize)
        self.act_signature = QAction("Insert Si&gnature / Initials…", self, triggered=self._insert_signature)
        self.act_make_cert = QAction("Create Self-Signed &Certificate…", self, triggered=self._make_cert)
        self.act_sign = QAction("Digitally &Sign (certificate)…", self, triggered=self._sign)

        # Convert / scan / production / AI
        self.act_office = QAction("Create PDF from &Office File…", self, triggered=self._office_to_pdf)
        self.act_ocr = QAction("&OCR — Make Searchable…", self, triggered=self._ocr)
        self.act_pdfa = QAction("Convert to PDF/&A (archival)…", self, triggered=self._to_pdfa)
        self.act_preflight = QAction("&Preflight (print check)…", self, triggered=self._preflight)
        self.act_ai_summary = QAction("&Summarize Document", self, triggered=self._ai_summarize)
        self.act_ai_ask = QAction("&Ask a Question…", self, triggered=self._ai_ask)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m_file = mb.addMenu("&File")
        m_file.addActions([self.act_new, self.act_open, self.act_save, self.act_save_as, self.act_print])
        m_file.addSeparator()
        m_file.addActions([self.act_combine, self.act_images_to_pdf, self.act_office, self.act_append, self.act_extract])
        m_file.addSeparator()
        m_file.addActions([self.act_optimize, self.act_split, self.act_compare])
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

        m_editm = mb.addMenu("&Edit")
        m_editm.addActions([self.act_undo, self.act_redo])
        m_editm.addSeparator()
        m_editm.addAction(self.act_copy)
        m_editm.addSeparator()
        m_editm.addAction(self.act_set_author)
        m_editm.addAction(self.act_rename_author)

        m_view = mb.addMenu("&View")
        m_view.addActions([self.act_zoom_in, self.act_zoom_out, self.act_fit_width])
        m_view.addSeparator()
        m_view.addAction(self.act_toggle_thumbs)
        m_view.addAction(self.act_toggle_comments)
        m_view.addSeparator()
        m_view.addActions([self.act_prev, self.act_next, self.act_goto])

        m_page = mb.addMenu("&Page")
        m_page.addActions([self.act_insert_page, self.act_delete_page])
        m_page.addSeparator()
        m_page.addActions([self.act_rotate_cw, self.act_rotate_ccw])

        m_doc = mb.addMenu("&Document")
        m_doc.addActions([self.act_watermark, self.act_header_footer, self.act_bates])
        m_doc.addSeparator()
        m_doc.addActions([self.act_crop, self.act_bookmark, self.act_link, self.act_attach])
        m_doc.addSeparator()
        m_doc.addAction(self.act_extract_images)

        m_forms = mb.addMenu("F&orms")
        m_forms.addActions([self.act_add_field, self.act_fill_field, self.act_flatten])

        m_secure = mb.addMenu("&Secure")
        m_secure.addAction(self.act_signature)
        m_secure.addSeparator()
        m_secure.addActions([self.act_encrypt, self.act_search_redact, self.act_sanitize])
        m_secure.addSeparator()
        m_secure.addActions([self.act_make_cert, self.act_sign])

        m_scan = mb.addMenu("Sca&n")
        m_scan.addActions([self.act_ocr, self.act_pdfa, self.act_preflight])

        m_ai = mb.addMenu("&AI")
        m_ai.addActions([self.act_ai_summary, self.act_ai_ask])

        m_edit = mb.addMenu("&Tools")
        m_edit.addActions([self.act_copy, self.act_search, self.act_add_image, self.act_ink_color])

    def _icon(self, name: str) -> QIcon:
        """Load a bundled minimalist icon by name (SVG), or a theme fallback."""
        path = os.path.join(
            os.path.dirname(__file__), "..", "packaging", "icons", "toolbar", name + ".svg"
        )
        if os.path.exists(path):
            return QIcon(path)
        return QIcon.fromTheme(name)

    def _assign_icons(self) -> None:
        """Give toolbar actions our minimalist icons and a hover tooltip."""
        icons = {
            self.act_open: "open", self.act_save: "save", self.act_print: "print",
            self.act_undo: "undo", self.act_redo: "redo",
            self.act_zoom_in: "zoom-in", self.act_zoom_out: "zoom-out",
            self.act_fit_width: "fit-width", self.act_prev: "prev", self.act_next: "next",
            self.act_rotate_cw: "rotate-right", self.act_rotate_ccw: "rotate-left",
            self.act_delete_page: "delete", self.act_signature: "signature",
            self.act_search: "search",
        }
        for act, name in icons.items():
            act.setIcon(self._icon(name))
            # Hover tooltip = the action's label (mnemonics/ellipsis stripped).
            act.setToolTip(act.text().replace("&", "").replace("…", ""))

    def _build_toolbar(self) -> None:
        self._assign_icons()
        tb = QToolBar("Main")
        tb.setMovable(False)
        # We ship our own icons, so always use icon-only with hover tooltips.
        icon_style = Qt.ToolButtonIconOnly
        tb.setToolButtonStyle(icon_style)
        self.addToolBar(tb)

        tb.addAction(self.act_open)
        # Save is a split button: click = Save, dropdown arrow = Save As.
        save_btn = QToolButton()
        save_btn.setDefaultAction(self.act_save)
        save_btn.setToolButtonStyle(icon_style)
        save_btn.setPopupMode(QToolButton.MenuButtonPopup)
        save_menu = QMenu(save_btn)
        save_menu.addAction(self.act_save_as)
        save_btn.setMenu(save_menu)
        self._save_btn = save_btn
        tb.addWidget(save_btn)
        tb.addAction(self.act_print)
        tb.addSeparator()
        tb.addActions([self.act_undo, self.act_redo])
        tb.addSeparator()
        tb.addActions([self.act_zoom_out, self.act_fit_width, self.act_zoom_in])
        tb.addSeparator()
        tb.addActions([self.act_prev, self.act_next])
        tb.addWidget(self._page_prefix)
        tb.addWidget(self._page_spin)
        tb.addWidget(self._page_total)
        tb.addSeparator()

        # Tool selector.
        self._tool_box = QComboBox()
        for label, tool in [
            ("Select Text", Tool.SELECT),
            ("Hand (pan)", Tool.HAND),
            ("Edit Text", Tool.EDIT_TEXT),
            ("Text", Tool.TEXT),
            ("Sticky Note", Tool.NOTE),
            ("Highlight", Tool.HIGHLIGHT),
            ("Rectangle", Tool.RECT),
            ("Draw", Tool.INK),
            ("Redact", Tool.REDACT),
            ("Crop", Tool.CROP),
            ("Link", Tool.LINK),
        ]:
            self._tool_box.addItem(label, tool)
        self._tool_box.currentIndexChanged.connect(
            lambda i: self._set_tool(self._tool_box.itemData(i))
        )
        tb.addWidget(QLabel(" Tool: "))
        tb.addWidget(self._tool_box)
        tb.addSeparator()
        tb.addActions([self.act_rotate_ccw, self.act_rotate_cw, self.act_delete_page])
        tb.addSeparator()
        tb.addActions([self.act_signature, self.act_search])

    # -- document lifecycle --------------------------------------------

    def new_document(self) -> None:
        self._add_tab(PdfDocument.new())

    def open_document(self, path: Optional[str] = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", _PDF_FILTER)
        if not path:
            return
        # If this file is already open in a tab, just switch to it.
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if isinstance(tab, DocumentTab) and tab.doc and tab.doc.path == path:
                self._tabs.setCurrentIndex(i)
                return
        try:
            doc = PdfDocument.open(path)
        except DocumentError as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        if doc.is_encrypted:
            pw, ok = QInputDialog.getText(self, "Password", "This PDF is encrypted:")
            if not ok or not doc.authenticate(pw):
                QMessageBox.warning(self, "Locked", "Could not unlock the document.")
                doc.close()
                return
        self._add_tab(doc)

    def save_document(self) -> bool:
        if not self._doc:
            return False
        if not self._doc.path:
            return self.save_document_as()
        try:
            overlays = self._view.overlay_objects()
            if overlays:
                self._doc.save_with_overlays(self._doc.path, overlays)
            else:
                self._doc.save()
        except DocumentError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self._update_title()
        self.statusBar().showMessage("Saved.", 3000)
        return True

    def save_document_as(self) -> bool:
        if not self._doc:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF As", "", _PDF_FILTER)
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            overlays = self._view.overlay_objects()
            if overlays:
                self._doc.save_with_overlays(path, overlays)
            else:
                self._doc.save(path)
        except DocumentError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self._update_title()
        self.statusBar().showMessage(f"Saved to {os.path.basename(path)}.", 3000)
        return True

    def _load_document(self, doc: PdfDocument) -> None:
        """Load a document into the current tab."""
        t = self._tab
        if t is None:
            self._add_tab(doc)
            return
        if t.doc is not None and t.doc is not doc:
            t.doc.close()
        t.doc = doc
        doc.author = self._author
        t.undo.clear()
        t.redo.clear()
        self._view.set_document(doc)
        self._view.fit_width(self._scroll.viewport().width())
        self._rebuild_thumbnails()
        self._rebuild_comments()
        self._update_enabled()
        self._update_undo_actions()
        self._update_title()
        self._update_page_label()
        self._update_tab_text()

    # -- tab management -------------------------------------------------

    def _tab_label(self, doc: Optional[PdfDocument]) -> str:
        if doc is None:
            return "Untitled"
        name = os.path.basename(doc.path) if doc.path else "Untitled"
        return ("• " if doc.dirty else "") + name

    def _update_tab_text(self) -> None:
        index = self._tabs.currentIndex()
        if index >= 0:
            self._tabs.setTabText(index, self._tab_label(self._doc))
            if self._doc and self._doc.path:
                self._tabs.setTabToolTip(index, self._doc.path)

    def _on_tab_changed(self, index: int) -> None:
        """Refresh the toolbar/menus/status for the newly selected tab."""
        self._update_enabled()
        self._update_undo_actions()
        self._update_title()
        self._update_page_label()
        if self._view is not None:
            self._set_tool(self._view.tool)
            self._tool_box.blockSignals(True)
            self._tool_box.setCurrentIndex(self._tool_index(self._view.tool))
            self._tool_box.blockSignals(False)

    def _close_tab(self, index: int) -> None:
        tab = self._tabs.widget(index)
        if not isinstance(tab, DocumentTab):
            return
        self._tabs.setCurrentIndex(index)
        if not self._confirm_discard():
            return
        if tab.doc is not None:
            tab.doc.close()
        self._tabs.removeTab(index)
        tab.deleteLater()
        if self._tabs.count() == 0:
            self._update_enabled()
            self._update_title()
            self._update_page_label()

    def _detach_tab(self, index: int, global_pos: QPoint) -> None:
        """Tear a tab off into its own window."""
        tab = self._tabs.widget(index)
        if not isinstance(tab, DocumentTab):
            return
        if self._tabs.count() <= 1:
            return  # nothing gained by detaching the only tab
        self._detach_widget(tab, self._tabs.tabText(index),
                            self._tabs.tabToolTip(index), global_pos)

    def _detach_widget(self, tab: "DocumentTab", title: str, tip: str,
                       global_pos: QPoint) -> None:
        """Move a specific tab into a brand-new window."""
        i = self._tabs.indexOf(tab)
        if i >= 0:
            self._tabs.removeTab(i)          # reparents the widget out
        win = MainWindow()
        win.resize(self.size())
        win.move(global_pos - QPoint(120, 20))
        win.show()
        win._adopt_tab(tab, title, tip)
        self._close_if_empty()

    def _accept_tab(self, tab: "DocumentTab", title: str, tip: str,
                    index: int) -> None:
        """Take a tab dragged in from another window and insert it here."""
        source = tab.win
        if source is not None and source is not self:
            si = source._tabs.indexOf(tab)
            if si >= 0:
                source._tabs.removeTab(si)
        else:
            si = self._tabs.indexOf(tab)   # same-window move
            if si >= 0:
                self._tabs.removeTab(si)
                if si < index:
                    index -= 1
        tab.win = self
        index = max(0, min(index, self._tabs.count()))
        self._tabs.insertTab(index, tab, title)
        if tip:
            self._tabs.setTabToolTip(index, tip)
        self._tabs.setCurrentIndex(index)
        self._on_tab_changed(index)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if source is not None and source is not self:
            source._close_if_empty()

    def _close_if_empty(self) -> None:
        """Close this window if it has no tabs left (e.g. after moving its
        last tab elsewhere) — but never the very last window open."""
        if self._tabs.count() == 0 and len(MainWindow._windows) > 1:
            self.close()

    def _tab_menu(self, index: int, global_pos: QPoint) -> None:
        tab = self._tabs.widget(index)
        title = self._tabs.tabText(index)
        tip = self._tabs.tabToolTip(index)
        menu = QMenu(self)
        act_detach = menu.addAction("Move to New Window")
        act_detach.setEnabled(self._tabs.count() > 1)

        # Submenu: move this tab into any other open window.
        others = [w for w in MainWindow._windows if w is not self and w.isVisible()]
        move_acts = {}
        if others:
            sub = menu.addMenu("Move to Window")
            for w in others:
                label = w._window_menu_label()
                act = sub.addAction(label)
                move_acts[act] = w
        menu.addSeparator()
        act_close = menu.addAction("Close Tab")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == act_detach:
            self._detach_tab(index, global_pos)
        elif chosen == act_close:
            self._close_tab(index)
        elif chosen in move_acts and isinstance(tab, DocumentTab):
            move_acts[chosen]._accept_tab(tab, title, tip, move_acts[chosen]._tabs.count())

    def _window_menu_label(self) -> str:
        """A short label naming this window by its current tab."""
        idx = self._tabs.currentIndex()
        if idx >= 0:
            return self._tabs.tabText(idx).lstrip("• ") or "Window"
        return "Window"

    def _adopt_tab(self, tab: DocumentTab, title: str, tip: str = "") -> None:
        """Take ownership of a tab detached from another window."""
        tab.win = self
        index = self._tabs.addTab(tab, title)
        if tip:
            self._tabs.setTabToolTip(index, tip)
        self._tabs.setCurrentIndex(index)
        self._on_tab_changed(index)

    # -- editing actions ------------------------------------------------

    def _bake_objects(self) -> None:
        """Stamp placed objects into the document before a structural edit that
        would otherwise invalidate their page/coordinate references."""
        overlays = self._view.overlay_objects()
        if not overlays:
            return
        self._doc.stamp_images(overlays)
        self._view.clear_objects()
        self._view.refresh()
        self._rebuild_thumbnails()

    def _rotate(self, degrees: int) -> None:
        if not self._doc:
            return
        self._bake_objects()
        self._checkpoint()
        self._doc.rotate_page(self._view.page_index, degrees)
        self._view.refresh()
        self._refresh_current_thumbnail()

    def _delete_page(self) -> None:
        if not self._doc:
            return
        current = self._view.page_index
        self._bake_objects()
        self._checkpoint()
        try:
            self._doc.delete_page(current)
        except DocumentError as exc:
            self._undo.pop()  # nothing changed
            self._update_undo_actions()
            QMessageBox.warning(self, "Delete page", str(exc))
            return
        self._view.refresh()
        self._rebuild_thumbnails()
        self._go_page(min(current, self._doc.page_count - 1))

    def _insert_blank_page(self) -> None:
        if not self._doc:
            return
        self._bake_objects()
        self._checkpoint()
        at = self._doc.insert_blank_page(self._view.page_index + 1)
        self._view.refresh()
        self._rebuild_thumbnails()
        self._go_page(at)

    def _append_pdf(self) -> None:
        if not self._doc:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Append PDF", "", _PDF_FILTER)
        if not path:
            return
        self._bake_objects()
        self._checkpoint()
        try:
            self._doc.append_pdf(path)
        except Exception as exc:
            self._undo.pop()
            self._update_undo_actions()
            QMessageBox.critical(self, "Append failed", str(exc))
            return
        self._view.refresh()
        self._rebuild_thumbnails()
        self._update_page_label()

    def _extract_page(self) -> None:
        if not self._doc:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Extract Page As", "", _PDF_FILTER)
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self._doc.extract_pages([self._view.page_index], path)
        self.statusBar().showMessage(f"Extracted page to {os.path.basename(path)}.", 3000)

    def _search(self) -> None:
        if not self._doc:
            return
        needle, ok = QInputDialog.getText(self, "Find", "Search text:")
        if not ok or not needle:
            return
        hits = self._doc.search(needle)
        if not hits:
            self.statusBar().showMessage("No matches.", 3000)
            return
        first = hits[0]
        self._go_page(first.page)
        self.statusBar().showMessage(
            f"{len(hits)} match(es); first on page {first.page + 1}.", 5000
        )

    def _pick_ink_color(self) -> None:
        color = QColorDialog.getColor(QColor(255, 0, 0), self, "Drawing color")
        if color.isValid():
            self._view.ink_color = (color.redF(), color.greenF(), color.blueF())

    def _on_place_requested(self, page: int, x: float, y: float) -> None:
        if not self._doc:
            return
        if self._view.tool == Tool.TEXT:
            text, ok = QInputDialog.getMultiLineText(self, "Insert Text", "Text:")
            if ok and text:
                self._view.add_freetext_annotation(page, x, y, text)
        elif self._view.tool == Tool.NOTE:
            text, ok = QInputDialog.getMultiLineText(self, "Sticky Note", "Note:")
            if ok and text:
                self._view.add_note_annotation(page, x, y, text)
        # Back to Select so the new item can be dragged/edited right away.
        self._tool_box.setCurrentIndex(self._tool_index(Tool.SELECT))

    def _on_edit_text_requested(self, page: int, x: float, y: float) -> None:
        """Edit the line of real (selectable) text the user clicked on."""
        if not self._doc:
            return
        span = self._doc.get_text_line_at(page, x, y)
        if span is None:
            self.statusBar().showMessage(
                "No editable text here — this looks like a scanned image.", 4000
            )
            return
        new_text, ok = QInputDialog.getMultiLineText(
            self, "Edit Text", "Text:", span["text"]
        )
        if not ok or new_text == span["text"]:
            return
        self._checkpoint()
        self._doc.replace_text_line(span, new_text)
        self._view.refresh_page(page)
        self._on_edited()

    def _edit_text_at(self, idx: int, x: float, y: float) -> None:
        """Right-click 'Edit Text Here' entry."""
        self._on_edit_text_requested(idx, x, y)

    def _on_rect_selected(self, page: int, x0: float, y0: float, x1: float, y1: float) -> None:
        """Handle tools that drag a rectangle and need extra input."""
        if not self._doc:
            return
        rect = (x0, y0, x1, y1)
        if self._view.tool == Tool.IMAGE:
            path, _ = QFileDialog.getOpenFileName(
                self, "Insert Image", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
            )
            if path and (x1 - x0 < 5 or y1 - y0 < 5):
                rect = (x0, y0, x0 + 200, y0 + 200)
            if path:
                self._checkpoint()
                self._doc.add_image(page, rect, path)
        elif self._view.tool == Tool.CROP:
            if x1 - x0 > 5 and y1 - y0 > 5:
                self._bake_objects()
                self._checkpoint()
                self._doc.crop_page(page, rect)
        elif self._view.tool == Tool.LINK:
            uri, ok = QInputDialog.getText(self, "Add Link", "Web address (URL):", text="https://")
            if ok and uri:
                self._checkpoint()
                self._doc.add_link_uri(page, rect, uri)
        # Return to the text-selection tool after a one-shot placement.
        self._tool_box.setCurrentIndex(self._tool_index(Tool.SELECT))
        self._view.refresh()
        self._on_edited()

    def _begin_tool(self, tool: Tool, hint: str) -> None:
        """Activate a drag-based tool and tell the user what to do."""
        if not self._doc:
            return
        self._set_tool(tool)
        if hint:
            self.statusBar().showMessage(hint, 6000)

    # -- convert / combine / split -------------------------------------

    def _combine_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose files to combine", "",
            "PDF and images (*.pdf *.png *.jpg *.jpeg *.bmp *.gif *.tiff);;All files (*)",
        )
        if len(paths) < 2:
            if paths:
                QMessageBox.information(self, "Combine", "Pick at least two files.")
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save combined PDF as", "", _PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        try:
            ops.merge_files(paths, out)
        except Exception as exc:
            QMessageBox.critical(self, "Combine failed", str(exc))
            return
        self._offer_open(out, f"Combined {len(paths)} files.")

    def _images_to_pdf(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose images", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff)"
        )
        if not paths:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save PDF as", "", _PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        try:
            ops.images_to_pdf(paths, out)
        except Exception as exc:
            QMessageBox.critical(self, "Failed", str(exc))
            return
        self._offer_open(out, f"Created PDF from {len(paths)} image(s).")

    def _optimize(self) -> None:
        if not self._doc or not self._doc.path:
            QMessageBox.information(self, "Reduce File Size", "Save the document first.")
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save optimized PDF as", "", _PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        old, new = ops.optimize(self._doc.path, out)
        pct = (1 - new / old) * 100 if old else 0
        QMessageBox.information(
            self, "Reduce File Size",
            f"Original: {old / 1024:.0f} KB\nOptimized: {new / 1024:.0f} KB\nSaved: {pct:.0f}%",
        )

    def _split(self) -> None:
        if not self._doc or not self._doc.path:
            QMessageBox.information(self, "Split", "Save the document first.")
            return
        method, ok = QInputDialog.getItem(
            self, "Split Document", "Split by:",
            ["Number of pages", "Top-level bookmarks", "Maximum file size (MB)"],
            0, False,
        )
        if not ok:
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if not out_dir:
            return
        try:
            if method.startswith("Number"):
                n, ok = QInputDialog.getInt(self, "Split", "Pages per file:", 1, 1, 10000)
                if not ok:
                    return
                outs = ops.split_by_page_count(self._doc.path, n, out_dir)
            elif method.startswith("Top-level"):
                outs = ops.split_by_bookmarks(self._doc.path, out_dir)
                if not outs:
                    QMessageBox.information(self, "Split", "This PDF has no bookmarks.")
                    return
            else:
                mb, ok = QInputDialog.getDouble(self, "Split", "Max size (MB):", 5.0, 0.1, 1000.0, 1)
                if not ok:
                    return
                outs = ops.split_by_size(self._doc.path, int(mb * 1024 * 1024), out_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Split failed", str(exc))
            return
        QMessageBox.information(self, "Split", f"Created {len(outs)} file(s) in\n{out_dir}")

    def _compare(self) -> None:
        a, _ = QFileDialog.getOpenFileName(self, "First PDF (original)", "", _PDF_FILTER)
        if not a:
            return
        b, _ = QFileDialog.getOpenFileName(self, "Second PDF (revised)", "", _PDF_FILTER)
        if not b:
            return
        report = ops.compare_text(a, b)
        self._show_text_report("Compare PDFs — text changes", report)

    # -- document tools -------------------------------------------------

    def _add_watermark(self) -> None:
        if not self._doc:
            return
        text, ok = QInputDialog.getText(self, "Watermark", "Watermark text:", text="CONFIDENTIAL")
        if not ok or not text:
            return
        self._checkpoint()
        self._doc.add_text_watermark(text)
        self._view.refresh()
        self._rebuild_thumbnails()
        self._on_edited()

    def _add_header_footer(self) -> None:
        if not self._doc:
            return
        text, ok = QInputDialog.getText(
            self, "Header/Footer",
            "Text (use {page} and {pages} for numbers):", text="Page {page} of {pages}",
        )
        if not ok or not text:
            return
        pos, ok = QInputDialog.getItem(
            self, "Header/Footer", "Position:",
            ["top-left", "top-center", "top-right",
             "bottom-left", "bottom-center", "bottom-right"],
            4, False,
        )
        if not ok:
            return
        self._checkpoint()
        self._doc.add_header_footer(text, position=pos)
        self._view.refresh()
        self._on_edited()

    def _add_bates(self) -> None:
        if not self._doc:
            return
        prefix, ok = QInputDialog.getText(self, "Bates Numbering", "Prefix (optional):")
        if not ok:
            return
        start, ok = QInputDialog.getInt(self, "Bates Numbering", "Start number:", 1, 0, 10_000_000)
        if not ok:
            return
        self._checkpoint()
        self._doc.add_bates_numbering(prefix=prefix, start=start)
        self._view.refresh()
        self._on_edited()

    def _add_bookmark(self) -> None:
        if not self._doc:
            return
        title, ok = QInputDialog.getText(self, "Add Bookmark", "Bookmark title:")
        if ok and title:
            self._checkpoint()
            self._doc.add_bookmark(title, self._view.page_index)
            self.statusBar().showMessage(f"Bookmark '{title}' added.", 3000)
            self._on_edited()

    def _attach_file(self) -> None:
        if not self._doc:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Attach a file", "", "All files (*)")
        if path:
            self._checkpoint()
            self._doc.attach_file(path)
            self.statusBar().showMessage(f"Attached {os.path.basename(path)}.", 3000)
            self._on_edited()

    def _extract_images(self) -> None:
        if not self._doc:
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Save images to folder")
        if not out_dir:
            return
        saved = self._doc.extract_images(out_dir)
        QMessageBox.information(self, "Extract Images", f"Saved {len(saved)} image(s).")

    # -- forms ----------------------------------------------------------

    def _add_form_field(self) -> None:
        if not self._doc:
            return
        name, ok = QInputDialog.getText(self, "Add Form Field", "Field name:")
        if not ok or not name:
            return
        # Place a default-sized field near the top of the current page.
        w, _ = self._doc.page_size(self._view.page_index)
        self._checkpoint()
        self._doc.add_text_field(
            self._view.page_index, (72, 100, min(w - 72, 372), 122), name
        )
        self._view.refresh()
        self.statusBar().showMessage(f"Added field '{name}'. Use Forms ▸ Fill to set a value.", 5000)
        self._on_edited()

    def _fill_form_field(self) -> None:
        if not self._doc:
            return
        fields = self._doc.get_form_fields()
        if not fields:
            QMessageBox.information(self, "Fill Form", "This document has no form fields.")
            return
        names = [f["name"] for f in fields if f["name"]]
        name, ok = QInputDialog.getItem(self, "Fill Form", "Field:", names, 0, False)
        if not ok:
            return
        value, ok = QInputDialog.getText(self, "Fill Form", f"Value for '{name}':")
        if ok:
            self._checkpoint()
            self._doc.fill_form_field(name, value)
            self._view.refresh()
            self._on_edited()

    def _flatten(self) -> None:
        if not self._doc:
            return
        if QMessageBox.question(
            self, "Flatten",
            "Flatten annotations and form fields into the page content?\n"
            "This makes edits permanent and non-editable.",
        ) == QMessageBox.Yes:
            self._bake_objects()
            self._checkpoint()
            self._doc.flatten()
            self._view.refresh()
            self._rebuild_thumbnails()
            self._on_edited()

    # -- security -------------------------------------------------------

    def _encrypt(self) -> None:
        if not self._doc:
            return
        from PySide6.QtWidgets import QLineEdit

        pw, ok = QInputDialog.getText(
            self, "Password Protect", "Set a password to open the file:",
            QLineEdit.Password,
        )
        if not ok or not pw:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save protected PDF as", "", _PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        try:
            self._doc.save_encrypted(out, user_pw=pw)
        except Exception as exc:
            QMessageBox.critical(self, "Encrypt failed", str(exc))
            return
        QMessageBox.information(
            self, "Password Protect",
            f"Saved 256-bit AES encrypted PDF:\n{os.path.basename(out)}",
        )

    def _search_and_redact(self) -> None:
        if not self._doc:
            return
        text, ok = QInputDialog.getText(self, "Search & Redact", "Text to permanently remove:")
        if not ok or not text:
            return
        if QMessageBox.question(
            self, "Search & Redact",
            f"Permanently remove every occurrence of '{text}'?\nThis cannot be undone after saving.",
        ) != QMessageBox.Yes:
            return
        self._checkpoint()
        n = self._doc.search_and_redact(text)
        self._view.refresh()
        self._rebuild_thumbnails()
        self._on_edited()
        QMessageBox.information(self, "Search & Redact", f"Redacted {n} occurrence(s).")

    def _sanitize(self) -> None:
        if not self._doc:
            return
        if QMessageBox.question(
            self, "Sanitize",
            "Remove hidden metadata, JavaScript, and embedded XML data?",
        ) == QMessageBox.Yes:
            self._checkpoint()
            self._doc.sanitize()
            self._on_edited()
            self.statusBar().showMessage("Document sanitized.", 3000)

    # -- convert / scan / production ------------------------------------

    def _office_to_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an Office file", "",
            "Office files (*.doc *.docx *.odt *.rtf *.txt *.xls *.xlsx *.ods *.csv *.ppt *.pptx *.odp);;All files (*)",
        )
        if not path:
            return
        try:
            out = convert.office_to_pdf(path)
        except external.DependencyError as exc:
            self._dep_message(exc)
            return
        except Exception as exc:
            QMessageBox.critical(self, "Conversion failed", str(exc))
            return
        self._offer_open(out, "Converted to PDF.")

    def _require_saved(self, feature: str) -> Optional[str]:
        """Return the document's path, prompting to save first if needed."""
        if not self._doc:
            return None
        if not self._doc.path or self._doc.dirty:
            QMessageBox.information(self, feature, "Please save the document first.")
            return None
        return self._doc.path

    def _ocr(self) -> None:
        src = self._require_saved("OCR")
        if not src:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save searchable PDF as", "", _PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        self.statusBar().showMessage("Running OCR… this can take a while.", 0)
        try:
            ocr.ocr_pdf(src, out, deskew=True, clean=True, rotate=True)
        except external.DependencyError as exc:
            self.statusBar().clearMessage()
            self._dep_message(exc)
            return
        except Exception as exc:
            self.statusBar().clearMessage()
            QMessageBox.critical(self, "OCR failed", str(exc))
            return
        self.statusBar().clearMessage()
        self._offer_open(out, "OCR complete — text is now searchable.")

    def _to_pdfa(self) -> None:
        src = self._require_saved("PDF/A")
        if not src:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save PDF/A as", "", _PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        try:
            ocr.to_pdfa(src, out)
        except external.DependencyError as exc:
            self._dep_message(exc)
            return
        except Exception as exc:
            QMessageBox.critical(self, "PDF/A conversion failed", str(exc))
            return
        self._offer_open(out, "Converted to archival PDF/A.")

    def _preflight(self) -> None:
        src = self._require_saved("Preflight")
        if not src:
            return
        report = production.preflight(src)
        self._show_text_report("Preflight — print readiness", report.as_text())

    # -- signature / initials image ------------------------------------

    def _insert_signature(self) -> None:
        """Choose or upload a signature image, then place it on the page."""
        if not self._doc:
            return
        saved = imaging.list_signatures()

        if not saved:
            # First time: go straight to the file picker — no dropdown.
            path = self._prepare_new_signature()
        else:
            # Offer a clear choice between uploading and reusing a saved one.
            box = QMessageBox(self)
            box.setWindowTitle("Insert Signature / Initials")
            box.setText("Upload a new signature image, or use a saved one?")
            upload_btn = box.addButton("Upload New Image…", QMessageBox.AcceptRole)
            saved_btn = box.addButton("Use Saved…", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked == upload_btn:
                path = self._prepare_new_signature()
            elif clicked == saved_btn:
                names = [os.path.basename(p) for p in saved]
                name, ok = QInputDialog.getItem(
                    self, "Saved Signatures", "Choose a signature:", names, 0, False
                )
                if not ok:
                    return
                path = saved[names.index(name)]
            else:
                return

        if not path:
            return

        # Enter interactive placement: the signature follows the cursor.
        from PySide6.QtGui import QPixmap

        pix = QPixmap(path)
        if pix.isNull():
            QMessageBox.warning(self, "Signature", "Could not load that image.")
            return
        self._view.begin_placement(pix, path)
        self.statusBar().showMessage(
            "Move the signature and click to drop it. Afterwards you can drag it "
            "to move, drag a corner to resize, or press Delete to remove it.",
            0,
        )

    def _on_objects_changed(self) -> None:
        """A placed image/signature was added, moved, resized, or deleted."""
        if self._doc:
            self._doc.dirty = True
        self._update_title()

    # -- Comments panel (live annotations) ------------------------------

    # Which annotation kinds show up in the Comments list.
    _COMMENT_KINDS = {
        "Text": "Note", "FreeText": "Text", "Highlight": "Highlight",
        "Underline": "Underline", "StrikeOut": "Strikeout", "Squiggly": "Squiggly",
        "Ink": "Drawing", "Square": "Rectangle", "Circle": "Oval",
        "Line": "Line", "Polygon": "Polygon", "PolyLine": "Polyline",
        "Stamp": "Stamp",
    }

    # -- comment author -------------------------------------------------

    @staticmethod
    def _load_author() -> str:
        """The saved author name, defaulting to the OS login name."""
        saved = QSettings().value("annotations/author", "", type=str)
        if saved:
            return saved
        try:
            import getpass
            return getpass.getuser()
        except Exception:  # pragma: no cover
            return ""

    def _set_comment_author(self) -> None:
        """Prompt for the author name applied to comments/notes/highlights."""
        name, ok = QInputDialog.getText(
            self, "Comment Author", "Author name:", text=self._author
        )
        if not ok:
            return
        name = name.strip()
        self._author = name
        QSettings().setValue("annotations/author", name)
        if self._doc:
            self._doc.set_default_author(name)
        # Offer to relabel comments already in this document.
        if self._doc and self._doc.list_annotations():
            resp = QMessageBox.question(
                self, "Update existing comments",
                "Also set this author on the comments already in this document?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if resp == QMessageBox.Yes:
                self._checkpoint()
                self._doc.relabel_annotations(name)
                self._view.refresh()
                self._on_annotations_changed()
        self.statusBar().showMessage(
            f"Comment author set to “{name or '(none)'}”.", 3000
        )

    def _rename_existing_author(self) -> None:
        """Rename an author already present on comments in this document."""
        if not self._doc:
            return
        authors = self._doc.annotation_authors()
        if not authors:
            QMessageBox.information(
                self, "Rename Author",
                "There are no comments with an author name in this document yet.",
            )
            return
        old, ok = QInputDialog.getItem(
            self, "Rename Author", "Author to rename:", authors, 0, False
        )
        if not ok or not old:
            return
        new, ok = QInputDialog.getText(
            self, "Rename Author", f"New name for “{old}”:", text=old
        )
        if not ok:
            return
        new = new.strip()
        self._checkpoint()
        changed = self._doc.rename_author(old, new)
        self._view.refresh()
        self._on_annotations_changed()
        self.statusBar().showMessage(
            f"Renamed author on {changed} comment(s).", 3000
        )

    def _on_annotations_changed(self) -> None:
        """An annotation was added, moved, edited or deleted — refresh panel."""
        if self._doc:
            self._doc.dirty = True
        self._update_title()
        self._rebuild_comments()

    def _rebuild_comments(self) -> None:
        if self._comments is None:
            return
        self._comments.clear()
        annots = self._doc.list_annotations() if self._doc else []
        shown = [a for a in annots if a["kind"] in self._COMMENT_KINDS]
        self._comments_empty.setVisible(not shown)
        self._comments.setVisible(bool(shown))
        sel = self._view.selected_annotation()
        for a in shown:
            label = self._COMMENT_KINDS.get(a["kind"], a["kind"])
            body = (a["content"] or "").strip() or "(no text)"
            head = f"p.{a['page'] + 1}  ·  {label}"
            if a["author"]:
                head += f"  ·  {a['author']}"
            item = QListWidgetItem(f"{head}\n{body}")
            item.setData(Qt.UserRole, (a["page"], a["xref"]))
            self._comments.addItem(item)
            if sel is not None and sel == (a["page"], a["xref"]):
                self._comments.setCurrentItem(item)

    def _on_comment_clicked(self, item: QListWidgetItem) -> None:
        page, xref = item.data(Qt.UserRole)
        self._view.select_annotation(page, xref)
        self._scroll_to_annotation(page, xref)

    def _on_comment_double_clicked(self, item: QListWidgetItem) -> None:
        page, xref = item.data(Qt.UserRole)
        self._on_annot_edit_requested(page, xref)

    def _comment_context_menu(self, pos) -> None:
        item = self._comments.itemAt(pos)
        if item is None:
            return
        page, xref = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self._on_annot_edit_requested(page, xref))
        menu.addAction("Go to", lambda: self._scroll_to_annotation(page, xref))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self._delete_annotation(page, xref))
        menu.exec(self._comments.mapToGlobal(pos))

    def _delete_annotation(self, page: int, xref: int) -> None:
        if not self._doc:
            return
        self._checkpoint()
        self._doc.delete_annot(page, xref)
        self._view.clear_annot_selection()
        self._view.refresh_page(page)
        self._on_annotations_changed()

    def _scroll_to_annotation(self, page: int, xref: int) -> None:
        rect = self._doc.annot_rect(page, xref) if self._doc else None
        self._scroll.verticalScrollBar().setValue(
            max(0, self._view.page_top(page)
                + (int(rect[1] * self._view.zoom) - 60 if rect else 0))
        )
        self._view.setFocus()

    def _prepare_new_signature(self) -> Optional[str]:
        """Upload an image, optionally remove its background, and save it."""
        src, _ = QFileDialog.getOpenFileName(
            self, "Choose a photo/scan of your signature", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff *.webp)"
        )
        if not src:
            return None

        remove = QMessageBox.question(
            self, "Remove background",
            "Remove the background so only the ink shows?\n"
            "(Recommended for a photo or scan on white paper.)",
        ) == QMessageBox.Yes

        name, ok = QInputDialog.getText(
            self, "Save signature", "Name this signature (for reuse):",
            text="My signature"
        )
        if not ok or not name:
            name = "signature"
        safe = "".join(c for c in name if c.isalnum() or c in " -_").strip() or "signature"
        out = os.path.join(imaging.signatures_dir(), f"{safe}.png")

        try:
            if remove:
                imaging.remove_background(src, out)
            else:
                # Still normalize to PNG so transparency is possible later.
                from PySide6.QtGui import QImage
                QImage(src).save(out, "PNG")
        except external.DependencyError as exc:
            self._dep_message(exc)
            return None
        except Exception as exc:
            QMessageBox.critical(self, "Signature", f"Could not process image:\n{exc}")
            return None
        return out

    def _tool_index(self, tool: Tool) -> int:
        for i in range(self._tool_box.count()):
            if self._tool_box.itemData(i) == tool:
                return i
        return 0

    # -- signing --------------------------------------------------------

    def _make_cert(self) -> None:
        from PySide6.QtWidgets import QLineEdit

        name, ok = QInputDialog.getText(self, "Self-Signed Certificate", "Your name / identity:")
        if not ok or not name:
            return
        pw, ok = QInputDialog.getText(
            self, "Self-Signed Certificate", "Set a password for the certificate:", QLineEdit.Password
        )
        if not ok or not pw:
            return
        out, _ = QFileDialog.getSaveFileName(
            self, "Save certificate as", "", "Certificate (*.pfx *.p12)"
        )
        if not out:
            return
        if not (out.lower().endswith(".pfx") or out.lower().endswith(".p12")):
            out += ".pfx"
        try:
            signing.create_self_signed_cert(out, name, pw)
        except external.DependencyError as exc:
            self._dep_message(exc)
            return
        except Exception as exc:
            QMessageBox.critical(self, "Certificate failed", str(exc))
            return
        QMessageBox.information(
            self, "Certificate created",
            f"Saved {os.path.basename(out)}.\nUse Secure ▸ Digitally Sign with this file.",
        )

    def _sign(self) -> None:
        from PySide6.QtWidgets import QLineEdit

        src = self._require_saved("Digital Signature")
        if not src:
            return
        pfx, _ = QFileDialog.getOpenFileName(
            self, "Choose your certificate", "", "Certificate (*.pfx *.p12)"
        )
        if not pfx:
            return
        pw, ok = QInputDialog.getText(
            self, "Digital Signature", "Certificate password:", QLineEdit.Password
        )
        if not ok:
            return
        reason, _ = QInputDialog.getText(self, "Digital Signature", "Reason (optional):")
        out, _ = QFileDialog.getSaveFileName(self, "Save signed PDF as", "", _PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        try:
            signing.sign_pdf(src, out, pfx, pw, reason=reason)
        except external.DependencyError as exc:
            self._dep_message(exc)
            return
        except Exception as exc:
            QMessageBox.critical(self, "Signing failed", str(exc))
            return
        self._offer_open(out, "Document digitally signed.")

    # -- AI -------------------------------------------------------------

    def _ai_summarize(self) -> None:
        if not self._doc:
            return
        text = self._all_text()
        try:
            result = ai.summarize(text)
        except external.DependencyError as exc:
            self._dep_message(exc)
            return
        except ai.AIError as exc:
            QMessageBox.warning(self, "AI Assistant", str(exc))
            return
        self._show_text_report("AI Summary", result)

    def _ai_ask(self) -> None:
        if not self._doc:
            return
        question, ok = QInputDialog.getText(self, "Ask about this document", "Your question:")
        if not ok or not question:
            return
        text = self._all_text()
        try:
            result = ai.ask(text, question)
        except external.DependencyError as exc:
            self._dep_message(exc)
            return
        except ai.AIError as exc:
            QMessageBox.warning(self, "AI Assistant", str(exc))
            return
        self._show_text_report(f"AI — {question}", result)

    def _all_text(self) -> str:
        return "\n\n".join(
            self._doc.get_text(i) for i in range(self._doc.page_count)
        )

    def _dep_message(self, exc: external.DependencyError) -> None:
        QMessageBox.information(self, "Optional feature not installed", str(exc))

    # -- helpers --------------------------------------------------------

    def _offer_open(self, path: str, message: str) -> None:
        """Tell the user a file was created and offer to open it."""
        if QMessageBox.question(
            self, "Done", f"{message}\n\nOpen {os.path.basename(path)} now?",
        ) == QMessageBox.Yes:
            self.open_document(path)

    def _show_text_report(self, title: str, text: str) -> None:
        from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setProperty("resizable", True)  # keep this one user-resizable
        dlg.resize(700, 500)
        layout = QVBoxLayout(dlg)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        from PySide6.QtGui import QFont

        view.setFont(QFont("monospace"))
        layout.addWidget(view)
        dlg.exec()

    # -- navigation & view ---------------------------------------------

    def _go_page(self, index: int) -> None:
        """Scroll so the given page is at the top of the view."""
        if not self._doc:
            return
        index = max(0, min(index, self._doc.page_count - 1))
        self._scroll.verticalScrollBar().setValue(self._view.page_top(index))
        # The scroll triggers _on_visible_page_changed, which syncs the UI.
        self._on_visible_page_changed(index)

    def _on_visible_page_changed(self, index: int) -> None:
        """Update the page counter and thumbnail highlight as pages scroll by."""
        self._thumbs.blockSignals(True)
        self._thumbs.setCurrentRow(index)
        self._thumbs.blockSignals(False)
        self._update_page_label()

    def _goto_page(self) -> None:
        if not self._doc:
            return
        n, ok = QInputDialog.getInt(
            self, "Go to Page", "Page number:", self._view.page_index + 1, 1, self._doc.page_count
        )
        if ok:
            self._go_page(n - 1)

    def _zoom_by(self, factor: float) -> None:
        self._view.set_zoom(self._view.zoom * factor)

    def _fit_width(self) -> None:
        self._view.fit_width(self._scroll.viewport().width())

    _RAIL_W = 18  # collapsed panel width (just the rail)

    def _set_split_section(self, index: int, width: int) -> None:
        """Resize one splitter section, letting the page area absorb the change."""
        sizes = self._splitter.sizes()
        if not (0 <= index < len(sizes)):
            return
        delta = width - sizes[index]
        sizes[index] = width
        sizes[1] = max(120, sizes[1] - delta)  # centre page area
        self._splitter.setSizes(sizes)

    def _toggle_thumbnails(self) -> None:
        showing = self._thumbs.isVisible()
        if showing:
            self._left_size = self._splitter.sizes()[0]
            self._thumbs.setVisible(False)
            self._set_split_section(0, self._RAIL_W)
        else:
            self._thumbs.setVisible(True)
            self._set_split_section(0, getattr(self, "_left_size", 190))
        # Arrow points the way it will move the panel next.
        self._thumb_rail.setIcon(self._icon("next" if showing else "prev"))
        self._thumb_rail.setToolTip(
            "Show page thumbnails" if showing else "Hide page thumbnails"
        )

    def _toggle_comments(self) -> None:
        showing = self._comments_panel.isVisible()
        if showing:
            self._right_size = self._splitter.sizes()[2]
            self._comments_panel.setVisible(False)
            self._set_split_section(2, self._RAIL_W)
        else:
            self._comments_panel.setVisible(True)
            self._set_split_section(2, getattr(self, "_right_size", 260))
        # Arrow points the way the panel will move next.
        self._comments_rail.setIcon(self._icon("prev" if showing else "next"))
        self._comments_rail.setToolTip(
            "Show comments" if showing else "Hide comments"
        )

    def _set_tool(self, tool: Tool) -> None:
        if self._view is None:
            return
        self._view.tool = tool
        # Base cursor; in Select mode hover refines it to I-beam over text,
        # move/resize over a placed object, and plain arrow otherwise.
        if tool == Tool.HAND:
            self._view.setCursor(Qt.OpenHandCursor)
        elif tool == Tool.SELECT:
            self._view.setCursor(Qt.ArrowCursor)
        elif tool == Tool.EDIT_TEXT:
            self._view.setCursor(Qt.IBeamCursor)
        else:
            self._view.setCursor(Qt.CrossCursor)

    def _copy_text(self) -> None:
        if self._view.copy_selection():
            self.statusBar().showMessage("Copied selected text.", 2000)
        else:
            self.statusBar().showMessage("No text selected — drag with the Select Text tool.", 3000)

    # -- Ctrl+wheel zoom ------------------------------------------------

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        if obj is self._view and event.type() == QEvent.Wheel and self._doc:
            if event.modifiers() & Qt.ControlModifier:
                self._zoom_at_cursor(event)
                return True
        return super().eventFilter(obj, event)

    def _zoom_at_cursor(self, event) -> None:
        """Zoom keeping the document point under the cursor fixed."""
        pos = event.position()
        hbar = self._scroll.horizontalScrollBar()
        vbar = self._scroll.verticalScrollBar()
        old_w, old_h = max(1, self._view.width()), max(1, self._view.height())
        screen_x = pos.x() - hbar.value()
        screen_y = pos.y() - vbar.value()
        fx, fy = pos.x() / old_w, pos.y() / old_h
        factor = 1.15 if event.angleDelta().y() > 0 else (1 / 1.15)
        self._view.set_zoom(self._view.zoom * factor)
        hbar.setValue(int(fx * self._view.width() - screen_x))
        vbar.setValue(int(fy * self._view.height() - screen_y))
        self._on_visible_page_changed(self._view.current_page())

    # -- right-click context menu ---------------------------------------

    def _show_context_menu(self, pos) -> None:
        if not self._doc:
            return
        menu = QMenu(self)
        hit = self._view.point_to_page(pos)

        if self._view.has_selection():
            menu.addAction("Copy", self._copy_text)
            menu.addAction("Highlight Selection", self._highlight_selection)
            menu.addAction("Underline Selection", self._underline_selection)
            menu.addAction("Strikethrough Selection", self._strikeout_selection)
            menu.addAction("Redact Selection", self._redact_selection)
            menu.addSeparator()
            menu.addAction("Search for Selection", self._search_selection)
            menu.addAction("Explain Selection (AI)", self._ai_explain_selection)
            menu.addAction("Translate Selection (AI)…", self._ai_translate_selection)
            menu.addSeparator()

        if hit is not None:
            idx, x, y = hit
            if self._doc.get_text_line_at(idx, x, y) is not None:
                menu.addAction("Edit Text Here…", lambda: self._edit_text_at(idx, x, y))
            menu.addAction("Insert Text Here…", lambda: self._insert_text_at(idx, x, y))
            menu.addAction("Sticky Note Here…", lambda: self._note_at(idx, x, y))
            menu.addAction("Insert Signature / Initials…", self._insert_signature)
            menu.addSeparator()

        menu.addAction("Copy Area as Image", self._start_snapshot)
        menu.addAction("Add Bookmark Here…", self._add_bookmark)
        menu.addAction("Extract This Page…", self._extract_page)
        menu.addSeparator()
        menu.addActions([self.act_undo, self.act_redo])
        menu.addSeparator()
        menu.addActions([self.act_zoom_in, self.act_zoom_out, self.act_fit_width])
        menu.addSeparator()
        menu.addActions([self.act_rotate_cw, self.act_rotate_ccw, self.act_delete_page])
        menu.addSeparator()
        menu.addAction("Copy Whole Page Text", self._copy_page_text)
        menu.exec(self._view.mapToGlobal(pos))

    def _highlight_selection(self) -> None:
        page, rects = self._view.selection()
        if not rects:
            return
        self._checkpoint()
        for r in rects:
            self._doc.add_highlight(page, r)
        self._view.clear_selection()
        self._view.refresh()
        self._on_edited()

    def _redact_selection(self) -> None:
        page, rects = self._view.selection()
        if not rects:
            return
        if QMessageBox.question(
            self, "Redact Selection",
            "Permanently remove the selected text? This cannot be undone after saving.",
        ) != QMessageBox.Yes:
            return
        self._checkpoint()
        for r in rects:
            self._doc.redact(page, r)
        self._view.clear_selection()
        self._view.refresh()
        self._rebuild_thumbnails()
        self._on_edited()

    def _insert_text_at(self, idx: int, x: float, y: float) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Insert Text", "Text:")
        if ok and text:
            self._view.add_freetext_annotation(idx, x, y, text)

    def _note_at(self, idx: int, x: float, y: float) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Sticky Note", "Note:")
        if ok and text:
            self._view.add_note_annotation(idx, x, y, text)

    def _on_annot_edit_requested(self, page: int, xref: int) -> None:
        """Double-clicked an annotation — edit its text/comment."""
        if not self._doc:
            return
        current = self._doc.get_annot_text(page, xref)
        text, ok = QInputDialog.getMultiLineText(self, "Edit Comment", "Text:", current)
        if not ok:
            return
        self._checkpoint()
        self._doc.set_annot_text(page, xref, text)
        self._view.refresh_page(page)
        self._on_annotations_changed()

    def _copy_page_text(self) -> None:
        if not self._doc:
            return
        QGuiApplication.clipboard().setText(self._doc.get_text(self._view.page_index))
        self.statusBar().showMessage("Copied page text.", 2000)

    def _underline_selection(self) -> None:
        self._markup_selection(self._doc.add_underline if self._doc else None)

    def _strikeout_selection(self) -> None:
        self._markup_selection(self._doc.add_strikeout if self._doc else None)

    def _markup_selection(self, method) -> None:
        page, rects = self._view.selection()
        if not rects or method is None:
            return
        self._checkpoint()
        for r in rects:
            method(page, r)
        self._view.clear_selection()
        self._view.refresh()
        self._on_edited()

    def _search_selection(self) -> None:
        needle = self._view.selected_text().split("\n")[0].strip()
        if not needle:
            return
        hits = self._doc.search(needle)
        if not hits:
            self.statusBar().showMessage("No matches.", 3000)
            return
        self._go_page(hits[0].page)
        self.statusBar().showMessage(f"{len(hits)} match(es) for '{needle}'.", 5000)

    def _ai_explain_selection(self) -> None:
        text = self._view.selected_text()
        try:
            result = ai.explain(text)
        except external.DependencyError as exc:
            self._dep_message(exc)
            return
        except ai.AIError as exc:
            QMessageBox.warning(self, "AI Assistant", str(exc))
            return
        self._show_text_report("Explain selection", result)

    def _ai_translate_selection(self) -> None:
        text = self._view.selected_text()
        lang, ok = QInputDialog.getText(self, "Translate", "Target language:", text="English")
        if not ok or not lang:
            return
        try:
            result = ai.translate(text, lang)
        except external.DependencyError as exc:
            self._dep_message(exc)
            return
        except ai.AIError as exc:
            QMessageBox.warning(self, "AI Assistant", str(exc))
            return
        self._show_text_report(f"Translation ({lang})", result)

    def _start_snapshot(self) -> None:
        self._set_tool(Tool.SNAPSHOT)
        self.statusBar().showMessage("Drag a box to copy that area as an image.", 6000)

    def _on_area_copied(self) -> None:
        self._tool_box.setCurrentIndex(self._tool_index(Tool.SELECT))
        self.statusBar().showMessage("Area copied to clipboard.", 2500)

    # -- printing -------------------------------------------------------

    def _print(self) -> None:
        if not self._doc:
            return
        try:
            from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        except Exception:
            QMessageBox.information(self, "Print", "Printing support isn't available in this build.")
            return
        printer = QPrinter(QPrinter.HighResolution)
        if QPrintDialog(printer, self).exec() != QPrintDialog.Accepted:
            return
        painter = QPainter(printer)
        zoom = min(max(printer.resolution() / 72.0, 1.0), 3.0)
        for i in range(self._doc.page_count):
            if i > 0:
                printer.newPage()
            rp = self._doc.render_page(i, zoom)
            img = QImage(rp.samples, rp.width, rp.height, rp.stride, QImage.Format_RGBA8888).copy()
            vp = painter.viewport()
            scaled = img.scaled(vp.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawImage(
                vp.x() + (vp.width() - scaled.width()) // 2,
                vp.y() + (vp.height() - scaled.height()) // 2,
                scaled,
            )
        painter.end()
        self.statusBar().showMessage("Sent to printer.", 3000)

    # -- thumbnail right-click ------------------------------------------

    def _thumb_context_menu(self, pos) -> None:
        if not self._doc:
            return
        item = self._thumbs.itemAt(pos)
        if item is None:
            return
        row = self._thumbs.row(item)
        menu = QMenu(self)
        menu.addAction("Rotate Right", lambda: self._rotate_page_at(row, 90))
        menu.addAction("Rotate Left", lambda: self._rotate_page_at(row, -90))
        menu.addSeparator()
        menu.addAction("Delete Page", lambda: self._delete_page_at(row))
        menu.addAction("Extract Page…", lambda: self._extract_page_at(row))
        menu.exec(self._thumbs.mapToGlobal(pos))

    def _rotate_page_at(self, row: int, degrees: int) -> None:
        self._bake_objects()
        self._checkpoint()
        self._doc.rotate_page(row, degrees)
        self._view.refresh()
        item = self._thumbs.item(row)
        if item:
            item.setIcon(self._thumbnail_icon(row))
        self._on_edited()

    def _delete_page_at(self, row: int) -> None:
        self._bake_objects()
        self._checkpoint()
        try:
            self._doc.delete_page(row)
        except DocumentError as exc:
            self._undo.pop()
            self._update_undo_actions()
            QMessageBox.warning(self, "Delete page", str(exc))
            return
        self._view.refresh()
        self._rebuild_thumbnails()
        self._go_page(min(row, self._doc.page_count - 1))
        self._on_edited()

    def _extract_page_at(self, row: int) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Extract Page As", "", _PDF_FILTER)
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self._doc.extract_pages([row], path)
        self.statusBar().showMessage(f"Extracted page {row + 1}.", 3000)

    # -- undo / redo ----------------------------------------------------

    def _checkpoint(self) -> None:
        """Snapshot the document *before* a change, for undo."""
        if not self._doc:
            return
        try:
            self._undo.append(self._doc.to_bytes())
        except Exception:
            return
        if len(self._undo) > self._max_history:
            self._undo.pop(0)
        self._redo.clear()
        self._update_undo_actions()

    def _undo_action(self) -> None:
        if not self._doc or not self._undo:
            return
        try:
            self._redo.append(self._doc.to_bytes())
        except Exception:
            pass
        self._restore(self._undo.pop())
        self.statusBar().showMessage("Undo.", 1500)

    def _redo_action(self) -> None:
        if not self._doc or not self._redo:
            return
        try:
            self._undo.append(self._doc.to_bytes())
        except Exception:
            pass
        self._restore(self._redo.pop())
        self.statusBar().showMessage("Redo.", 1500)

    def _restore(self, data: bytes) -> None:
        current = self._view.page_index
        self._doc.restore_bytes(data)
        self._view.clear_annot_selection()
        self._view.refresh()
        self._rebuild_thumbnails()
        self._rebuild_comments()
        self._go_page(min(current, self._doc.page_count - 1))
        self._update_title()
        self._update_page_label()
        self._update_undo_actions()

    def _update_undo_actions(self) -> None:
        self.act_undo.setEnabled(bool(self._doc) and bool(self._undo))
        self.act_redo.setEnabled(bool(self._doc) and bool(self._redo))

    def _on_thumb_selected(self, row: int) -> None:
        if row >= 0:
            self._go_page(row)

    def _on_edited(self) -> None:
        self._update_title()
        self._refresh_current_thumbnail()
        self._rebuild_comments()  # highlights/markup are annotations too

    # -- thumbnails -----------------------------------------------------

    def _rebuild_thumbnails(self) -> None:
        if self._thumbs is None:
            return
        self._thumbs.blockSignals(True)
        self._thumbs.clear()
        if self._doc:
            for i in range(self._doc.page_count):
                item = QListWidgetItem(self._thumbnail_icon(i), f"{i + 1}")
                item.setTextAlignment(Qt.AlignHCenter)
                self._thumbs.addItem(item)
            self._thumbs.setCurrentRow(self._view.page_index)
        self._thumbs.blockSignals(False)

    def _refresh_current_thumbnail(self) -> None:
        if not self._doc:
            return
        i = self._view.page_index
        item = self._thumbs.item(i)
        if item:
            item.setIcon(self._thumbnail_icon(i))

    def _thumbnail_icon(self, index: int) -> QIcon:
        w, _ = self._doc.page_size(index)
        zoom = 140 / w if w else 0.2
        rp = self._doc.render_page(index, zoom)
        img = QImage(rp.samples, rp.width, rp.height, rp.stride, QImage.Format_RGBA8888)
        # Composite onto a white sheet so the thumbnail reads as paper.
        base = QPixmap(rp.width, rp.height)
        base.fill(Qt.white)
        painter = QPainter(base)
        painter.drawImage(0, 0, img)
        painter.end()
        return QIcon(base)

    # -- window state ---------------------------------------------------

    def _update_enabled(self) -> None:
        has = self._doc is not None
        for act in (
            self.act_save, self.act_save_as, self.act_print,
            self.act_zoom_in, self.act_zoom_out,
            self.act_fit_width, self.act_prev, self.act_next, self.act_goto,
            self.act_insert_page, self.act_delete_page, self.act_rotate_cw,
            self.act_rotate_ccw, self.act_append, self.act_extract, self.act_search,
            self.act_add_image, self.act_ink_color,
            self.act_optimize, self.act_split, self.act_watermark,
            self.act_header_footer, self.act_bates, self.act_crop, self.act_bookmark,
            self.act_link, self.act_attach, self.act_extract_images,
            self.act_add_field, self.act_fill_field, self.act_flatten,
            self.act_encrypt, self.act_search_redact, self.act_sanitize,
            self.act_ocr, self.act_pdfa, self.act_preflight, self.act_sign,
            self.act_ai_summary, self.act_ai_ask, self.act_signature,
        ):
            act.setEnabled(has)
        self._tool_box.setEnabled(has)

    def _update_title(self) -> None:
        if self._doc is None:
            self.setWindowTitle("PDF Viewer & Editor")
            return
        name = os.path.basename(self._doc.path) if self._doc.path else "Untitled"
        dirty = "*" if self._doc.dirty else ""
        self.setWindowTitle(f"{dirty}{name} — PDF Viewer & Editor")
        self._update_tab_text()

    def _update_page_label(self) -> None:
        spin = self._page_spin
        spin.blockSignals(True)   # don't treat a programmatic sync as a jump
        if self._doc:
            count = self._doc.page_count
            spin.setEnabled(True)
            spin.setMinimum(1)
            spin.setMaximum(count)
            spin.setValue(self._view.page_index + 1)
            self._page_total.setText(f"/ {count} ")
        else:
            spin.setEnabled(False)
            spin.setMinimum(0)
            spin.setMaximum(0)
            spin.setValue(0)
            self._page_total.setText("/ 0 ")
        spin.blockSignals(False)

    def _on_page_spin_changed(self, value: int) -> None:
        """User typed a page number (or used the arrows) — jump to it."""
        if self._doc and value >= 1:
            self._go_page(value - 1)

    # -- close handling -------------------------------------------------

    def _confirm_discard(self) -> bool:
        """Return True if it's safe to discard the current document."""
        if not self._doc or not self._doc.dirty:
            return True
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes before continuing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if choice == QMessageBox.Save:
            return self.save_document()
        return choice == QMessageBox.Discard

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # Confirm and close every open tab before the window goes away.
        while self._tabs.count():
            self._tabs.setCurrentIndex(0)
            if not self._confirm_discard():
                event.ignore()
                return
            tab = self._tabs.widget(0)
            if isinstance(tab, DocumentTab) and tab.doc is not None:
                tab.doc.close()
            self._tabs.removeTab(0)
        if self in MainWindow._windows:
            MainWindow._windows.remove(self)
        event.accept()
