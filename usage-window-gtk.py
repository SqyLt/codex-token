#!/usr/bin/env python3
"""codex-usage 伴随窗口（GTK 版）

GTK 窗口能被窗口管理器正常托管，所以缩放、层级、任务栏都走系统原生行为。
"""
import datetime
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

PORT = os.environ.get("CODEX_USAGE_PORT", "8788")
API = "http://127.0.0.1:%s/api/snapshot" % PORT
REFRESH_MS = int(os.environ.get("CODEX_USAGE_REFRESH_MS", "1000"))
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "window.state")

BG = "#0b0d10"
PANEL = "#12151a"
LINE = "#1f242c"
TEXT = "#e6e8eb"
MUTED = "#8b929c"
ACCENT = "#4c9aff"
GREEN = "#3fb950"
AMBER = "#d29922"
RED = "#f85149"

MIN_W, MIN_H = 340, 420
DEFAULT_W, DEFAULT_H = 392, 820

CSS = """
window { background-color: %(bg)s; }
box, grid, label { color: %(text)s; }
.card { background-color: %(panel)s; border: 1px solid %(line)s; border-radius: 8px; }
.muted { color: %(muted)s; font-size: 9pt; }
.small { color: %(muted)s; font-size: 8pt; }
.tiny { color: %(muted)s; font-size: 8pt; }
.section { color: %(muted)s; font-size: 9pt; font-weight: bold; }
.mono { font-family: monospace; font-size: 9pt; }
button, combobox, checkbutton { background-image: none; }
combobox button { background-color: %(panel)s; color: %(text)s; border: 1px solid %(line)s; }
list, listbox, row { background-color: transparent; }
scrolledwindow { border: 1px solid %(line)s; border-radius: 8px; background-color: %(panel)s; }
""" % {"bg": BG, "panel": PANEL, "line": LINE, "text": TEXT, "muted": MUTED}


def fmt(n):
    return "–" if n is None else "{:,}".format(int(round(n)))


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
    return datetime.datetime.fromtimestamp(ts / 1000.0).strftime("%H:%M:%S")


def colorize(text, color, size=None, weight=None):
    attrs = ['foreground="%s"' % color]
    if size:
        attrs.append('size="%s"' % size)
    if weight:
        attrs.append('weight="%s"' % weight)
    return "<span %s>%s</span>" % (" ".join(attrs), GLib.markup_escape_text(text))


class UsageWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Codex 用量")
        # 用固定 WM_CLASS 便于外部按类名找到这个窗口（中文标题编码不可靠）
        self.set_wmclass("codex-usage", "CodexUsage")
        self.set_resizable(True)
        self.set_size_request(MIN_W, MIN_H)

        self.session = os.environ.get("CODEX_USAGE_SESSION", "")
        self.paused = False
        self._save_source = None
        self._calls_sig = None
        self._data = None

        style = Gtk.CssProvider()
        style.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._restore_geometry()
        self._build()
        self.connect("destroy", Gtk.main_quit)
        self.connect("configure-event", self._on_configure)
        GLib.timeout_add(REFRESH_MS, self._tick)

    # ---------------------------------------------------------------- 布局
    def _build(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(10)
        outer.set_margin_bottom(10)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        self.add(outer)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.dot = Gtk.Label(label="●")
        self.dot.set_markup(colorize("●", MUTED, size="large"))
        head.pack_start(self.dot, False, False, 0)
        self.status = Gtk.Label(label="连接中…")
        self.status.set_halign(Gtk.Align.START)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        head.pack_start(self.status, True, True, 0)
        self.session_box = Gtk.ComboBoxText()
        self.session_box.connect("changed", self._on_session_changed)
        head.pack_start(self.session_box, False, False, 0)
        self.top_btn = Gtk.ToggleButton(label="置顶")
        self.top_btn.set_tooltip_text("勾选后始终显示在其他窗口之上")
        self.top_btn.connect("toggled", self._on_top_toggled)
        head.pack_start(self.top_btn, False, False, 0)
        outer.pack_start(head, False, False, 0)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        hero.get_style_context().add_class("card")
        hero.set_margin_top(2)
        title = Gtk.Label(label="会话缓存命中率")
        title.get_style_context().add_class("section")
        title.set_halign(Gtk.Align.START)
        title.set_margin_start(12)
        title.set_margin_top(8)
        hero.pack_start(title, False, False, 0)
        self.big = Gtk.Label()
        self.big.set_halign(Gtk.Align.START)
        self.big.set_margin_start(12)
        hero.pack_start(self.big, False, False, 0)
        self.hero_sub = Gtk.Label(label="等待数据…")
        self.hero_sub.get_style_context().add_class("mono")
        self.hero_sub.set_halign(Gtk.Align.START)
        self.hero_sub.set_ellipsize(Pango.EllipsizeMode.END)
        self.hero_sub.set_margin_start(12)
        self.hero_sub.set_margin_bottom(10)
        hero.pack_start(self.hero_sub, False, False, 0)
        outer.pack_start(hero, False, False, 0)

        self.ctx_area = Gtk.DrawingArea()
        self.ctx_area.set_size_request(-1, 8)
        self.ctx_area.connect("draw", self._draw_ctx)
        outer.pack_start(self.ctx_area, False, False, 0)
        self.ctx_label = Gtk.Label(label="上下文 –")
        self.ctx_label.get_style_context().add_class("mono")
        self.ctx_label.set_halign(Gtk.Align.START)
        outer.pack_start(self.ctx_label, False, False, 0)

        self.stats = Gtk.Grid()
        self.stats.set_column_spacing(6)
        self.stats.set_row_spacing(6)
        self.stats.set_column_homogeneous(True)
        self.stat_values = {}
        titles = [
            ("call", "本次调用输入"),
            ("turn", "本轮累计输入"),
            ("thread", "会话累计输入"),
            ("miss", "未缓存输入"),
            ("cache_write", "缓存写入"),
            ("calls", "调用次数"),
        ]
        for i, (key, label) in enumerate(titles):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.get_style_context().add_class("card")
            cap = Gtk.Label(label=label)
            cap.get_style_context().add_class("small")
            cap.set_halign(Gtk.Align.START)
            cap.set_margin_start(10)
            cap.set_margin_top(6)
            cap.set_ellipsize(Pango.EllipsizeMode.END)
            box.pack_start(cap, False, False, 0)
            val = Gtk.Label(label="–")
            val.set_halign(Gtk.Align.START)
            val.set_margin_start(10)
            val.set_margin_bottom(6)
            box.pack_start(val, False, False, 0)
            self.stat_values[key] = val
            self.stats.attach(box, i % 2, i // 2, 1, 1)
        outer.pack_start(self.stats, False, False, 0)

        spark_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cap = Gtk.Label(label="最近调用命中率")
        cap.get_style_context().add_class("section")
        cap.set_halign(Gtk.Align.START)
        spark_head.pack_start(cap, True, True, 0)
        self.spark_note = Gtk.Label(label="")
        self.spark_note.get_style_context().add_class("tiny")
        spark_head.pack_end(self.spark_note, False, False, 0)
        outer.pack_start(spark_head, False, False, 0)
        self.spark = Gtk.DrawingArea()
        self.spark.set_size_request(-1, 56)
        self.spark.connect("draw", self._draw_spark)
        spark_frame = Gtk.Box()
        spark_frame.get_style_context().add_class("card")
        spark_frame.pack_start(self.spark, True, True, 0)
        outer.pack_start(spark_frame, False, False, 0)

        calls_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cap = Gtk.Label(label="最近调用")
        cap.get_style_context().add_class("section")
        cap.set_halign(Gtk.Align.START)
        calls_head.pack_start(cap, True, True, 0)
        self.calls_note = Gtk.Label(label="")
        self.calls_note.get_style_context().add_class("tiny")
        calls_head.pack_end(self.calls_note, False, False, 0)
        outer.pack_start(calls_head, False, False, 0)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_size_request(-1, 160)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.scroll.add(self.listbox)
        outer.pack_start(self.scroll, True, True, 0)

    # ---------------------------------------------------------------- 几何
    def _monitors(self):
        display = Gdk.Display.get_default()
        mons = []
        for i in range(display.get_n_monitors()):
            g = display.get_monitor(i).get_geometry()
            mons.append((g.x, g.y, g.width, g.height))
        return mons

    def _restore_geometry(self):
        try:
            with open(STATE_FILE, encoding="utf-8") as fh:
                w, h, x, y = (int(v) for v in fh.read().split()[:4])
        except Exception:
            self.set_default_size(DEFAULT_W, DEFAULT_H)
            return
        self.set_default_size(max(MIN_W, w), max(MIN_H, h))
        for (mx, my, mw, mh) in self._monitors():
            if mx <= x < mx + mw and my <= y < my + mh:
                self.move(x, y)
                return

    def _on_configure(self, _widget, _event):
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(1200, self._save_geometry)
        return False

    def _save_geometry(self):
        self._save_source = None
        try:
            x, y = self.get_position()
            w, h = self.get_size()
            with open(STATE_FILE, "w", encoding="utf-8") as fh:
                fh.write("%d %d %d %d\n" % (w, h, x, y))
        except Exception:
            pass
        return False

    # ---------------------------------------------------------------- 数据
    def _fetch(self):
        url = API
        if self.session:
            url += "?session=" + urllib.parse.quote(self.session)
        req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _tick(self):
        if not self.paused:
            try:
                self._render(self._fetch())
            except Exception:
                self._show_error()
        return True

    def _show_error(self):
        self.dot.set_markup(colorize("●", RED, size="large"))
        self.status.set_text("看板服务未连接")
        self.hero_sub.set_text("请确认 codex-usage 伴随脚本在运行")

    # ---------------------------------------------------------------- 绘制
    def _draw_ctx(self, widget, cr):
        alloc = widget.get_allocation()
        pct = 0.0
        if self._data:
            p = self._data["usage"].get("context_percent")
            pct = 0.0 if p is None else max(0.0, min(100.0, p))
        Gdk.cairo_set_source_rgba(cr, Gdk.RGBA(0.10, 0.12, 0.15, 1))
        cr.rectangle(0, 0, alloc.width, alloc.height)
        cr.fill()
        if pct > 0:
            color = RED if pct >= 85 else AMBER if pct >= 60 else ACCENT
            rgba = Gdk.RGBA()
            rgba.parse(color)
            Gdk.cairo_set_source_rgba(cr, rgba)
            cr.rectangle(0, 0, alloc.width * pct / 100.0, alloc.height)
            cr.fill()
        return False

    def _draw_spark(self, widget, cr):
        alloc = widget.get_allocation()
        data = (self._data or {}).get("hit_history") or []
        data = data[-40:]
        if not data:
            return False
        h = alloc.height
        Gdk.cairo_set_source_rgba(cr, Gdk.RGBA(0.16, 0.19, 0.23, 1))
        cr.set_dash([3, 3], 0)
        cr.move_to(0, 9)
        cr.line_to(alloc.width, 9)
        cr.stroke()
        cr.set_dash([], 0)
        step = alloc.width / float(len(data))
        bw = max(1.0, step - 2)
        for i, v in enumerate(data):
            v = max(0.0, min(100.0, v))
            bh = max(1.0, v / 100.0 * (h - 6))
            rgba = Gdk.RGBA()
            rgba.parse(hit_color(v))
            Gdk.cairo_set_source_rgba(cr, rgba)
            cr.rectangle(i * step, h - bh, bw, bh)
            cr.fill()
        return False

    # ---------------------------------------------------------------- 渲染
    def _render(self, data):
        self._data = data
        if data.get("error"):
            self._show_error()
            return
        u = data["usage"]
        active = u.get("last_event_at") and False
        if u.get("last_event_at"):
            try:
                ts = datetime.datetime.fromisoformat(u["last_event_at"].replace("Z", "+00:00")).timestamp() * 1000
                active = (data["now"] - ts) < 3000
            except Exception:
                active = False
        self.dot.set_markup(colorize("●", GREEN if active else "#3a4250", size="large"))
        self.status.set_text("%s · %s · %s" % ("进行中" if active else "空闲",
                                              (u.get("session_id") or "")[:8],
                                              u.get("model") or "?"))

        thread = u.get("thread")
        r = thread.get("hit_rate") if thread else None
        self.big.set_markup(colorize(hit_text(r), hit_color(r), size="xx-large", weight="bold"))
        if thread:
            self.hero_sub.set_text(
                "缓存命中 %s / 总输入 %s · 未缓存 %s" % (
                    fmt(thread["cached"]), fmt(thread["input"]),
                    fmt(max(0, thread["input"] - thread["cached"]))))
        else:
            self.hero_sub.set_text("等待第一次调用…")

        pct = u.get("context_percent")
        self.ctx_label.set_text("上下文 %s" % (
            "–" if pct is None else "%.1f%%  (%s / %s)" % (pct, fmt(u.get("context_used")),
                                                           fmt(u.get("context_window")))))
        self.ctx_area.queue_draw()

        call = u.get("last_call")
        turn = u.get("last_turn")
        self.stat_values["call"].set_text(fmt(call["input"]) if call else "–")
        self.stat_values["turn"].set_text(fmt(turn["input"]) if turn else "–")
        self.stat_values["thread"].set_text(fmt(thread["input"]) if thread else "–")
        self.stat_values["miss"].set_text(
            fmt(max(0, thread["input"] - thread["cached"])) if thread else "–")
        self.stat_values["cache_write"].set_text(fmt(thread["cacheWrite"]) if thread else "–")
        self.stat_values["calls"].set_text(str(u.get("calls") or 0))

        hist = data.get("hit_history") or []
        if hist:
            recent = hist[-40:]
            self.spark_note.set_text("近 %d 次 · 平均 %.1f%%" % (
                len(recent), sum(recent) / len(recent)))
        else:
            self.spark_note.set_text("")
        self.spark.queue_draw()

        self._render_calls(data.get("recent_calls") or [])
        self._render_sessions(data.get("sessions") or [])

    def _render_calls(self, calls):
        sig = (len(calls), calls[-1]["ts"] if calls else 0)
        if sig == self._calls_sig:
            return
        self._calls_sig = sig
        adj = self.scroll.get_vadjustment()
        keep = adj.get_value() if adj else 0
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        rows = list(reversed(calls))
        self.calls_note.set_text("共 %d 次 · 可滚动" % len(rows) if rows else "")
        if not rows:
            self.listbox.add(Gtk.Label(label="暂无调用记录"))
        for c in rows:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(2)
            box.set_margin_bottom(2)
            box.set_margin_start(8)
            box.set_margin_end(8)
            t = Gtk.Label(label=clock(c["ts"]) if c.get("ts") else "–")
            t.get_style_context().add_class("mono")
            box.pack_start(t, False, False, 0)
            i = Gtk.Label(label="输入 %s" % fmt(c["input"]))
            i.get_style_context().add_class("mono")
            box.pack_start(i, False, False, 0)
            o = Gtk.Label(label="输出 %s" % fmt(c["output"]))
            o.get_style_context().add_class("mono")
            box.pack_end(o, False, False, 0)
            h = Gtk.Label()
            h.set_markup(colorize(hit_text(c.get("hit_rate")), hit_color(c.get("hit_rate"))))
            h.get_style_context().add_class("mono")
            box.pack_end(h, False, False, 0)
            row.add(box)
            self.listbox.add(row)
        self.listbox.show_all()
        if adj:
            adj.set_value(min(keep, adj.get_upper() - adj.get_page_size()))

    def _render_sessions(self, sessions):
        ids = [s["id"] for s in sessions]
        model = self.session_box.get_model()
        if model is None or len(model) != len(ids):
            self.session_box.remove_all()
            for sid in ids:
                self.session_box.append(sid, sid[:8])
        current = self.session or (ids[0] if ids else "")
        if current:
            self.session_box.set_active_id(current)

    # ---------------------------------------------------------------- 事件
    def _on_session_changed(self, combo):
        sid = combo.get_active_id()
        if sid and sid != self.session:
            self.session = sid
            self._calls_sig = None
            self._tick()

    def _on_top_toggled(self, button):
        self.set_keep_above(button.get_active())


def main():
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        sys.stderr.write("没有可用的图形环境\n")
        return 1
    win = UsageWindow()
    win.show_all()
    if os.environ.get("CODEX_USAGE_TOPMOST") == "1":
        win.top_btn.set_active(True)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
