"""One set of colour tokens for the whole interface, in light and dark.

Status colours mean only status and are shared with the MATLAB build. Chart
series use a separate palette in a fixed order, checked for colour-blind
separation, so a series colour can never be mistaken for an alarm state.
"""
from __future__ import annotations

STATUS = {"green": ("#18794e", "#ffffff"), "yellow": ("#f2b632", "#16181c"), "red": ("#c8322b", "#ffffff"),
          "grey": ("#5b6470", "#ffffff"), "blue": ("#2a78d6", "#ffffff")}

THEMES = {
    "dark": {"window": "#15171a", "surface": "#1e2125", "raised": "#272b30", "border": "#343a41", "text": "#e8eaed", "muted": "#a3abb5",
             "accent": "#3987e5", "accent_text": "#ffffff", "selection": "#25426a", "grid": "#343a41",
             "series": ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767")},
    "light": {"window": "#f4f5f7", "surface": "#ffffff", "raised": "#eef0f3", "border": "#d8dce2", "text": "#15171a", "muted": "#5b6470",
              "accent": "#2a78d6", "accent_text": "#ffffff", "selection": "#d4e5f9", "grid": "#e3e6ea",
              "series": ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")},
}


def stylesheet(name: str) -> str:
    t = THEMES[name]
    return f"""
    * {{ font-size: 13px; color: {t['text']}; }}
    QMainWindow, QDialog, QWidget#page {{ background: {t['window']}; }}
    QWidget#topbar {{ background: {t['surface']}; border-bottom: 1px solid {t['border']}; }}
    QListWidget#sidebar {{ background: {t['surface']}; border: none; border-right: 1px solid {t['border']}; padding: 8px 6px; outline: 0; }}
    QListWidget#sidebar::item {{ padding: 9px 12px; border-radius: 6px; color: {t['muted']}; margin: 1px 0; }}
    QListWidget#sidebar::item:hover {{ background: {t['raised']}; color: {t['text']}; }}
    QListWidget#sidebar::item:selected {{ background: {t['selection']}; color: {t['text']}; font-weight: 600; }}
    QLabel#pageTitle {{ font-size: 20px; font-weight: 600; }}
    QLabel#muted, QLabel#hint {{ color: {t['muted']}; }}
    QLabel#sectionTitle {{ font-weight: 600; }}
    QFrame#card, QGroupBox {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px; }}
    QGroupBox {{ margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
    QLabel#cardTitle {{ color: {t['muted']}; font-size: 12px; font-weight: 600; }}
    QLabel#bigValue {{ font-size: 22px; font-weight: 600; }}
    QPushButton {{ background: {t['raised']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 6px 14px; }}
    QPushButton:hover {{ border-color: {t['accent']}; }}
    QPushButton:pressed {{ background: {t['selection']}; }}
    QPushButton:disabled {{ color: {t['muted']}; }}
    QPushButton#primary {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['accent_text']}; font-weight: 600; }}
    QLineEdit, QComboBox, QPlainTextEdit, QDoubleSpinBox {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 5px 8px; }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {t['accent']}; }}
    QLineEdit:disabled, QComboBox:disabled {{ color: {t['muted']}; background: {t['raised']}; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']}; selection-background-color: {t['selection']}; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {t['muted']}; border-radius: 3px; background: {t['surface']}; }}
    QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
    QTableWidget, QListWidget {{ background: {t['surface']}; alternate-background-color: {t['surface']}; border: 1px solid {t['border']}; border-radius: 6px; gridline-color: {t['grid']}; selection-background-color: {t['selection']}; selection-color: {t['text']}; outline: 0; }}
    QHeaderView::section {{ background: {t['raised']}; color: {t['muted']}; border: none; border-right: 1px solid {t['border']}; border-bottom: 1px solid {t['border']}; padding: 6px 8px; font-weight: 600; font-size: 12px; }}
    QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 6px; background: {t['surface']}; top: -1px; }}
    QTabBar::tab {{ padding: 7px 16px; color: {t['muted']}; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {t['text']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 5px; min-height: 24px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: {t['border']}; border-radius: 5px; min-width: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    """


def pill_style(color: str, radius: int = 6) -> str:
    background, foreground = STATUS[color]
    return f"background: {background}; color: {foreground}; border: none; border-radius: {radius}px; padding: 6px 14px; font-weight: 600;"
