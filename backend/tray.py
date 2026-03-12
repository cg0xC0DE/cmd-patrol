"""
cmd-patrol system tray wrapper.
Launches the Flask backend as a subprocess, monitors it, and auto-restarts on crash.
Provides a tray icon with quick actions.
"""
import os
import sys
import time
import subprocess
import threading
import webbrowser
import urllib.request

from PIL import Image, ImageDraw, ImageFont
import pystray

URL = "http://127.0.0.1:51314"
BACKEND_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
BACKEND_CWD = os.path.dirname(os.path.abspath(__file__))

# ── State ─────────────────────────────────────────────────────
backend_proc: subprocess.Popen = None
lock = threading.Lock()
should_quit = False
tray_icon: pystray.Icon = None


# ── Icon generation ───────────────────────────────────────────
def create_icon_image(color="#3b82f6"):
    """Create a simple 64x64 icon with 'CP' text."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # rounded-ish background
    draw.rounded_rectangle([2, 2, 62, 62], radius=12, fill=color)
    # text
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    draw.text((32, 32), "CP", fill="white", font=font, anchor="mm")
    return img


def icon_green():
    return create_icon_image("#22c55e")


def icon_red():
    return create_icon_image("#ef4444")


def icon_blue():
    return create_icon_image("#3b82f6")


# ── Backend management ────────────────────────────────────────
def start_backend():
    global backend_proc
    with lock:
        if backend_proc and backend_proc.poll() is None:
            return  # already running
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        backend_proc = subprocess.Popen(
            [sys.executable, BACKEND_SCRIPT],
            cwd=BACKEND_CWD,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    update_icon_status()


def stop_backend():
    global backend_proc
    with lock:
        if backend_proc and backend_proc.poll() is None:
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend_proc.kill()
        backend_proc = None
    update_icon_status()


def restart_backend():
    stop_backend()
    time.sleep(0.5)
    start_backend()


def is_backend_alive():
    with lock:
        if backend_proc is None or backend_proc.poll() is not None:
            return False
    # Also do a quick HTTP check
    try:
        urllib.request.urlopen(URL + "/api/mq/stats", timeout=3)
        return True
    except Exception:
        # Process is alive but HTTP not ready yet — still alive
        with lock:
            return backend_proc is not None and backend_proc.poll() is None


def update_icon_status():
    if tray_icon is None:
        return
    alive = is_backend_alive()
    tray_icon.icon = icon_green() if alive else icon_red()
    tray_icon.title = f"cmd-patrol — {'运行中' if alive else '已停止'}"


# ── Watchdog thread ───────────────────────────────────────────
def watchdog():
    """Monitor backend process; auto-restart if it dies."""
    while not should_quit:
        time.sleep(3)
        if should_quit:
            break
        with lock:
            proc_dead = backend_proc is None or backend_proc.poll() is not None
        if proc_dead and not should_quit:
            print("[tray] Backend died, auto-restarting...")
            start_backend()
        update_icon_status()


# ── Tray menu actions ─────────────────────────────────────────
def on_open_browser(icon, item):
    webbrowser.open(URL)


def on_start_all(icon, item):
    try:
        req = urllib.request.Request(
            URL + "/api/services/start-all",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=b"{}",
        )
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"[tray] start-all failed: {e}")


def on_stop_all(icon, item):
    try:
        # Call individual stop for each running service
        data = urllib.request.urlopen(URL + "/api/services", timeout=5).read()
        import json
        services = json.loads(data)
        for s in services:
            if s.get("status") == "running":
                req = urllib.request.Request(
                    URL + f"/api/services/{s['id']}/stop",
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[tray] stop-all failed: {e}")


def on_restart_backend(icon, item):
    threading.Thread(target=restart_backend, daemon=True).start()


def on_quit(icon, item):
    global should_quit
    should_quit = True
    stop_backend()
    icon.stop()


# ── Main ──────────────────────────────────────────────────────
def main():
    global tray_icon

    menu = pystray.Menu(
        pystray.MenuItem("打开面板", on_open_browser, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("启动全部服务", on_start_all),
        pystray.MenuItem("停止全部服务", on_stop_all),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("重启后端", on_restart_backend),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )

    tray_icon = pystray.Icon("cmd-patrol", icon_blue(), "cmd-patrol — 启动中...", menu)

    # Start backend before entering tray loop
    start_backend()

    # Start watchdog
    wd = threading.Thread(target=watchdog, daemon=True)
    wd.start()

    # pystray.run() blocks — this is the main loop
    tray_icon.run()


if __name__ == "__main__":
    main()
