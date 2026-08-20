"""Color schemes for VoxKey. Each returns a (bg, panel, border, text, accent, chunk)."""
SCHEMES = {
    "default":       {"bg":"#0b0b0d","panel":"#141416","border":"#2a2a30","text":"#f2f2f4","accent":"#ffffff","sub":"#8a8a92","chunk":"#4ade80"},
    "neon_pink":     {"bg":"#12000a","panel":"#1e0714","border":"#3d1028","text":"#ffd6ec","accent":"#ff2d95","sub":"#c77","chunk":"#ff2d95"},
    "electric_blue": {"bg":"#000814","panel":"#001d3d","border":"#003566","text":"#cde8ff","accent":"#00b4ff","sub":"#6a9","chunk":"#00b4ff"},
    "xp":            {"bg":"#ece9d8","panel":"#ffffff","border":"#aca899","text":"#000000","accent":"#0a5bd7","sub":"#555","chunk":"#3a6ea5"},
    "deep_red":      {"bg":"#0a0000","panel":"#1a0303","border":"#3a0808","text":"#ffcccc","accent":"#e10600","sub":"#a55","chunk":"#e10600"},
    "neon_green":    {"bg":"#02120a","panel":"#04240f","border":"#0a3d18","text":"#c8ffcf","accent":"#39ff14","sub":"#7a7","chunk":"#39ff14"},
}
SCHEME_LABELS = {
    "default":"Default (dark)", "neon_pink":"Neon Pink", "electric_blue":"Electric Blue",
    "xp":"Classic Windows XP", "deep_red":"HAL 9000 Deep Red", "neon_green":"MP3 Neon Green",
}

def stylesheet(scheme_name: str) -> str:
    c = SCHEMES.get(scheme_name, SCHEMES["default"])
    return f"""
        QMainWindow, QWidget {{ background:{c['bg']}; color:{c['text']}; font-size:14px; }}
        QTabWidget::pane {{ border:0; top:-1px; }}
        QTabBar::tab {{ background:transparent; padding:9px 20px; margin:2px;
            border-radius:10px; color:{c['sub']}; font-weight:500; }}
        QTabBar::tab:selected {{ background:{c['panel']}; color:{c['accent']}; }}
        QPushButton {{ background:{c['panel']}; border:1px solid {c['border']};
            padding:8px 16px; border-radius:10px; color:{c['text']}; font-weight:500; }}
        QPushButton:hover {{ border:1px solid {c['accent']}; }}
        QPushButton:default {{ background:{c['accent']}; color:{c['bg']}; border:0; }}
        QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{ background:{c['panel']};
            border:1px solid {c['border']}; border-radius:10px; padding:8px 10px; color:{c['text']}; }}
        QLineEdit:focus, QComboBox:focus {{ border:1px solid {c['accent']}; }}
        QCheckBox {{ color:{c['text']}; spacing:9px; }}
        QLabel {{ color:{c['text']}; }}
        QGroupBox {{ border:1px solid {c['border']}; border-radius:12px; margin-top:14px;
            padding-top:10px; color:{c['sub']}; font-weight:500; }}
        QGroupBox::title {{ subcontrol-origin:margin; left:14px; padding:0 6px; }}
        QListWidget {{ background:{c['panel']}; border:1px solid {c['border']};
            border-radius:10px; padding:4px; }}
        QScrollBar:vertical {{ background:transparent; width:10px; }}
        QScrollBar::handle:vertical {{ background:{c['border']}; border-radius:5px; }}
        QProgressBar {{ background:{c['panel']}; border:1px solid {c['border']};
            border-radius:8px; }}
        QProgressBar::chunk {{ background:{c['chunk']}; border-radius:7px; }}
    """
