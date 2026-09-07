"""Compute-device enumeration for analysis/QA acceleration.

Honest model: audio *encoding* is always CPU (ffmpeg -> exhale/fdkaac pipe).
The GPU only accelerates spectral probe + validation QA. This module finds
every usable compute device so the user can pick one explicitly:

  backends: cpu (always) | cuda (NVIDIA via torch/nvidia-smi) |
            directml (any GPU via onnxruntime-directml) | mps (Apple Silicon via torch)

Sources probed in order (all optional, failures are swallowed):
  1. torch CUDA devices (names + VRAM)
  2. nvidia-smi (covers machines without torch installed)
  3. onnxruntime DirectML provider flag
  4. Windows Win32_VideoController via wmic/powershell (Display adapters incl. Intel Arc/AMD)
  5. Linux lspci VGA/3D entries
  6. torch MPS (Apple Silicon)

Selection is explicit: resolve_selection(backend, gpu) with "auto" defaults
(fastest CUDA -> DirectML -> MPS -> CPU) and persisted to gpu_selection.json
via save_selection()/load_selection() so GUI + CLI agree.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import json
import os
import platform
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELECTION_FILE = os.path.join(ROOT, "gpu_selection.json")


@dataclass
class Device:
    backend: str  # cpu | cuda | directml | mps
    index: int    # index within backend, 0-based
    name: str
    detail: str = ""

    def key(self) -> str:
        return f"{self.backend}:{self.index}"


def _cuda_via_torch() -> list[Device]:
    try:
        import torch
        if not torch.cuda.is_available():
            return []
        out = []
        for i in range(torch.cuda.device_count()):
            try:
                props = torch.cuda.get_device_properties(i)
                out.append(Device("cuda", i, f"{props.name}",
                                  f"{props.total_memory // 1024 ** 2} MiB VRAM"))
            except Exception:
                out.append(Device("cuda", i, f"CUDA device {i}", ""))
        return out
    except Exception:
        return []


def _nvidia_via_smi() -> list[str]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                            "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
    except Exception:
        pass
    return []


def _directml_present() -> bool:
    try:
        import onnxruntime as ort
        return "DmlExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


def _windows_display_adapters() -> list[str]:
    if platform.system() != "Windows":
        return []
    # PowerShell CIM is present on Win10/11; wmic is deprecated/removed on Win11.
    for cmd in (
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
        ["wmic", "path", "win32_VideoController", "get", "name"],
    ):
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            names = [l.strip() for l in (r.stdout or "").splitlines()
                     if l.strip() and l.strip().lower() not in ("name",)]
            if names:
                return names
        except Exception:
            continue
    return []


def _linux_display_adapters() -> list[str]:
    if platform.system() != "Linux" or not shutil.which("lspci"):
        return []
    try:
        r = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
        out = []
        for line in (r.stdout or "").splitlines():
            if "VGA" in line or "3D controller" in line or "Display controller" in line:
                out.append(line.split(": ", 1)[-1].strip())
        return out
    except Exception:
        return []


def _mps_present() -> bool:
    try:
        import torch
        return bool(getattr(torch.backends, "mps", None) is not None
                    and torch.backends.mps.is_available())
    except Exception:
        return False


def list_devices() -> list[Device]:
    devs: list[Device] = [Device("cpu", 0, "CPU", "always available; runs encode + fallback analysis")]

    cuda = _cuda_via_torch()
    if cuda:
        devs.extend(cuda)
    else:
        for i, name in enumerate(_nvidia_via_smi()):
            devs.append(Device("cuda", i, name, "via nvidia-smi; install torch for compute use"))

    if _directml_present():
        devs.append(Device("directml", 0, "DirectML GPU(s)",
                           "via onnxruntime-directml; works with NVIDIA/AMD/Intel"))
    else:
        # Show physical adapters so users on dual-GPU rigs (e.g. NVIDIA compute +
        # Intel Arc display) can see what's there and what to install.
        adapters = _windows_display_adapters() + _linux_display_adapters()
        seen = set()
        for i, name in enumerate(adapters):
            if name in seen:
                continue
            seen.add(name)
            devs.append(Device("directml", i, name,
                               "display adapter; install onnxruntime-directml to use for compute"))

    if _mps_present():
        devs.append(Device("mps", 0, "Apple Silicon GPU", "via torch MPS backend"))

    return devs


def _auto_default(devs: list[Device]) -> Device:
    for backend in ("cuda", "directml", "mps"):
        cands = [d for d in devs if d.backend == backend and "install" not in d.detail]
        if cands:
            return cands[0]
    # fall back to informational entries, then CPU
    for d in devs:
        if d.backend in ("cuda", "directml", "mps"):
            return d
    return devs[0]


def resolve_selection(backend: str = "auto", gpu: str = "auto") -> Device:
    """Resolve user choice. backend in auto/cpu/cuda/directml/mps; gpu is index str or 'auto'."""
    devs = list_devices()
    backend = (backend or "auto").lower()
    if backend == "auto" and (gpu or "auto").lower() == "auto":
        saved = load_selection()
        if saved:
            for d in devs:
                if d.backend == saved.get("backend") and d.index == saved.get("index"):
                    return d
        return _auto_default(devs)
    pool = [d for d in devs if d.backend == backend] if backend != "auto" else devs
    if not pool:
        return _auto_default(devs)
    if (gpu or "auto").lower() == "auto":
        usable = [d for d in pool if "install" not in d.detail]
        return usable[0] if usable else pool[0]
    try:
        idx = int(gpu)
    except (TypeError, ValueError):
        return pool[0]
    for d in pool:
        if d.index == idx:
            return d
    return pool[0]


def save_selection(device: Device) -> None:
    try:
        with open(SELECTION_FILE, "w", encoding="utf-8") as f:
            json.dump({"backend": device.backend, "index": device.index,
                       "name": device.name}, f, indent=2)
    except Exception:
        pass


def load_selection() -> dict | None:
    try:
        if os.path.exists(SELECTION_FILE):
            with open(SELECTION_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def describe_all() -> str:
    return "\n".join(f"[{d.backend}:{d.index}] {d.name} — {d.detail}" for d in list_devices())
