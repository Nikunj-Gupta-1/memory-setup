from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path
from typing import List, Optional

from memory_setup.biometrics.cluster import cluster_and_generate_avatars
from memory_setup.biometrics.detector import run_biometric_face_scan
from memory_setup.config import ArchiveContext
from memory_setup.gui.app import launch_gui
from memory_setup.ingestion.router import (
    run_stage1_consolidation,
    run_stage2_standardization,
    run_stage3_validation,
)
from memory_setup.pipeline_runner import get_archive_metrics
from memory_setup.setup.checker import run_diagnostics
from memory_setup.setup.installer import ensure_ollama_running, install_guidance, pull_ollama_model
from memory_setup.viewer.builder import run_stage5_museum
from memory_setup.vision.ollama_client import run_vision_pipeline


def _print_progress(evt: dict) -> None:
    stage = evt.get("stage", "")
    cur = evt.get("current", 0)
    total = evt.get("total", 0)
    msg = evt.get("message", "")
    rate = evt.get("rate_img_s")
    suffix = f" ({rate:.2f} img/s)" if isinstance(rate, (int, float)) and rate > 0 else ""
    if total:
        print(f"[{stage}] {cur}/{total} — {msg}{suffix}", flush=True)
    else:
        print(f"[{stage}] {msg}", flush=True)


def _cmd_setup(args: argparse.Namespace) -> int:
    archive = Path(args.archive).expanduser().resolve() if args.archive else None
    diag = run_diagnostics(archive_path=archive, mode=args.mode, ollama_model=args.model)
    if args.ensure_ollama:
        diag["ollama_start"] = ensure_ollama_running()
        if args.pull_model:
            diag["ollama_pull"] = pull_ollama_model(model=args.model)
    disk_ok = bool(diag.get("disk", {}).get("ok", True))
    if args.json:
        print(json.dumps(diag, indent=2, default=str))
        return 0 if disk_ok else 1

    plat = diag["platform"]
    tools = diag["tools"]
    disk = diag["disk"]
    bio = diag["biometrics"]
    ol = diag["ollama"]
    print("=== Memory Setup Pre-Flight Diagnostics ===")
    print(f"Platform:         {plat['system']} {plat['machine']} ({plat['release']})")
    print(f"Python:           {tools['python']['version']} ({tools['python']['executable']})")
    print(f"Disk Safety:      {disk['message']} [{'OK' if disk_ok else 'LOW'}]")
    print(
        f"Core Tools:       exiftool={'YES' if tools['exiftool']['installed'] else 'NO'}, "
        f"sips={'YES' if tools['sips']['installed'] else 'NO'}, "
        f"pillow_heif={'YES' if tools['sips']['pillow_heif'] else 'NO'}, "
        f"magick={'YES' if tools['magick']['installed'] else 'NO'}"
    )
    print(
        f"Biometrics:       insightface={'YES' if bio['package_installed'] else 'NO'}, "
        f"buffalo_l={'YES' if bio['weights_cached'] else 'NO'}, "
        f"providers={', '.join(diag['onnx_providers']) or 'none'}"
    )
    print(
        f"Ollama Daemon:    installed={ol['binary_installed']}, running={ol['running']}, "
        f"target_model_ready={ol['target_model_ready']}, models={ol['models']}"
    )
    if args.show_install:
        print("\nInstall Guidance:")
        for k, v in install_guidance().items():
            print(f"  {k}: {v}")
    return 0 if disk_ok else 1


def _cmd_run(args: argparse.Namespace) -> int:
    archive_root = Path(args.folder).expanduser().resolve()
    sources = [Path(s).expanduser().resolve() for s in (args.source or [])]
    mode = "safe_copy" if args.safe_copy else "in_place"
    speed_mode = "turbo" if args.turbo else "whisper"

    ctx = ArchiveContext(
        archive_root=archive_root,
        source_dirs=sources,
        mode=mode,
        speed_mode=speed_mode,
        enable_biometrics=not args.no_biometrics,
        enable_vision=not args.no_vision,
        ollama_model=args.model,
    )

    diag = run_diagnostics(archive_path=ctx.archive_root, mode=ctx.mode, ollama_model=ctx.ollama_model)
    if not diag["disk"]["ok"] and not args.force:
        print(
            f"ERROR: Disk check failed ({diag['disk']['message']}). Pass --force to override.",
            file=sys.stderr,
        )
        return 2

    stop_event = threading.Event()

    def _handle_sigint(signum, frame):  # type: ignore[no-untyped-def]
        print("\nInterrupt received — saving progress and shutting down cleanly...", flush=True)
        stop_event.set()

    prev_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        stages = args.stages or ["stage1", "stage2", "stage3", "stage4_faces", "stage4_vision", "stage5"]
        if "stage1" in stages and not stop_event.is_set():
            res1 = run_stage1_consolidation(ctx, progress_cb=_print_progress)
            print(f"Stage 1 summary: {json.dumps(res1)}")
        if "stage2" in stages and not stop_event.is_set():
            res2 = run_stage2_standardization(ctx, progress_cb=_print_progress)
            print(f"Stage 2 summary: {json.dumps(res2)}")
        if "stage3" in stages and not stop_event.is_set():
            res3 = run_stage3_validation(ctx, progress_cb=_print_progress)
            print(f"Stage 3 summary: {json.dumps(res3)}")
        if "stage4_faces" in stages and ctx.enable_biometrics and not stop_event.is_set():
            scan = run_biometric_face_scan(ctx, progress_cb=_print_progress)
            clus = cluster_and_generate_avatars(ctx)
            print(f"Stage 4A summary: {json.dumps({'scan': scan, 'clustering': clus})}")
        if "stage4_vision" in stages and ctx.enable_vision and not stop_event.is_set():
            res4b = run_vision_pipeline(
                ctx,
                limit=args.vision_limit,
                stop_event=stop_event,
                progress_cb=_print_progress,
            )
            print(f"Stage 4B summary: {json.dumps(res4b)}")
        if "stage5" in stages:
            res5 = run_stage5_museum(ctx, overwrite_html=args.overwrite_html)
            print(f"Stage 5 summary: {json.dumps(res5)}")
    finally:
        signal.signal(signal.SIGINT, prev_handler)

    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    archive_root = Path(args.folder).expanduser().resolve()
    ctx = ArchiveContext(archive_root=archive_root)
    res = run_stage5_museum(ctx, overwrite_html=args.overwrite_html)
    print(json.dumps(res, indent=2))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    archive_root = Path(args.folder).expanduser().resolve()
    metrics = get_archive_metrics(archive_root)
    print(json.dumps(metrics, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-setup",
        description="Universal Local Media Intelligence Engine & Offline Museum Control Center",
    )
    parser.add_argument("--setup", action="store_true", help="Run pre-flight system diagnostics and exit")
    parser.add_argument("--archive", type=str, default=None, help="Archive root folder for GUI or --setup")
    parser.add_argument("--mode", choices=["in_place", "safe_copy"], default="in_place", help="Ingestion mode")
    parser.add_argument("--port", type=int, default=8765, help="Control Center HTTP port (default: 8765)")
    parser.add_argument("--no-webview", action="store_true", help="Skip native pywebview window")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open browser tab")
    parser.add_argument("--json", action="store_true", help="Print JSON output for --setup")
    parser.add_argument("--ensure-ollama", action="store_true", help="Attempt to start Ollama daemon during --setup")
    parser.add_argument("--pull-model", action="store_true", help="Pull Ollama vision model during --setup")
    parser.add_argument("--show-install", action="store_true", help="Print installation instructions during --setup")
    parser.add_argument("--model", type=str, default="moondream:latest", help="Ollama VLM model name")

    subparsers = parser.add_subparsers(dest="command")

    p_run = subparsers.add_parser("run", help="Run full headless ingestion & AI pipeline on a folder")
    p_run.add_argument("folder", help="Target Archive folder (holds content/ and viewer/)")
    p_run.add_argument("--source", action="append", default=[], help="Extra external source folder(s) for Safe Copy")
    p_run.add_argument("--inplace", action="store_true", help="Organize directly inside folder/content/ (default)")
    p_run.add_argument("--safe-copy", action="store_true", help="Copy files into folder/content/, leaving sources untouched")
    p_run.add_argument("--whisper", action="store_true", help="Memory-safe VLM recycling every 200 items (default)")
    p_run.add_argument("--turbo", action="store_true", help="Higher-throughput VLM recycling every 500 items")
    p_run.add_argument("--no-biometrics", action="store_true", help="Skip ArcFace face scan and clustering")
    p_run.add_argument("--no-vision", action="store_true", help="Skip local Ollama vision AI captioning")
    p_run.add_argument("--stages", nargs="+", default=None, help="Explicit subset of stages to run")
    p_run.add_argument("--vision-limit", type=int, default=None, help="Cap number of VLM photos in this run")
    p_run.add_argument("--model", type=str, default="moondream:latest", help="Ollama vision model")
    p_run.add_argument("--overwrite-html", action="store_true", help="Allow overwriting viewer.html during Stage 5")
    p_run.add_argument("--force", action="store_true", help="Override free disk space safety warning")

    p_sync = subparsers.add_parser("sync", help="Rebuild FTS5 index, catalog_data.js, and Offline Museum assets")
    p_sync.add_argument("folder", help="Target Archive folder")
    p_sync.add_argument("--overwrite-html", action="store_true", help="Overwrite existing viewer.html")

    p_status = subparsers.add_parser("status", help="Inspect read-only archive metrics from catalog.sqlite")
    p_status.add_argument("folder", help="Target Archive folder")

    p_setup = subparsers.add_parser("setup", help="Run pre-flight system diagnostics")
    p_setup.add_argument("--archive", type=str, default=None, help="Archive root folder to inspect")
    p_setup.add_argument("--mode", choices=["in_place", "safe_copy"], default="in_place")
    p_setup.add_argument("--json", action="store_true")
    p_setup.add_argument("--ensure-ollama", action="store_true")
    p_setup.add_argument("--pull-model", action="store_true")
    p_setup.add_argument("--show-install", action="store_true")
    p_setup.add_argument("--model", type=str, default="moondream:latest")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "setup" or args.setup:
        return _cmd_setup(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "sync":
        return _cmd_sync(args)
    if args.command == "status":
        return _cmd_status(args)

    archive_path = Path(args.archive).expanduser().resolve() if args.archive else None
    launch_gui(
        default_archive=archive_path,
        port=args.port,
        use_webview=not args.no_webview,
        open_browser=not args.no_browser,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
