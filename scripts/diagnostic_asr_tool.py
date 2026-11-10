#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone test for DashScope ASR connectivity.

Usage:
    python3 scripts/diagnostic_asr_tool.py [--model MODEL] [--duration SECONDS]

Tests:
1. WebSocket connection (on_open)
2. Audio sending (no exceptions)
3. Recognition events (on_event with text)
4. Session lifecycle (on_close / on_complete)
"""
# pylint: disable=too-many-branches,too-many-statements

import argparse
import os
import sys
import tempfile
import threading
import time

from platformdirs import user_config_path


def _write_diagnostic_log(model, events, chunks_sent, send_errors):
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="agenticx-asr-",
        suffix=".log",
        delete=False,
    ) as log_file:
        log_file.write(f"Model: {model}\n")
        log_file.write(f"on_open: {events['on_open']}\n")
        log_file.write(f"on_close: {events['on_close']}\n")
        log_file.write(f"on_complete: {events['on_complete']}\n")
        log_file.write(f"on_error: {events['on_error']}\n")
        log_file.write(f"event_count: {events['on_event_count']}\n")
        log_file.write(f"chunks_sent: {chunks_sent}\n")
        log_file.write(f"send_errors: {send_errors}\n")
        log_file.write(f"texts: {events['on_event_texts']}\n")
        log_file.write("\nRaw results:\n")
        for result in events["raw_results"]:
            log_file.write(f"  {result}\n")
        return log_file.name


def main():
    parser = argparse.ArgumentParser(
        description="Test DashScope ASR connectivity",
    )
    parser.add_argument(
        "--model",
        default="paraformer-realtime-v2",
        help="ASR model",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=5,
        help="Recording duration in seconds",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="DashScope API key (or set DASHSCOPE_API_KEY)",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        # Try loading from workspace config
        try:
            import tomli

            for path in [
                "workspace.toml",
                os.path.join(
                    user_config_path("AgenticX", "DashScope"),
                    "workspace.toml",
                ),
            ]:
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        cfg = tomli.load(f)
                    key = cfg.get("tongyi_api_key", "")
                    if key:
                        api_key = key
                        print(f"[OK] Loaded API key from {path}")
                        break
        except Exception:
            pass

    if not api_key:
        print(
            "[FAIL] No API key found. Set DASHSCOPE_API_KEY or "
            "configure via /key tongyi",
        )
        sys.exit(1)

    masked = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "***"
    print(f"Model:    {args.model}")
    print(f"API Key:  {masked}")
    print(f"Duration: {args.duration}s")
    print()

    # Step 1: Import
    print("[1/5] Importing dashscope...")
    try:
        import dashscope

        print(f"  SDK version: {getattr(dashscope, '__version__', 'unknown')}")
    except ImportError:
        print("[FAIL] dashscope not installed. pip install dashscope")
        sys.exit(1)

    from dashscope.audio.asr import Recognition, RecognitionCallback

    # Step 2: Setup callback
    print("[2/5] Setting up Recognition + callbacks...")
    events = {
        "on_open": False,
        "on_close": False,
        "on_complete": False,
        "on_error": None,
        "on_event_count": 0,
        "on_event_texts": [],
        "raw_results": [],
    }
    lock = threading.Lock()

    class TestCallback(RecognitionCallback):
        def on_open(self):
            with lock:
                events["on_open"] = True
            print("  [CALLBACK] on_open — WebSocket connected")

        def on_event(self, result):
            with lock:
                events["on_event_count"] += 1
                events["raw_results"].append(str(result))
            output = getattr(result, "output", None) or {}
            sentence = output.get("sentence", {})
            if isinstance(sentence, dict):
                text = sentence.get("text", "")
                is_final = sentence.get("end_time") is not None
                tag = "FINAL" if is_final else "partial"
                print(f'  [CALLBACK] on_event ({tag}): "{text}"')
                if text:
                    with lock:
                        events["on_event_texts"].append(text)

        def on_close(self):
            with lock:
                events["on_close"] = True
            print("  [CALLBACK] on_close")

        def on_complete(self):
            with lock:
                events["on_complete"] = True
            print("  [CALLBACK] on_complete")

        def on_error(self, result):
            msg = getattr(result, "message", None) or str(result)
            with lock:
                events["on_error"] = msg
                events["raw_results"].append(f"ERROR: {result}")
            print(f"  [CALLBACK] on_error: {msg}")

    callback = TestCallback()
    recognition = Recognition(
        model=args.model,
        callback=callback,
        format="pcm",
        sample_rate=16000,
        language_hints=["zh", "en"],
    )

    # Step 3: Start
    print("[3/5] Starting Recognition session...")
    t0 = time.time()
    try:
        recognition.start()
    except Exception as e:
        print(f"  [FAIL] start() raised: {type(e).__name__}: {e}")
        sys.exit(1)

    # Wait for on_open
    for _ in range(30):  # 3 seconds max
        if events["on_open"]:
            break
        time.sleep(0.1)

    if events["on_open"]:
        print(f"  [OK] on_open fired in {time.time() - t0:.2f}s")
    else:
        print(f"  [WARN] on_open NOT fired after {time.time() - t0:.2f}s")

    # Step 4: Record + send audio
    print(f"[4/5] Recording {args.duration}s from microphone...")
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        print(
            "  [FAIL] sounddevice/numpy not installed. "
            "pip install sounddevice numpy",
        )
        sys.exit(1)

    chunks_sent = 0
    send_errors = 0

    def audio_callback(indata, _frames, _time_info, _status):
        nonlocal chunks_sent, send_errors
        audio_bytes = indata.tobytes()
        chunks_sent += 1
        try:
            recognition.send_audio_frame(audio_bytes)
        except Exception as e:
            send_errors += 1
            if send_errors <= 3:
                print(f"  [SEND ERROR] {type(e).__name__}: {e}")

    try:
        with sd.InputStream(
            samplerate=16000,
            channels=1,
            dtype=np.int16,
            blocksize=3200,
            callback=audio_callback,
        ):
            print("  Recording... speak now!")
            for i in range(args.duration):
                time.sleep(1)
                with lock:
                    evt_count = events["on_event_count"]
                print(
                    f"  {i+1}s — chunks:{chunks_sent} "
                    f"events:{evt_count} send_errors:{send_errors}",
                )
    except Exception as e:
        print(f"  [FAIL] Microphone error: {e}")
        sys.exit(1)

    # Step 5: Stop + report
    print("[5/5] Stopping Recognition...")
    try:
        recognition.stop()
    except Exception as e:
        print(f"  stop() error: {e}")

    # Wait for on_complete/on_close
    for _ in range(50):  # 5 seconds max
        if events["on_complete"] or events["on_close"]:
            break
        time.sleep(0.1)

    print()
    print("=" * 50)
    print("RESULTS:")
    print(f"  WebSocket open:     {'✓' if events['on_open'] else '✗'}")
    print(f"  WebSocket close:    {'✓' if events['on_close'] else '✗'}")
    print(f"  on_complete:        {'✓' if events['on_complete'] else '✗'}")
    print(f"  on_error:           {events['on_error'] or '(none)'}")
    print(f"  Chunks sent:        {chunks_sent}")
    print(f"  Send errors:        {send_errors}")
    print(f"  Recognition events: {events['on_event_count']}")
    if events["on_event_texts"]:
        print(f"  Recognized text:    {''.join(events['on_event_texts'])}")
    print()

    # Raw results for debugging
    if events["raw_results"] and events["on_event_count"] == 0:
        print("  Raw results (for debugging):")
        for r in events["raw_results"][:5]:
            print(f"    {r[:200]}")

    if events["on_event_count"] > 0:
        print("[PASS] ASR working correctly!")
    elif send_errors > 0:
        print(
            "[FAIL] Connection dropped during recording. "
            "Check API key permissions.",
        )
    elif not events["on_open"]:
        print("[FAIL] WebSocket never connected. Check network/firewall.")
    else:
        print(
            "[FAIL] Connected but no recognition events. "
            "Model or audio format issue.",
        )

    # Save raw data for debugging
    log_path = _write_diagnostic_log(
        args.model,
        events,
        chunks_sent,
        send_errors,
    )
    print(f"\nDetailed log: {log_path}")


if __name__ == "__main__":
    main()
