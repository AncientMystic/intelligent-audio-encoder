"""PySide6 GUI: device pick + mode-aware bitrate + source/output folders + batch queue.

GPU honesty: the device picker controls analysis/QA acceleration only.
Encoding is always the CPU ffmpeg -> exhale/fdkaac pipe.
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

APP_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "app_config.json")


def _load_app_config() -> dict:
    try:
        if os.path.exists(APP_CONFIG):
            with open(APP_CONFIG, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_app_config(cfg: dict) -> None:
    try:
        with open(APP_CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def main(argv=None):
    from PySide6.QtCore import QObject, Qt, QThread, Signal
    from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QGroupBox, QHBoxLayout,
                                   QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                                   QProgressBar, QPushButton, QRadioButton, QSlider, QSpinBox,
                                   QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
    import sys
    from core.devices import list_devices, save_selection, load_selection
    from core.queue import collect_jobs_with_mode, run_job

    app = QApplication(sys.argv if argv is None else argv)
    cfg = _load_app_config()
    w = QMainWindow()
    w.setWindowTitle("intelligent-audio-encoder — audiobook batch")
    root = QWidget()
    lay = QVBoxLayout(root)

    # ---- row 1: compute device ----
    row1 = QHBoxLayout()
    row1.addWidget(QLabel("Compute Backend:"))
    backend = QComboBox()
    backend.addItems(["Auto", "CPU", "CUDA", "DirectML", "MPS"])
    row1.addWidget(backend)
    row1.addWidget(QLabel("GPU:"))
    gpu = QComboBox()
    row1.addWidget(gpu, 1)
    refresh_btn = QPushButton("Refresh")
    row1.addWidget(refresh_btn)
    lay.addLayout(row1)
    dev_status = QLabel("")
    lay.addWidget(dev_status)

    def refill():
        all_devs = list_devices()
        want = backend.currentText().lower()
        gpu.clear()
        for d in all_devs:
            if want not in ("auto",) and d.backend != want:
                continue
            label = f"[{d.backend}:{d.index}] {d.name}"
            if d.detail:
                label += f" — {d.detail}"
            gpu.addItem(label, d)
        if gpu.count():
            dev_status.setText(f"{gpu.count()} device(s) detected. Selection persists to gpu_selection.json.")
        else:
            dev_status.setText("No devices for this backend — try Auto or Refresh after installing torch/onnxruntime-directml.")

    def apply_selection():
        d = gpu.currentData()
        if d is not None:
            save_selection(d)
            dev_status.setText(f"Selected [{d.backend}:{d.index}] {d.name}. "
                               "Used for spectral probe + QA only; encode stays on CPU pipe.")

    saved = load_selection()
    if saved:
        be = (saved.get("backend") or "auto").capitalize()
        idx = backend.findText(be)
        backend.setCurrentIndex(idx if idx >= 0 else 0)
    elif cfg.get("backend"):
        idx = backend.findText(cfg["backend"])
        backend.setCurrentIndex(idx if idx >= 0 else 0)
    refill()
    if saved:
        for i in range(gpu.count()):
            d = gpu.itemData(i)
            if d is not None and d.backend == saved.get("backend") and d.index == saved.get("index"):
                gpu.setCurrentIndex(i)
                break
    backend.currentTextChanged.connect(refill)
    gpu.currentIndexChanged.connect(lambda _i: apply_selection())
    refresh_btn.clicked.connect(refill)

    # ---- row 2: codec + mode-dependent bitrate ----
    row2 = QHBoxLayout()
    row2.addWidget(QLabel("Codec:"))
    codec = QComboBox()
    codec.addItems(["Auto (advisor picks)", "AAC-LC (max compatibility)", "xHE-AAC (smallest files)",
                    "HE-AAC (compatible)", "HE-AACv2 (stereo low)", "MP3"])
    if cfg.get("codec"):
        idx = codec.findText(cfg["codec"])
        codec.setCurrentIndex(idx if idx >= 0 else 0)
    row2.addWidget(codec)
    row2.addWidget(QLabel("Mode:"))
    mode = QComboBox()
    mode.addItems(["transparent", "maximum_saving", "archive", "manual kbps", "% of source"])
    if cfg.get("mode"):
        idx = mode.findText(cfg["mode"])
        mode.setCurrentIndex(idx if idx >= 0 else 0)
    row2.addWidget(mode)

    br_label = QLabel("Bitrate:")
    br = QSpinBox()
    br.setRange(16, 320)
    br.setValue(int(cfg.get("bitrate_kbps", 24)))
    br.setSuffix(" kbps")
    row2.addWidget(br_label)
    row2.addWidget(br)

    pct_label = QLabel("Percent:")
    pct = QSpinBox()
    pct.setRange(10, 150)
    pct.setValue(int(cfg.get("percent", 75)))
    pct.setSuffix(" %")
    pct.setToolTip("Target bitrate as % of probed source bitrate (e.g. 75% of 32k ≈ 24k)")
    row2.addWidget(pct_label)
    row2.addWidget(pct)

    auto_hint = QLabel("")
    auto_hint.setToolTip("Advisor picks bitrate per file from probe (codec/SR/ch/cutoff/content). No per-file input needed.")
    row2.addWidget(auto_hint, 1)
    lay.addLayout(row2)
    mode_status = QLabel("")
    lay.addWidget(mode_status)

    AUTO_HINTS = {
        "transparent": ("Auto: advisor picks a transparent target per file — "
                        "e.g. TTS → ~21k xHE, 32k mono → 24k xHE, "
                        "128k stereo → 80k HE, 640k lossless → 96k LC."),
        "maximum_saving": ("Auto: smallest safe target per file — "
                           "e.g. TTS → ~18k xHE, 32k mono → 24k xHE, "
                           "128k stereo → 80k HE, 640k lossless → 96k LC."),
        "archive": ("Auto: high-quality target per file — "
                    "e.g. TTS → 28k xHE, 32k mono → 32k LC, "
                    "128k stereo → 112k LC, 640k lossless → 128k LC, stereo always preserved."),
    }

    def current_rate_setting() -> dict:
        """Single source of truth for batch wiring: {mode, bitrate_kbps?, percent?}."""
        m = mode.currentText()
        if m == "manual kbps":
            return {"mode": m, "bitrate_kbps": br.value()}
        if m == "% of source":
            return {"mode": m, "percent": pct.value()}
        return {"mode": m}

    def refresh_mode_ui():
        m = mode.currentText()
        if m in AUTO_HINTS:
            br_label.setVisible(False)
            br.setVisible(False)
            br.setEnabled(False)
            pct_label.setVisible(False)
            pct.setVisible(False)
            pct.setEnabled(False)
            auto_hint.setVisible(True)
            auto_hint.setText(AUTO_HINTS[m])
            mode_status.setText(f"Mode '{m}': bitrate is chosen automatically per file.")
        elif m == "manual kbps":
            br_label.setVisible(True)
            br.setVisible(True)
            br.setEnabled(True)
            pct_label.setVisible(False)
            pct.setVisible(False)
            pct.setEnabled(False)
            auto_hint.setVisible(False)
            mode_status.setText(f"Mode 'manual kbps': every file encoded at {br.value()} kbps.")
        else:  # "% of source"
            br_label.setVisible(False)
            br.setVisible(False)
            br.setEnabled(False)
            pct_label.setVisible(True)
            pct.setVisible(True)
            pct.setEnabled(True)
            auto_hint.setVisible(False)
            mode_status.setText(f"Mode '% of source': every file encoded at {pct.value()}% of its probed bitrate.")

    mode.currentTextChanged.connect(lambda _t: refresh_mode_ui())
    br.valueChanged.connect(lambda _v: refresh_mode_ui())
    pct.valueChanged.connect(lambda _v: refresh_mode_ui())
    refresh_mode_ui()
    # expose for tests / batch wiring
    w.current_rate_setting = current_rate_setting

    # ---- row 3: QA + workers ----
    row3 = QHBoxLayout()
    row3.addWidget(QLabel("Validation % (1-100):"))
    vs = QSlider(Qt.Horizontal)
    vs.setRange(1, 100)
    vs.setValue(int(cfg.get("qa_pct", 5)))
    row3.addWidget(vs)
    vlab = QLabel(f"{vs.value()}%")
    vs.valueChanged.connect(lambda v: vlab.setText(f"{v}%"))
    row3.addWidget(vlab)
    row3.addWidget(QLabel("Workers:"))
    wk = QSpinBox()
    wk.setRange(1, 32)
    wk.setValue(int(cfg.get("workers", 10)))
    row3.addWidget(wk)
    lay.addLayout(row3)

    # ---- folders group (underneath) ----
    folders = QGroupBox("Folders")
    flay = QVBoxLayout(folders)
    src_row = QHBoxLayout()
    src_row.addWidget(QLabel("Source folder:"))
    source_edit = QLineEdit()
    source_edit.setReadOnly(True)
    source_edit.setPlaceholderText("No source folder selected — Browse or Add folder…")
    if cfg.get("source_dir"):
        source_edit.setText(cfg["source_dir"])
    src_row.addWidget(source_edit, 1)
    src_browse = QPushButton("Browse…")
    src_row.addWidget(src_browse)
    flay.addLayout(src_row)

    dest_row = QHBoxLayout()
    dest_source_radio = QRadioButton("Source folder")
    dest_output_radio = QRadioButton("Output folder")
    if cfg.get("dest_mode", "source") == "output":
        dest_output_radio.setChecked(True)
    else:
        dest_source_radio.setChecked(True)
    dest_row.addWidget(QLabel("Output:"))
    dest_row.addWidget(dest_source_radio)
    dest_row.addWidget(dest_output_radio)
    dest_edit = QLineEdit()
    dest_edit.setReadOnly(True)
    dest_edit.setPlaceholderText("No output folder selected — Browse…")
    if cfg.get("output_dir"):
        dest_edit.setText(cfg["output_dir"])
    dest_row.addWidget(dest_edit, 1)
    dest_browse = QPushButton("Browse…")
    dest_row.addWidget(dest_browse)
    flay.addLayout(dest_row)

    warn_label = QLabel("WARNING: Source folder output replaces files in place! "
                        "Originals are kept as .bak next to each output.")
    warn_label.setStyleSheet("color: #b00020; font-weight: bold;")
    flay.addWidget(warn_label)

    def refresh_dest_ui():
        to_source = dest_source_radio.isChecked()
        warn_label.setVisible(to_source)
        dest_edit.setEnabled(not to_source)
        dest_browse.setEnabled(not to_source)

    dest_source_radio.toggled.connect(lambda _c: refresh_dest_ui())
    refresh_dest_ui()
    lay.addWidget(folders)

    def get_batch_config() -> dict:
        d = gpu.currentData()
        return {
            "source_dir": source_edit.text().strip(),
            "dest_mode": "source" if dest_source_radio.isChecked() else "output",
            "output_dir": dest_edit.text().strip(),
            "codec": codec.currentText(),
            "rate": current_rate_setting(),
            "workers": wk.value(),
            "qa_pct": vs.value(),
            "backend": (d.backend if d is not None else "cpu"),
            "gpu_index": (d.index if d is not None else 0),
        }

    w.get_batch_config = get_batch_config

    def persist_config():
        c = get_batch_config()
        _save_app_config({
            "source_dir": c["source_dir"], "dest_mode": c["dest_mode"],
            "output_dir": c["output_dir"], "codec": c["codec"],
            "mode": c["rate"]["mode"], "bitrate_kbps": c["rate"].get("bitrate_kbps", 24),
            "percent": c["rate"].get("percent", 75), "workers": c["workers"],
            "qa_pct": c["qa_pct"], "backend": backend.currentText(),
        })

    # ---- job table + progress ----
    table = QTableWidget(0, 6)
    table.setHorizontalHeaderLabels(["Source", "Target", "Rate", "Est MB", "Status", "Note"])
    table.horizontalHeader().setStretchLastSection(True)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    lay.addWidget(table, 1)
    prog_row = QHBoxLayout()
    progress = QProgressBar()
    progress.setRange(0, 100)
    prog_row.addWidget(progress, 1)
    stop_btn = QPushButton("Stop")
    stop_btn.setEnabled(False)
    prog_row.addWidget(stop_btn)
    lay.addLayout(prog_row)
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setMaximumBlockCount(500)
    log.setPlaceholderText("Batch log…")
    lay.addWidget(log)

    jobs: list = []
    row_by_src: dict = {}
    cancel_event = threading.Event()
    thread_holder: dict = {}

    def log_line(msg: str):
        log.appendPlainText(msg)

    def rate_summary() -> str:
        r = current_rate_setting()
        if r["mode"] == "manual kbps":
            return f"{r['bitrate_kbps']} kbps"
        if r["mode"] == "% of source":
            return f"{r['percent']}%"
        return f"auto/{r['mode']}"

    def rescan_jobs():
        nonlocal jobs
        jobs = []
        row_by_src.clear()
        table.setRowCount(0)
        src_root = source_edit.text().strip()
        if not src_root or not os.path.isdir(src_root):
            return
        mode_sel = "source" if dest_source_radio.isChecked() else "output"
        out_dir = dest_edit.text().strip() or None
        if mode_sel == "output" and (not out_dir or not os.path.isdir(out_dir)):
            log_line("Pick an output folder first (or switch Output back to Source folder).")
            return
        jobs = collect_jobs_with_mode(src_root, mode_sel, out_dir)
        table.setRowCount(len(jobs))
        for i, job in enumerate(jobs):
            row_by_src[job.src] = i
            table.setItem(i, 0, QTableWidgetItem(os.path.relpath(job.src, src_root)))
            table.setItem(i, 1, QTableWidgetItem(job.dst))
            table.setItem(i, 2, QTableWidgetItem(rate_summary()))
            table.setItem(i, 3, QTableWidgetItem("—"))
            table.setItem(i, 4, QTableWidgetItem("queued"))
            table.setItem(i, 5, QTableWidgetItem(""))
        progress.setRange(0, max(1, len(jobs)))
        progress.setValue(0)
        log_line(f"Scanned {len(jobs)} file(s) from {src_root} "
                 f"({'in place — replaces files, .bak kept' if mode_sel == 'source' else f'into {out_dir}'}, {rate_summary()}).")
        persist_config()

    def browse_source():
        start = source_edit.text().strip() or os.path.expanduser("~")
        picked = QFileDialog.getExistingDirectory(w, "Select source folder", start)
        if picked:
            source_edit.setText(picked)
            rescan_jobs()

    def browse_output():
        start = dest_edit.text().strip() or source_edit.text().strip() or os.path.expanduser("~")
        picked = QFileDialog.getExistingDirectory(w, "Select output folder", start)
        if picked:
            dest_edit.setText(picked)
            dest_output_radio.setChecked(True)
            refresh_dest_ui()
            rescan_jobs()

    src_browse.clicked.connect(browse_source)
    dest_browse.clicked.connect(browse_output)

    class BatchWorker(QObject):
        job_done = Signal(dict)
        message = Signal(str)
        finished = Signal()

        def __init__(self, job_list, config, cancel):
            super().__init__()
            self.job_list = job_list
            self.config = config
            self.cancel = cancel

        def run(self):
            workers = max(1, min(32, int(self.config.get("workers", 4))))
            self.message.emit(f"Starting {len(self.job_list)} job(s) with {workers} worker(s)…")
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_to_src = {}
                for job in self.job_list:
                    if self.cancel.is_set():
                        break
                    future_to_src[pool.submit(
                        run_job, job.src, job.dst,
                        self.config["rate"], self.config["codec"],
                        self.config["backend"], self.config["gpu_index"],
                        self.config["qa_pct"], self.cancel)] = job.src
                for future in as_completed(future_to_src):
                    try:
                        self.job_done.emit(future.result())
                    except Exception as e:  # never let one job kill the batch
                        self.job_done.emit({"src": future_to_src[future], "dst": "",
                                            "status": "failed", "note": f"worker error: {e}"})
            self.finished.emit()

    def set_running(running: bool):
        for btn in action_buttons:
            btn.setEnabled(not running)
        src_browse.setEnabled(not running)
        dest_browse.setEnabled(not running)
        dest_source_radio.setEnabled(not running)
        dest_output_radio.setEnabled(not running)
        stop_btn.setEnabled(running)

    def on_job_done(res: dict):
        i = row_by_src.get(res.get("src", ""))
        if i is None:
            return
        status = res.get("status", "?")
        table.setItem(i, 4, QTableWidgetItem(status))
        detail = res.get("note", "")
        if status == "done":
            table.setItem(i, 3, QTableWidgetItem(f"{res.get('src_mb', '?')}→{res.get('dst_mb', '?')}"))
            table.setItem(i, 2, QTableWidgetItem(f"{res.get('target_kbps', '?')}k {res.get('encoder', '')}"))
        table.setItem(i, 5, QTableWidgetItem(detail))
        done = sum(1 for r in range(table.rowCount())
                   if (table.item(r, 4) or QTableWidgetItem("")).text() in ("done", "failed", "cancelled"))
        progress.setValue(done)
        log_line(f"[{status}] {os.path.basename(res.get('src', '?'))} — {detail}")

    def on_finished():
        th = thread_holder.pop("thread", None)
        if th is not None:
            th.quit()
            th.wait()
        set_running(False)
        persist_config()
        log_line("Batch finished.")

    def start_batch():
        if thread_holder.get("thread") is not None:
            return
        if not jobs:
            QMessageBox.information(w, "No jobs", "Add a source folder first.")
            return
        config = get_batch_config()
        if config["dest_mode"] == "output" and not config["output_dir"]:
            QMessageBox.information(w, "No output folder", "Select an output folder first.")
            return
        if config["dest_mode"] == "source":
            answer = QMessageBox.warning(
                w, "Replace files?",
                "Output = Source folder.\n\nFiles will be REPLACED in place "
                "(originals kept as .bak). Continue?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return
        # refresh destinations in case controls changed since scan
        fresh = collect_jobs_with_mode(config["source_dir"] or ".", config["dest_mode"],
                                       config["output_dir"] or None)
        if len(fresh) != len(jobs):
            rescan_jobs()
            QMessageBox.information(w, "Jobs changed", "Folder contents changed — list rescanned. Press Start again.")
            return
        cancel_event.clear()
        persist_config()
        set_running(True)
        progress.setValue(0)
        for r in range(table.rowCount()):
            table.setItem(r, 4, QTableWidgetItem("queued"))
        worker = BatchWorker(jobs, config, cancel_event)
        thread = QThread(w)
        worker.moveToThread(thread)
        worker.job_done.connect(on_job_done)
        worker.message.connect(log_line)
        worker.finished.connect(on_finished)
        thread.started.connect(worker.run)
        thread_holder["thread"] = thread
        thread.start()

    def stop_batch():
        cancel_event.set()
        log_line("Stopping after in-flight jobs…")

    stop_btn.clicked.connect(stop_batch)

    # ---- row 4: actions ----
    row4 = QHBoxLayout()
    action_buttons: list = []

    def _action(text, handler):
        b = QPushButton(text)
        b.clicked.connect(handler)
        row4.addWidget(b)
        action_buttons.append(b)
        return b

    _action("Add folder…", browse_source)
    _action("Output folder…", browse_output)
    start_btn = _action("Start batch", start_batch)

    def identify_book():
        rows = table.selectionModel().selectedRows() if table.selectionModel() else []
        if rows and jobs:
            src = jobs[rows[0].row()].src
        elif jobs:
            src = jobs[0].src
        else:
            QMessageBox.information(w, "Identify book", "Add a source folder first, then select a row.")
            return
        try:
            from core.enrich import guess_from_path, search_openlibrary, search_googlebooks
            guess = guess_from_path(src)
            cands = (search_openlibrary(guess.get("title", ""), guess.get("author", ""), 5)
                     + search_googlebooks(guess.get("title", ""), guess.get("author", ""), 5))
        except Exception as e:
            QMessageBox.warning(w, "Identify book", f"Lookup failed: {e}")
            return
        if not cands:
            QMessageBox.information(w, "Identify book",
                                    f"No candidates for '{guess.get('title')}' / '{guess.get('author')}'.")
            return
        lines = [f"{i + 1}. [{c.get('source')}] {c.get('title')} — {c.get('author')} ({c.get('year') or '?'})"
                 for i, c in enumerate(cands[:10])]
        QMessageBox.information(w, "Identify book (pick in a later step)",
                                "Top candidates:\n" + "\n".join(lines)
                                + "\n\nApplying the pick to tags + cover lands with the enrichment editor.")

    def validate_sample():
        try:
            from core.validator import quick_check
        except Exception as e:
            QMessageBox.warning(w, "Validate sample", f"Validator unavailable: {e}")
            return
        rows = table.selectionModel().selectedRows() if table.selectionModel() else []
        target = None
        if rows and jobs:
            job = jobs[rows[0].row()]
            if os.path.exists(job.dst):
                target = job
        if target is None and jobs:
            done = [j for j in jobs if os.path.exists(j.dst)]
            target = done[0] if done else None
        if target is None:
            QMessageBox.information(w, "Validate sample", "No encoded output yet — run the batch first.")
            return
        d = gpu.currentData()
        res = quick_check(target.src, target.dst, pct=vs.value(),
                          backend=(d.backend if d is not None else "cpu"),
                          gpu_index=(d.index if d is not None else 0))
        QMessageBox.information(w, f"QA: {res.get('status')}",
                                f"{target.dst}\n\n{res.get('reason', '')}\n\n{res.get('metrics', {})}")

    _action("Identify book", identify_book)
    _action("Validate sample", validate_sample)
    lay.addLayout(row4)
    lay.addWidget(QLabel("GPU = analysis/QA acceleration. Encode = CPU ffmpeg → exhale/fdkaac pipe."))

    # initial scan if a source folder was remembered
    if source_edit.text().strip():
        rescan_jobs()

    w.setCentralWidget(root)
    w.resize(1024, 720)
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
