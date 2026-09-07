"""Entry point — minimal boot to prove scaffold imports."""
import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="intelligent-audio-encoder",
                                 description="Intelligent batch audiobook transcoder")
    ap.add_argument("--backend", default="auto", choices=["auto", "cpu", "cuda", "directml"],
                    help="Compute backend for analysis/QA (encode is always CPU pipe)")
    ap.add_argument("--gpu", default="auto",
                    help="GPU index within backend, or 'auto'. Use --list-gpus to see choices.")
    ap.add_argument("--list-gpus", action="store_true", help="List detected GPUs and exit")
    ap.add_argument("--gui", action="store_true", help="Launch the GUI (default when no other action)")
    args = ap.parse_args(argv)

    print("intelligent-audio-encoder OK")
    print("Python:", sys.version.split()[0])

    from core.devices import list_devices, resolve_selection
    if args.list_gpus:
        for d in list_devices():
            marker = " [DEFAULT]" if d == resolve_selection("auto", "auto") else ""
            print(f" - backend={d.backend} index={d.index} name={d.name} detail={d.detail}{marker}")
        return 0
    try:
        for d in list_devices():
            print(" -", d)
        sel = resolve_selection(args.backend, args.gpu)
        print("Selected compute device:", sel)
    except Exception as e:
        print("devices probe skipped:", e)
    try:
        from core.advisor import advise_for_sample, SAMPLE_32K_MONO
        from core.encoder import check_ffmpeg
        rec = advise_for_sample(SAMPLE_32K_MONO, avail=check_ffmpeg())
        print("Advisor demo (32k mono reference):", rec)
    except Exception as e:
        print("advisor demo skipped:", e)
    if args.gui or True:
        print("Run GUI: start_gui.bat (Windows) / ./start_gui.sh (Linux/macOS), or: python -m gui.main_window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
