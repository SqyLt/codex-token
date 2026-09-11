#!/usr/bin/env python3
"""codex-usage 伴随窗口 —— 原生 GUI，实时显示 token 消耗与缓存命中率。

数据来自本机看板服务（127.0.0.1），不联网、不需要终端。
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

import tkinter as tk
from tkinter import font as tkfont

PORT = os.environ.get("CODEX_USAGE_PORT", "8788")
API = "http://127.0.0.1:%s/api/snapshot" % PORT
REFRESH_MS = int(os.environ.get("CODEX_USAGE_REFRESH_MS", "1000"))

BG = "#0b0d10"
PANEL = "#12151a"
LINE = "#1f242c"
TEXT = "#e6e8eb"
MUTED = "#8b929c"
ACCENT = "#4c9aff"
GREEN = "#3fb950"
AMBER = "#d29922"
RED = "#f85149"

WIDTH = 392
HEIGHT = 820  # 上限，实际会按屏幕高度自适应收紧
MIN_W, MIN_H = 340, 420
GRIP = 6  # 边缘拖拽缩放的热区宽度
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "window.state")


def fmt(n):
    if n is None:
        return "–"
    return "{:,}".format(int(round(n)))


def short(n):
    if n is None:
        return "–"
    if n >= 1e6:
        return "%.2fM" % (n / 1e6)
    if n >= 1e3:
        return "%.1f k" % (n / 1e3)
    return str(int(n))


def hit_color(r):
    if r is None:
        return MUTED
    return GREEN if r >= 90 else AMBER if r >= 60 else RED


def hit_text(r):
    return "–" if r is None else "%.1f%%" % r


def clock(ts):
    import datetime

    return datetime.datetime.fromtimestamp(ts / 1000.0).strftime("%H:%M:%S")


def pick_font(root):
    families = set(tkfont.families(root))
    for name in (
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Noto Sans CJK HK",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "DejaVu Sans",
    ):
        if name in families:
            return name
    return "TkDefaultFont"


def codex_geometry():
    """用 xdotool 找到 Codex 主窗口，用来把用量窗口贴在它右边。"""
    try:
        screen_w = int(
            subprocess.run(["xdotool", "getdisplaygeometry"],
                           capture_output=True, text=True, timeout=2).stdout.split()[0]
        )
        screen_h = int(
            subprocess.run(["xdotool", "getdisplaygeometry"],
                           capture_output=True, text=True, timeout=2).stdout.split()[1]
        )
    except Exception:
        screen_w, screen_h = 1920, 1080
    try:
        ids = subprocess.run(
            ["xdotool", "search", "--name", "ChatGPT"],
            capture_output=True, text=True, timeout=2,
        ).stdout.split()
        best = None
        for wid in ids:
            out = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", wid],
                capture_output=True, text=True, timeout=2,
            ).stdout
            d = {k: int(v) for k, v in re.findall(r"(X|Y|WIDTH|HEIGHT)=(-?\d+)", out)}
            w, h = d.get("WIDTH", 0), d.get("HEIGHT", 0)
            x, y = d.get("X", 0), d.get("Y", 0)
            # 过滤掉启动画面、离屏辅助窗口之类的异常窗口
            if w < 800 or h < 500 or h > screen_h + 200:
                continue
            if x + w < 0 or x > screen_w or y + h < 0 or y > screen_h:
                continue
            if best is None or w * h > best["WIDTH"] * best["HEIGHT"]:
                best = d
        return best
    except Exception:
        return None


def list_monitors():
    """从 xrandr 解析出各显示器的 (x, y, w, h)。"""
    try:
        out = subprocess.run(["xrandr", "--query"], capture_output=True,
                             text=True, timeout=2).stdout
    except Exception:
        return []
    mons = []
    for line in out.splitlines():
        if " connected" not in line:
            continue
        m = re.search(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", line)
        if m:
            w, h, x, y = (int(g) for g in m.groups())
            mons.append((x, y, w, h))
    return mons


def pick_monitor(mons, geo):
    """选中 Codex 主窗口所在的那块显示器。"""
    if geo and mons:
        cx = geo.get("X", 0) + geo.get("WIDTH", 0) // 2
        cy = geo.get("Y", 0) + geo.get("HEIGHT", 0) // 2
        for mon in mons:
            x, y, w, h = mon
            if x <= cx < x + w and y <= cy < y + h:
                return mon
    return mons[0] if mons else None


_COMM_CACHE = {}


def active_window_info():
    """当前活动窗口的信息：{id, comm, x, y, w, h}。"""
    try:
        out = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                             capture_output=True, text=True, timeout=2).stdout
        m = re.search(r"#\s*(0x[0-9a-fA-F]+)", out)
        if not m or m.group(1) == "0x0":
            return None
        wid = m.group(1)
        comm = _COMM_CACHE.get(wid)
        if comm is None:
            comm = ""
            pid_out = subprocess.run(["xprop", "-id", wid, "_NET_WM_PID"],
                                     capture_output=True, text=True, timeout=2).stdout
            pm = re.search(r"=\s*(\d+)", pid_out)
            if pm:
                try:
                    with open("/proc/%s/comm" % pm.group(1), encoding="utf-8") as fh:
                        comm = fh.read().strip()
                except Exception:
                    comm = ""
            if len(_COMM_CACHE) > 200:
                _COMM_CACHE.clear()
            _COMM_CACHE[wid] = comm
        geo = subprocess.run(["xdotool", "getwindowgeometry", "--shell", wid],
                             capture_output=True, text=True, timeout=2).stdout
        d = {k: int(v) for k, v in re.findall(r"(X|Y|WIDTH|HEIGHT)=(-?\d+)", geo)}
        return {"id": wid, "comm": comm, "x": d.get("X", 0), "y": d.get("Y", 0),
                "w": d.get("WIDTH", 0), "h": d.get("HEIGHT", 0)}
    except Exception:
        return None


class ActiveWindowWatcher(threading.Thread):
    """用 xprop -spy 监听 _NET_ACTIVE_WINDOW，焦点一变就通知主线程。

    比每秒轮询反应快得多（切换窗口瞬间触发），事件只投递到队列，
    真正的 Tk 调用仍由主线程完成。
    """

    def __init__(self, out_queue):
        super().__init__(daemon=True)
        self.queue = out_queue
        self._stop = threading.Event()
        self._proc = None

    def run(self):
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    ["xprop", "-spy", "-root", "_NET_ACTIVE_WINDOW"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                )
                for _line in self._proc.stdout:
                    if self._stop.is_set():
                        break
                    self.queue.put(time.monotonic())
            except Exception:
                pass
            if self._stop.is_set():
                break
            time.sleep(1)  # xprop 掉了（比如 X 重启）就重连

    def stop(self):
        self._stop.set()
        try:
            if self._proc:
                self._proc.terminate()
        except Exception:
            pass


def another_window_running():
    """已存在另一个用量窗口时返回 True，避免开出重复窗口。"""
    try:
        ids = subprocess.run(
            ["xdotool", "search", "--class", "[Cc]odex[Uu]sage"],
            capture_output=True, text=True, timeout=2,
        ).stdout.split()
    except Exception:
        return False
    return bool(ids)


class UsageWindow:
    def __init__(self, root):
        self.root = root
        self.session = os.environ.get("CODEX_USAGE_SESSION", "")
        self.paused = False
        self.last_error = None
        self.data = None
        self._drag = None
        self._tick_n = 0
        self._resize = None
        self._save_job = None

        fam = pick_font(root)
        self.f_title = (fam, 11, "bold")
        self.f_label = (fam, 9)
        self.f_big = (fam, 34, "bold")
        self.f_value = (fam, 11)
        self.f_mono = (fam, 9)
        self.f_small = (fam, 8)

        root.title("Codex 用量")
        root.configure(bg=BG)
        # 这台机器的 GNOME Shell 会把 Tk 的普通窗口卡在映射阶段（Tk 侧 deiconify 也救不回来），
        # 改用无边框窗口绕开 WM 管理；窗口外观由下面的自绘标题栏负责。
        self.borderless = os.environ.get("CODEX_USAGE_BORDERLESS", "1") != "0"
        if self.borderless:
            root.overrideredirect(True)
        # 高度按屏幕自适应：小屏不溢出，大屏尽量多显示调用明细
        self.height = max(520, min(HEIGHT, root.winfo_screenheight() - 140))
        root.geometry("%dx%d" % (WIDTH, self.height))
        root.minsize(340, 520)

        self.build()
        self.place()
        self.ensure_visible()
        # 启动后如果被窗口管理器收起来了，自己再冒出来一次
        self.root.after(1500, self.ensure_visible)
        self.root.after(4000, self.ensure_visible)
        self.tick()

    def ensure_visible(self):
        """窗口被最小化时恢复显示（只在刚启动时兜底，之后不跟用户抢）。"""
        try:
            if self.root.state() == "iconic":
                self.root.deiconify()
            self.root.lift()
        except Exception:
            pass

    def _drain_events(self):
        """把后台线程攒下的焦点变化合并成一次判定。"""
        first = None
        try:
            while True:
                stamp = self._events.get_nowait()
                if first is None:
                    first = stamp
        except queue.Empty:
            pass
        if first is not None:
            self.update_stacking(first)
        self.root.after(50, self._drain_events)

    def start_drag(self, event):
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def on_drag(self, event):
        if not self._drag:
            return
        dx, dy = self._drag
        self.root.geometry("+%d+%d" % (event.x_root - dx, event.y_root - dy))

    # ---------------------------------------------------------------- 缩放
    def edge_at(self, x, y):
        w, h = self.root.winfo_width(), self.root.winfo_height()
        zone = ""
        if x <= GRIP:
            zone += "w"
        elif x >= w - GRIP:
            zone += "e"
        if y <= GRIP:
            zone += "n"
        elif y >= h - GRIP:
            zone += "s"
        return zone

    def cursor_for(self, zone):
        return {
            "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
            "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
            "ne": "top_right_corner", "nw": "top_left_corner",
            "se": "bottom_right_corner", "sw": "bottom_left_corner",
        }.get(zone)

    def on_motion_all(self, event):
        if self._resize:
            return
        zone = self.edge_at(event.x_root - self.root.winfo_rootx(),
                            event.y_root - self.root.winfo_rooty())
        cur = self.cursor_for(zone) or ""
        if cur != getattr(self, "_cursor_now", None):
            self._cursor_now = cur
            try:
                self.root.configure(cursor=cur)
            except Exception:
                pass

    def on_press_all(self, event):
        try:
            x = event.x_root - self.root.winfo_rootx()
            y = event.y_root - self.root.winfo_rooty()
            zone = self.edge_at(x, y)
        except Exception:
            return
        # 点一下就抬到最前（普通窗口的默认行为），但不会长期压着别的软件
        try:
            self.root.lift()
            self._stacked = True
        except Exception:
            pass
        if zone:
            self.start_resize(event, zone)

    def on_drag_all(self, event):
        if self._resize:
            self.do_resize(event)

    def on_release_all(self, _event):
        if self._resize:
            self._resize = None
            self.save_geometry()

    def start_resize(self, event, zone):
        self._drag = None
        self._resize = {
            "zone": zone,
            "x": event.x_root, "y": event.y_root,
            "w": self.root.winfo_width(), "h": self.root.winfo_height(),
            "wx": self.root.winfo_x(), "wy": self.root.winfo_y(),
        }

    def do_resize(self, event):
        r = self._resize
        if not r:
            return
        dx = event.x_root - r["x"]
        dy = event.y_root - r["y"]
        w, h, x, y = r["w"], r["h"], r["wx"], r["wy"]
        zone = r["zone"]
        if "e" in zone:
            w = max(MIN_W, w + dx)
        if "s" in zone:
            h = max(MIN_H, h + dy)
        if "w" in zone:
            nw = max(MIN_W, w - dx)
            x += w - nw
            w = nw
        if "n" in zone:
            nh = max(MIN_H, h - dy)
            y += h - nh
            h = nh
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.refresh_scrollregion()

    # ---------------------------------------------------------------- 尺寸记忆
    def on_root_configure(self, event):
        if event.widget is not self.root or self._resize:
            return
        if self._save_job:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.root.after(1200, self.save_geometry)

    def save_geometry(self):
        self._save_job = None
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as fh:
                fh.write("%d %d %d %d\n" % (
                    self.root.winfo_width(), self.root.winfo_height(),
                    self.root.winfo_x(), self.root.winfo_y()))
        except Exception:
            pass

    def load_geometry(self):
        try:
            with open(STATE_FILE, encoding="utf-8") as fh:
                w, h, x, y = (int(v) for v in fh.read().split()[:4])
        except Exception:
            return None
        if w < MIN_W or h < MIN_H:
            return None
        mons = list_monitors()
        if mons and not any(mx <= x < mx + mw and my <= y < my + mh for (mx, my, mw, mh) in mons):
            return None  # 上次的位置已经不在任何显示器上，重新自动摆放
        return w, h, x, y

    def close(self):
        self.save_geometry()
        try:
            self._watcher.stop()
        except Exception:
            pass
        self.root.destroy()

    # ---------------------------------------------------------------- 布局
    def build(self):
        pad = {"padx": 16}

        # 自绘标题栏：无边框模式下没有系统装饰，拖动和关闭都在这里
        head = tk.Frame(self.root, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        head.pack(fill="x", pady=(0, 8))
        self.dot = tk.Label(head, text="●", bg=PANEL, fg=MUTED, font=(self.f_label[0], 12))
        self.dot.pack(side="left", padx=(12, 0), pady=6)
        self.status = tk.Label(head, text="连接中…", bg=PANEL, fg=MUTED, font=self.f_label)
        self.status.pack(side="left", padx=(6, 0), pady=6)
        close = tk.Label(head, text="✕", bg=PANEL, fg=MUTED, font=self.f_label, padx=10, pady=6)
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.close())
        close.bind("<Enter>", lambda _e: close.configure(fg=RED))
        close.bind("<Leave>", lambda _e: close.configure(fg=MUTED))
        # 默认不置顶：否则会一直压在其他软件上面
        self.topmost = tk.BooleanVar(value=os.environ.get("CODEX_USAGE_TOPMOST", "0") == "1")
        cb = tk.Checkbutton(
            head, text="置顶", variable=self.topmost, command=self.apply_topmost,
            bg=PANEL, fg=MUTED, selectcolor=PANEL, activebackground=PANEL,
            activeforeground=TEXT, font=self.f_small, bd=0, highlightthickness=0,
        )
        cb.pack(side="right", padx=(0, 6))
        for w in (head, self.dot, self.status):
            w.bind("<ButtonPress-1>", self.start_drag)
            w.bind("<B1-Motion>", self.on_drag)
            w.configure(cursor="fleur")

        hero = tk.Frame(self.root, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        hero.pack(fill="x", pady=(0, 10), **pad)
        tk.Label(hero, text="会话缓存命中率", bg=PANEL, fg=MUTED, font=self.f_label).pack(
            anchor="w", padx=12, pady=(10, 0)
        )
        self.big = tk.Label(hero, text="–", bg=PANEL, fg=TEXT, font=self.f_big)
        self.big.pack(anchor="w", padx=12)
        self.hero_sub = tk.Label(hero, text="等待数据…", bg=PANEL, fg=MUTED, font=self.f_mono)
        self.hero_sub.pack(anchor="w", padx=12, pady=(0, 10))

        self.ctx_canvas = tk.Canvas(self.root, width=WIDTH - 40, height=8, bg="#1a1f27",
                                    highlightthickness=0)
        self.ctx_canvas.pack(fill="x", **pad)
        self.ctx_text = tk.Label(self.root, text="上下文 –", bg=BG, fg=MUTED, font=self.f_mono)
        self.ctx_text.pack(anchor="w", pady=(4, 10), **pad)

        self.stats = tk.Frame(self.root, bg=BG)
        self.stats.pack(fill="x", **pad)
        self.stat_labels = {}
        for i, key in enumerate(["call", "turn", "thread", "miss", "cache_write", "calls"]):
            r, c = divmod(i, 2)
            box = tk.Frame(self.stats, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
            box.grid(row=r, column=c, sticky="nsew", padx=(0 if c == 0 else 6, 0), pady=(0, 6))
            tk.Label(box, text=self.stat_title(key), bg=PANEL, fg=MUTED, font=self.f_small).pack(
                anchor="w", padx=10, pady=(8, 0)
            )
            val = tk.Label(box, text="–", bg=PANEL, fg=TEXT, font=self.f_value)
            val.pack(anchor="w", padx=10, pady=(0, 8))
            self.stat_labels[key] = val
        self.stats.columnconfigure(0, weight=1)
        self.stats.columnconfigure(1, weight=1)

        spark_head = tk.Frame(self.root, bg=BG)
        spark_head.pack(fill="x", pady=(6, 4), **pad)
        tk.Label(spark_head, text="最近调用命中率", bg=BG, fg=MUTED, font=self.f_label).pack(side="left")
        self.spark_note = tk.Label(spark_head, text="", bg=BG, fg=MUTED, font=self.f_small)
        self.spark_note.pack(side="right")
        self.spark = tk.Canvas(self.root, height=58, bg=PANEL, highlightthickness=0)
        self.spark.pack(fill="x", pady=(0, 10), **pad)

        calls_head = tk.Frame(self.root, bg=BG)
        calls_head.pack(fill="x", **pad)
        tk.Label(calls_head, text="最近调用", bg=BG, fg=MUTED, font=self.f_label).pack(side="left")
        self.calls_count = tk.Label(calls_head, text="", bg=BG, fg=MUTED, font=self.f_small)
        self.calls_count.pack(side="left", padx=(8, 0))
        self.sel_var = tk.StringVar(value="")
        self.sel_menu = tk.OptionMenu(calls_head, self.sel_var, "")
        self.sel_menu.configure(
            bg=PANEL, fg=TEXT, activebackground=PANEL, activeforeground=TEXT,
            highlightthickness=0, bd=0, font=self.f_small, anchor="e",
        )
        self.sel_menu["menu"].configure(bg=PANEL, fg=TEXT, font=self.f_small)
        self.sel_menu.pack(side="right")

        # 可滚动的调用列表：调用次数多了也不会顶出窗口
        self.calls_wrap = tk.Frame(self.root, bg=BG)
        self.calls_wrap.pack(fill="both", expand=True, pady=(6, 12), **pad)
        self.calls_canvas = tk.Canvas(
            self.calls_wrap, bg=PANEL, highlightthickness=1, highlightbackground=LINE,
            bd=0, height=200, yscrollincrement=0,
        )
        self.calls_scroll = tk.Scrollbar(
            self.calls_wrap, orient="vertical", command=self.calls_canvas.yview,
            width=12, bd=0, highlightthickness=0, relief="flat",
            bg="#2c333d", troughcolor=BG, activebackground="#3a4250",
        )
        self.calls_canvas.configure(yscrollcommand=self.calls_scroll.set)
        self.calls_scroll.pack(side="right", fill="y", padx=(6, 0))
        self.calls_canvas.pack(side="left", fill="both", expand=True)
        self.calls_inner = tk.Frame(self.calls_canvas, bg=PANEL)
        self.calls_window = self.calls_canvas.create_window(
            (0, 0), window=self.calls_inner, anchor="nw"
        )
        self.calls_inner.bind("<Configure>", self.on_calls_configure)
        self.calls_canvas.bind("<Configure>", self.on_canvas_configure)
        for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            self.root.bind_all(seq, self.on_wheel, add="+")

        # 焦点变化事件队列（由后台线程投递，主线程消费）
        self._events = queue.Queue()
        self._watcher = ActiveWindowWatcher(self._events)
        self._watcher.start()
        self.root.after(100, self._drain_events)

        # 无边框窗口没有系统缩放边框，自己实现：边缘热区 + 右下角抓手
        self.grip = tk.Label(self.root, text="◢", bg=BG, fg="#4a515c", font=(self.f_small[0], 10))
        self.grip.place(relx=1.0, rely=1.0, anchor="se", x=-3, y=-3)
        self.grip.configure(cursor="bottom_right_corner")
        self.grip.bind("<ButtonPress-1>", lambda e: self.start_resize(e, "se"))
        self.grip.bind("<B1-Motion>", self.do_resize)
        self.root.bind_all("<Motion>", self.on_motion_all, add="+")
        self.root.bind_all("<ButtonPress-1>", self.on_press_all, add="+")
        self.root.bind_all("<B1-Motion>", self.on_drag_all, add="+")
        self.root.bind_all("<ButtonRelease-1>", self.on_release_all, add="+")
        self.root.bind("<Configure>", self.on_root_configure)

        self.apply_topmost()

    def stat_title(self, key):
        # 用词对齐 Codex 官方界面：input−cached 这个量官方叫 Uncached input tokens（未缓存输入）
        return {
            "call": "本次调用输入",
            "turn": "本轮累计输入",
            "thread": "会话累计输入",
            "miss": "未缓存输入",
            "cache_write": "缓存写入",
            "calls": "调用次数",
        }[key]

    def apply_topmost(self):
        try:
            self.root.attributes("-topmost", bool(self.topmost.get()))
        except Exception:
            pass

    # ---------------------------------------------------------------- 滚动
    def on_calls_configure(self, _event=None):
        self.calls_canvas.configure(scrollregion=self.calls_canvas.bbox("all"))

    def on_canvas_configure(self, event):
        # 让内层行宽跟随可视区宽度
        self.calls_canvas.itemconfigure(self.calls_window, width=event.width)

    def on_wheel(self, event):
        """鼠标滚轮只在指针位于调用列表上方时生效。"""
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        inside = False
        while widget is not None:
            if widget in (self.calls_wrap, self.calls_canvas, self.calls_inner):
                inside = True
                break
            widget = getattr(widget, "master", None)
        if not inside:
            return
        up = getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0
        self.calls_canvas.yview_scroll(-3 if up else 3, "units")

    def place(self):
        self.root.update_idletasks()
        saved = self.load_geometry()
        if saved:
            w, h, x, y = saved
            self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
            return
        geo = codex_geometry()
        mons = list_monitors()
        mon = pick_monitor(mons, geo)
        target = mon
        if len(mons) >= 2:
            # 有多块屏时默认放到 Codex 之外的那块：不重叠就不会触发"隐藏避让"
            others = [m for m in mons if m != mon]
            if others:
                target = others[-1]
        if target:
            mx, my, mw, mh = target
        else:
            mx, my = 0, 0
            mw, mh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        height = min(self.height, max(420, mh - 80))
        # 贴在 Codex 所在那块显示器的右侧，避免跑到另一块屏幕上
        x = mx + mw - WIDTH - 12
        y = my + 48
        if y + height > my + mh:
            y = max(my + 8, my + mh - height - 24)
        self.root.geometry("%dx%d+%d+%d" % (WIDTH, height, x, y))

    # ---------------------------------------------------------------- 数据
    def fetch(self):
        url = API
        if self.session:
            url += "?session=" + urllib.parse.quote(self.session)
        req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def tick(self):
        if not self.paused:
            try:
                data = self.fetch()
                self.last_error = None
                self.render(data)
            except Exception as exc:  # noqa: BLE001 - 任何异常都只影响显示
                self.last_error = str(exc)
                self.show_error()
        # 无边框窗口不受窗口管理器管，层级得自己维护
        self._tick_n += 1
        if self.borderless and self._tick_n % 2 == 0:
            # 兜底轮询：覆盖"焦点没变但对方窗口被移动导致重叠变化"的情况。
            # 焦点切换本身走事件，不受这里影响（即时反应）。
            self.update_stacking()
        self.root.after(REFRESH_MS, self.tick)

    # ---------------------------------------------------------------- 层级
    def _stack(self, on_top, event_at=None):
        if getattr(self, "_stacked", None) == on_top:
            return
        self._stacked = on_top
        stamp = time.strftime("%H:%M:%S") + ".%03d" % (time.time() % 1 * 1000)
        lag = ""
        if event_at:
            lag = "  焦点事件→动作 %.0fms" % ((time.monotonic() - event_at) * 1000)
        print("[stack] %s %s%s" % (stamp, "显示" if on_top else "隐藏避让", lag),
              file=sys.stderr, flush=True)
        try:
            if on_top:
                self.root.deiconify()
                self.root.lift()
            else:
                # 这台机器上 mutter 会把 override-redirect 窗口固定在普通窗口之上，
                # lower() 没有视觉效果，只能整体隐藏才能不遮挡别人
                self._remember_geometry()
                self.root.withdraw()
        except Exception:
            pass

    def _pointer_inside(self):
        try:
            px, py = self.root.winfo_pointerxy()
            if self.root.state() == "withdrawn":
                x, y, w, h = getattr(self, "_geom", (0, 0, 0, 0))
            else:
                x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
                w, h = self.root.winfo_width(), self.root.winfo_height()
            return x <= px < x + w and y <= py < y + h
        except Exception:
            return False

    def _remember_geometry(self):
        try:
            self._geom = (self.root.winfo_rootx(), self.root.winfo_rooty(),
                          self.root.winfo_width(), self.root.winfo_height())
        except Exception:
            pass

    def overlaps(self, info):
        if not info:
            return False
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        return not (info["x"] + info["w"] <= x or info["x"] >= x + w
                    or info["y"] + info["h"] <= y or info["y"] >= y + h)

    def update_stacking(self, event_at=None):
        """置顶勾选时永远在最前；否则只在“别的软件在前台且跟我们重叠”时隐藏自己，
        避免遮挡别人。隐藏后把鼠标移到它原来的位置不会再弹出来（那样反而会挡住别人），
        切回 Codex 或点应用菜单入口即可恢复。"""
        try:
            if self._tick_n < 3:   # 刚启动先露个面，避免以为没打开
                self._stack(True, event_at)
                return
            if self.topmost.get():
                self._stack(True, event_at)
                return
            info = active_window_info()
            if info is None or info.get("comm") == "ChatGPT" or not self.overlaps(info):
                self._stack(True, event_at)
            else:
                self._stack(False, event_at)
        except Exception:
            pass

    def show_error(self):
        self.dot.configure(fg=RED)
        self.status.configure(text="看板服务未连接")
        self.hero_sub.configure(text="请确认 codex-usage 伴随脚本在运行")

    # ---------------------------------------------------------------- 渲染
    def render(self, data):
        if data.get("error"):
            self.show_error()
            return
        u = data["usage"]
        active = u.get("last_event_at") and (data["now"] - self.ts(u["last_event_at"])) < 3000
        self.dot.configure(fg=GREEN if active else "#3a4250")
        self.status.configure(
            text="%s · %s · %s" % (
                "进行中" if active else "空闲",
                (u.get("session_id") or "")[:8],
                u.get("model") or "?",
            )
        )

        thread = u.get("thread")
        r = thread.get("hit_rate") if thread else None
        self.big.configure(text=hit_text(r), fg=hit_color(r))
        if thread:
            self.hero_sub.configure(
                text="缓存命中 %s / 总输入 %s · 未缓存 %s"
                % (fmt(thread["cached"]), fmt(thread["input"]),
                   fmt(max(0, thread["input"] - thread["cached"])))
            )
        else:
            self.hero_sub.configure(text="等待第一次调用…")

        pct = u.get("context_percent")
        self.draw_bar(pct)
        self.ctx_text.configure(
            text="上下文 %s" % ("–" if pct is None else "%.1f%%  (%s / %s)"
                                % (pct, fmt(u.get("context_used")), fmt(u.get("context_window"))))
        )

        call = u.get("last_call")
        turn = u.get("last_turn")
        self.stat_labels["call"].configure(text=fmt(call["input"]) if call else "–")
        self.stat_labels["turn"].configure(text=fmt(turn["input"]) if turn else "–")
        self.stat_labels["thread"].configure(text=fmt(thread["input"]) if thread else "–")
        self.stat_labels["miss"].configure(
            text=fmt(max(0, thread["input"] - thread["cached"])) if thread else "–"
        )
        self.stat_labels["cache_write"].configure(
            text=fmt(thread["cacheWrite"]) if thread else "–"
        )
        self.stat_labels["calls"].configure(text=str(u.get("calls") or 0))

        hist = data.get("hit_history") or []
        self.draw_spark(hist)
        if hist:
            avg = sum(hist[-40:]) / len(hist[-40:])
            self.spark_note.configure(text="近 %d 次 · 平均 %.1f%%" % (len(hist[-40:]), avg))
        else:
            self.spark_note.configure(text="")

        self.render_calls(data.get("recent_calls") or [])
        self.render_sessions(data.get("sessions") or [])

    def ts(self, iso):
        import datetime

        try:
            return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000
        except Exception:
            return 0

    def draw_bar(self, pct):
        c = self.ctx_canvas
        c.delete("all")
        w = c.winfo_width() or (WIDTH - 40)
        h = 8
        p = 0 if pct is None else max(0.0, min(100.0, pct))
        color = RED if p >= 85 else AMBER if p >= 60 else ACCENT
        if p > 0:
            c.create_rectangle(0, 0, w * p / 100.0, h, fill=color, outline="")

    def draw_spark(self, hist):
        c = self.spark
        c.delete("all")
        w = c.winfo_width() or (WIDTH - 40)
        h = int(c["height"])
        data = hist[-40:]
        if not data:
            return
        c.create_line(0, 9, w, 9, fill="#2a313b", dash=(3, 3))
        step = w / float(len(data))
        bw = max(1.0, step - 2)
        for i, v in enumerate(data):
            v = max(0.0, min(100.0, v))
            bh = max(1.0, v / 100.0 * (h - 6))
            x = i * step
            c.create_rectangle(x, h - bh, x + bw, h, fill=hit_color(v), outline="")

    def render_calls(self, calls):
        try:
            keep = self.calls_canvas.yview()[0]
        except Exception:
            keep = 0.0
        for child in self.calls_inner.winfo_children():
            child.destroy()
        rows = list(reversed(calls))  # 最新的排在最上面
        self.calls_count.configure(text=("共 %d 次 · 滚轮可翻看" % len(rows)) if rows else "")
        if not rows:
            tk.Label(self.calls_inner, text="暂无调用记录", bg=PANEL, fg=MUTED,
                     font=self.f_small).pack(anchor="w", padx=10, pady=8)
            self.refresh_scrollregion()
            return
        for i, c in enumerate(rows):
            bg = PANEL if i % 2 == 0 else "#161a20"
            row = tk.Frame(self.calls_inner, bg=bg)
            row.pack(fill="x", pady=(0 if i == 0 else 1, 0))
            tk.Label(row, text=clock(c["ts"]) if c.get("ts") else "–", bg=bg, fg=MUTED,
                     font=self.f_mono).pack(side="left")
            tk.Label(row, text="输入 %s" % fmt(c["input"]), bg=bg, fg=TEXT,
                     font=self.f_mono).pack(side="left", padx=(10, 0))
            tk.Label(row, text=hit_text(c.get("hit_rate")), bg=bg,
                     fg=hit_color(c.get("hit_rate")), font=self.f_mono).pack(side="right")
            tk.Label(row, text="输出 %s" % fmt(c["output"]), bg=bg, fg=MUTED,
                     font=self.f_mono).pack(side="right", padx=(0, 10))
        tk.Frame(self.calls_inner, bg=PANEL, height=2).pack()
        self.refresh_scrollregion()
        try:
            self.calls_canvas.yview_moveto(keep)
        except Exception:
            pass

    def refresh_scrollregion(self):
        self.calls_inner.update_idletasks()
        self.calls_canvas.configure(scrollregion=self.calls_canvas.bbox("all"))

    def render_sessions(self, sessions):
        ids = [s["id"] for s in sessions]
        current = self.session or (ids[0] if ids else "")
        if current and not any(s.startswith(current) or current.startswith(s) for s in ids):
            ids = [current] + ids
        label = lambda sid: sid[:8]  # noqa: E731
        self.sel_menu["menu"].delete(0, "end")
        for sid in ids[:12]:
            self.sel_menu["menu"].add_command(
                label=sid, command=lambda s=sid: self.switch(s)
            )
        if ids:
            self.sel_var.set(current[:8] if current else ids[0][:8])
        self.sel_menu.configure(text=self.sel_var.get())

    def switch(self, sid):
        self.session = sid
        self.sel_var.set(sid[:8])
        self.sel_menu.configure(text=sid[:8])


def main():
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        sys.stderr.write("没有可用的图形环境（DISPLAY 未设置）\n")
        return 1
    if another_window_running() and not os.environ.get("CODEX_USAGE_FORCE"):
        sys.stderr.write("已经有一个用量窗口在运行，跳过\n")
        return 0
    try:
        # 用自定义 WM_CLASS 标识窗口，避免依赖中文标题（标题编码会随 locale 变化）
        root = tk.Tk(className="CodexUsage")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("无法创建窗口：%s\n" % exc)
        return 1
    UsageWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
