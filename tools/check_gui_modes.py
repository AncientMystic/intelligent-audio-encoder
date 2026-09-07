"""Offscreen smoke test: mode switching drives the bitrate controls + setting dict."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QSpinBox

import gui.main_window as m


def _find(window, widget_type, predicate):
    found = next((w for w in window.findChildren(widget_type) if predicate(w)), None)
    assert found is not None, f"widget not found: {widget_type}"
    return found


def fake_exec(self):
    wins = [w for w in QApplication.topLevelWidgets()
            if "intelligent-audio-encoder" in w.windowTitle()]
    assert wins, "main window not found"
    wroot = wins[0]
    mode = _find(wroot, QComboBox, lambda c: c.findText("maximum_saving") >= 0)
    spins = wroot.findChildren(QSpinBox)
    br = next(s for s in spins if s.maximum() == 320)
    pct = next(s for s in spins if s.maximum() == 150)
    auto_hint = next((w for w in wroot.findChildren(QLabel) if w.text().startswith("Auto:")), None)

    for name, want in [("transparent", "auto"), ("maximum_saving", "auto"), ("archive", "auto"),
                       ("manual kbps", "br"), ("% of source", "pct")]:
        mode.setCurrentText(name)
        QApplication.processEvents()
        s = wroot.current_rate_setting()
        assert s["mode"] == name, (name, s)
        if want == "auto":
            assert not br.isVisible() and not pct.isVisible(), name
            assert "bitrate_kbps" not in s and "percent" not in s, s
        elif want == "br":
            assert br.isVisible() and br.isEnabled() and not pct.isVisible(), name
            assert s["bitrate_kbps"] == br.value(), s
        else:
            assert pct.isVisible() and pct.isEnabled() and not br.isVisible(), name
            assert s["percent"] == pct.value(), s

    # auto hints: no banned phrasing, multiple examples each
    for name in ["transparent", "maximum_saving", "archive"]:
        mode.setCurrentText(name)
        QApplication.processEvents()
    assert auto_hint is not None and auto_hint.isVisible(), "auto hint should be visible in auto mode"
    assert "Bitrate input off" not in auto_hint.text(), auto_hint.text()
    assert auto_hint.text().count("→") >= 3, auto_hint.text()
    print("GUI mode-switch smoke test: 5/5 PASS (+ hint wording PASS)")
    # destination controls: source default + warning visible; output mode shows folder box
    from PySide6.QtWidgets import QRadioButton, QLineEdit
    radios = wroot.findChildren(QRadioButton)
    src_radio = next(r for r in radios if r.text() == "Source folder")
    out_radio = next(r for r in radios if r.text() == "Output folder")
    assert src_radio.isChecked(), "source folder should be the default output"
    cfg = wroot.get_batch_config()
    assert cfg["dest_mode"] == "source" and cfg["source_dir"] == "", cfg
    out_radio.setChecked(True)
    QApplication.processEvents()
    cfg = wroot.get_batch_config()
    assert cfg["dest_mode"] == "output", cfg
    src_radio.setChecked(True)
    QApplication.processEvents()
    print("GUI destination smoke test: PASS")
    return 0


with patch.object(QApplication, "exec", fake_exec):
    rc = m.main([])
    assert rc == 0, rc
print("main() returned cleanly")
