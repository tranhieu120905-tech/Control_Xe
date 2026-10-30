"""Bảng điều khiển xe Arduino. Chạy: python dieukhienxe.py [--demo]."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import ttk

BG, PANEL, INNER, BORDER = "#171b24", "#242935", "#1b202b", "#333b4b"
TEXT, MUTED, CYAN, RED, GREEN = "#edf2f8", "#929eb1", "#27d5d5", "#ff654f", "#76d994"
FONT = "Segoe UI"
GESTURES = {1: "1", 2: "2", 3: "3", 5: "5"}
COMMAND_NAMES = {"1": "ĐANG TIẾN", "2": "ĐANG RẼ TRÁI", "3": "ĐANG RẼ PHẢI", "5": "ĐANG LÙI"}
CONNECTIONS = ((0, 1, 2, 3, 4), (0, 5, 6, 7, 8), (5, 9, 10, 11, 12),
               (9, 13, 14, 15, 16), (13, 17, 18, 19, 20), (0, 17))


def count_fingers(landmarks, handedness="Right"):
    """Đếm ngón tay với ảnh đã lật gương và nhãn bàn tay MediaPipe."""
    points = getattr(landmarks, "landmark", landmarks)
    thumb = points[4].x < points[3].x if handedness == "Right" else points[4].x > points[3].x
    return int(thumb) + sum(points[tip].y < points[tip-2].y for tip in (8, 12, 16, 20))


def parse_distance(line):
    """Chỉ nhận bản tin có nhãn; không nhầm echo lệnh thành khoảng cách."""
    match = re.fullmatch(r"\s*(?:DIST(?:ANCE)?|D|Khoang cach)\s*[:=]\s*(\d+(?:\.\d+)?)\s*(?:cm)?\s*", line, re.I)
    if match:
        value = float(match.group(1))
        if 0 <= value <= 10000:
            return value
    return None


class ArduinoLink:
    """Serial chạy nền; chỉ giữ lệnh mới nhất để tránh chạy lệnh cũ."""
    def __init__(self, events, stop_command="0"):
        self.events = events
        self.stop_command = stop_command.encode("ascii")
        self.outgoing = queue.Queue(maxsize=1)
        self.speed_outgoing = queue.Queue(maxsize=1)
        self.halt = threading.Event()
        self.thread = None

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def connect(self, port, baud):
        if self.alive():
            return False
        self.halt.clear()
        self._clear_commands()
        self.thread = threading.Thread(target=self._run, args=(port, baud), daemon=True)
        self.thread.start()
        return True

    def _clear_commands(self):
        self._drain(self.outgoing)
        self._drain(self.speed_outgoing)

    @staticmethod
    def _drain(pending):
        while True:
            try:
                pending.get_nowait()
            except queue.Empty:
                return

    def send(self, command):
        self._drain(self.outgoing)
        self.outgoing.put_nowait(command.encode("ascii"))

    def send_speed(self, command):
        # Tốc độ không được thay thế lệnh dừng đang chờ gửi.
        self._drain(self.speed_outgoing)
        self.speed_outgoing.put_nowait(command.encode("ascii"))

    def disconnect(self):
        self.halt.set()
        self._clear_commands()

    def _run(self, port, baud):
        device = None
        try:
            import serial
            device = serial.Serial(port, baud, timeout=0.03, write_timeout=0.3)
            # Arduino thường reset sau khi mở COM. Chờ trước khi nhận lệnh chạy.
            if self.halt.wait(1.8):
                return
            device.write(self.stop_command)
            self.events.put(("connected", port))
            buffer = b""
            while not self.halt.is_set():
                try:
                    command = self.outgoing.get(timeout=0.02)
                except queue.Empty:
                    command = None
                if command is not None and not self.halt.is_set():
                    device.write(command)
                try:
                    speed = self.speed_outgoing.get_nowait()
                except queue.Empty:
                    speed = None
                if speed is not None and not self.halt.is_set():
                    device.write(speed)
                data = device.read(min(device.in_waiting, 1024)) if device.in_waiting else b""
                buffer = (buffer+data)[-4096:]
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    distance = parse_distance(line.decode("utf-8", errors="replace"))
                    if distance is not None:
                        self.events.put(("distance", distance))
        except ImportError:
            self.events.put(("error", "Thiếu pyserial. Cài thư viện theo README.md."))
        except Exception as exc:
            self.events.put(("error", f"Không kết nối được {port}: {exc}"))
        finally:
            if device is not None:
                try:
                    device.write(self.stop_command)
                except Exception:
                    pass
                try:
                    device.close()
                except Exception:
                    pass
            self.events.put(("disconnected", None))


class CameraWorker:
    def __init__(self, events, index, model):
        self.events, self.index, self.model = events, index, model
        self.frames = queue.Queue(maxsize=1)
        self.halt = threading.Event()
        self.thread = None

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.alive():
            return
        self.halt.clear()
        while not self.frames.empty():
            self.frames.get_nowait()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.halt.set()

    def _run(self):
        capture = detector = None
        try:
            import cv2
            from PIL import Image
            try:
                import mediapipe as mp
            except ImportError:
                mp = None
            legacy = mp is not None and hasattr(mp, "solutions")
            if legacy:
                detector = mp.solutions.hands.Hands(max_num_hands=1, min_detection_confidence=0.7,
                                                    min_tracking_confidence=0.7)
            elif mp is not None and self.model.is_file():
                from mediapipe.tasks import python as mp_python
                from mediapipe.tasks.python import vision
                options = vision.HandLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=str(self.model)),
                    running_mode=vision.RunningMode.VIDEO, num_hands=1,
                    min_hand_detection_confidence=0.7, min_tracking_confidence=0.7)
                detector = vision.HandLandmarker.create_from_options(options)
            if detector is None:
                self.events.put(("notice", "Camera sẵn sàng; AI cần MediaPipe và model. Xem README.md."))
            capture = cv2.VideoCapture(self.index)
            if not capture.isOpened():
                raise RuntimeError("Không mở được camera. Kiểm tra quyền hoặc ứng dụng đang dùng camera.")
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.events.put(("camera_started", detector is not None))
            timestamp = 0
            while not self.halt.is_set():
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError("Mất tín hiệu camera.")
                captured_at = time.monotonic()
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                fingers = points = None
                side = "Right"
                if detector is not None:
                    if legacy:
                        result = detector.process(rgb)
                        if result.multi_hand_landmarks:
                            points = result.multi_hand_landmarks[0].landmark
                            side = result.multi_handedness[0].classification[0].label
                    else:
                        timestamp = max(timestamp+1, int(time.monotonic()*1000))
                        result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp)
                        if result.hand_landmarks:
                            points = result.hand_landmarks[0]
                            side = result.handedness[0][0].category_name
                    if points:
                        fingers = count_fingers(points, side)
                        h, w = rgb.shape[:2]
                        for chain in CONNECTIONS:
                            for a, b in zip(chain, chain[1:]):
                                cv2.line(rgb, (int(points[a].x*w), int(points[a].y*h)),
                                         (int(points[b].x*w), int(points[b].y*h)), (39, 213, 213), 2)
                        for p in points:
                            cv2.circle(rgb, (int(p.x*w), int(p.y*h)), 4, (255, 200, 105), -1)
                if self.frames.full():
                    try:
                        self.frames.get_nowait()
                    except queue.Empty:
                        pass
                self.frames.put_nowait((Image.fromarray(rgb), fingers, captured_at))
                self.halt.wait(0.015)
        except ImportError:
            self.events.put(("error", "Camera cần OpenCV và Pillow. Cài theo README.md."))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            try:
                if detector is not None:
                    detector.close()
            finally:
                try:
                    if capture is not None:
                        capture.release()
                finally:
                    self.events.put(("camera_stopped", None))


class FlatButton(tk.Button):
    def __init__(self, parent, text, command=None, bg=INNER, fg=TEXT):
        super().__init__(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground=BORDER, activeforeground=TEXT,
                         relief="flat", bd=0, cursor="hand2", font=(FONT, 10, "bold"),
                         padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER)


class PercentSlider(tk.Canvas):
    def __init__(self, parent, variable, command):
        super().__init__(parent, height=26, bg=PANEL, highlightthickness=0,
                         takefocus=True, cursor="hand2")
        self.variable, self.command = variable, command
        self.bind("<Configure>", lambda event: self.draw())
        self.bind("<Button-1>", self.move)
        self.bind("<B1-Motion>", self.move)
        self.bind("<Left>", lambda event: self.set(variable.get()-1))
        self.bind("<Right>", lambda event: self.set(variable.get()+1))
        self.bind("<Home>", lambda event: self.set(0))
        self.bind("<End>", lambda event: self.set(100))

    def move(self, event):
        self.focus_set()
        self.set((event.x-8)*100/max(1, self.winfo_width()-16))

    def set(self, value):
        self.variable.set(max(0, min(100, round(value))))
        self.draw()
        self.command(str(self.variable.get()))
        return "break"

    def draw(self):
        self.delete("all")
        w = self.winfo_width()
        x = 8+(w-16)*self.variable.get()/100
        self.create_line(8, 13, w-8, 13, fill="#79889a", width=3, capstyle="round")
        self.create_line(8, 13, x, 13, fill=CYAN, width=3, capstyle="round")
        self.create_oval(x-6, 7, x+6, 19, fill=CYAN, outline=INNER, width=2)


class CarDashboard:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.events = queue.Queue()
        self.link = ArduinoLink(self.events, args.stop_command)
        self.camera = CameraWorker(self.events, args.camera, args.model)
        self.connected = self.connecting = self.camera_running = self.ai_available = False
        self.closing = False
        self.last_command = args.stop_command
        self.candidate = None
        self.candidate_since = 0.0
        self.frame_time = self.distance_time = 0.0
        self.distance = self.photo = self.manual_key = None
        self.ai = tk.BooleanVar(value=False)
        self.speed = tk.DoubleVar(value=35)
        self.port = tk.StringVar(value=args.port)
        self.note = tk.StringVar(value="Chế độ xem trước giao diện · không kết nối phần cứng." if args.demo
                                 else "Chọn cổng COM và kết nối Arduino để bắt đầu.")
        self.root.title("Hệ thống điều khiển xe - AI Arduino Control")
        self.root.geometry("1240x720")
        self.root.minsize(1080, 660)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._build()
        self._bind_keys()
        self.refresh_ports()
        self.root.after(40, self._poll)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Dark.TCombobox", fieldbackground=INNER, background=BORDER,
                        foreground=CYAN, arrowcolor=TEXT, bordercolor=BORDER, padding=5)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", INNER)],
                  foreground=[("readonly", CYAN)], selectbackground=[("readonly", INNER)])
        self.root.option_add("*TCombobox*Listbox.background", INNER)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)

    def label(self, parent, text, color=TEXT, size=10, bold=False):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                        font=(FONT, size, "bold" if bold else "normal"))

    def card(self, parent):
        return tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)

    def _build(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=26, pady=(18, 16))
        self.label(header, "Hệ thống điều khiển xe", size=23, bold=True).pack(side="left")
        self.label(header, " /  AI Arduino Control", MUTED, 18).pack(side="left", pady=(5, 0))
        self.connection_badge = self.label(header, "●  CHƯA KẾT NỐI", MUTED, 9, True)
        self.connection_badge.pack(side="right", pady=(7, 0))
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=16)
        for col, weight in enumerate((26, 27, 47)):
            body.columnconfigure(col, weight=weight, uniform="columns")
        body.rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        middle = tk.Frame(body, bg=BG)
        middle.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        right = self.card(body)
        right.grid(row=0, column=2, sticky="nsew")
        self._manual_panel(left)
        self._connection_panel(middle)
        self._camera_panel(right)
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(side="bottom", fill="x", padx=22, pady=(10, 13), before=body)
        self.label(footer, "●", CYAN, 10).pack(side="left")
        tk.Label(footer, textvariable=self.note, bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(side="left", fill="x", expand=True, padx=7)
        self.label(footer, f"ARDUINO  /  {self.args.baud} BAUD", MUTED, 8).pack(side="right")

    def _manual_panel(self, parent):
        panel = self.card(parent)
        panel.pack(fill="both", expand=True)
        self.label(panel, "MANUAL CONTROL", MUTED, 10, True).pack(pady=(15, 4))
        self.label(panel, "ĐIỀU KHIỂN THỦ CÔNG", TEXT, 10, True).pack(pady=(0, 10))
        pad = tk.Frame(panel, bg=PANEL)
        pad.pack(pady=(0, 10))
        self.direction_buttons = {}
        for symbol, command, row, col in (("▲", "1", 0, 1), ("◀", "2", 1, 0), ("▶", "3", 1, 2), ("▼", "5", 2, 1)):
            button = tk.Button(pad, text=symbol, font=(FONT, 20), bg="#f1f4f8", fg="#1c2230",
                               activebackground=CYAN, relief="flat", bd=0, width=3, height=1,
                               cursor="hand2", takefocus=False, padx=0, pady=0)
            button.grid(row=row, column=col, padx=4, pady=4, ipadx=2, ipady=3)
            button.bind("<ButtonPress-1>", lambda event, cmd=command: self.drive(cmd))
            button.bind("<ButtonRelease-1>", lambda event: self.stop())
            button.bind("<Leave>", lambda event: self.stop() if self.manual_key else None)
            self.direction_buttons[command] = button
        stop = tk.Button(pad, text="■", font=(FONT, 23), bg="#ff3e43", fg="white",
                         activebackground=RED, relief="flat", bd=0, width=3, cursor="hand2",
                         command=lambda: self.stop(disarm=True), takefocus=False, padx=0, pady=0)
        stop.grid(row=1, column=1, padx=4, pady=4, ipadx=2, ipady=3)
        self.label(panel, "Giữ nút để chạy · Thả để dừng", MUTED, 9).pack()
        self.label(panel, "ĐIỀU KHIỂN TỐC ĐỘ", CYAN, 10, True).pack(pady=(18, 6))
        self.slider = PercentSlider(panel, self.speed, self._speed_changed)
        self.slider.pack(fill="x", padx=22)
        self.speed_label = self.label(panel, "Mức tốc độ: 35%", GREEN, 14, True)
        self.speed_label.pack(pady=(8, 3))
        self.label(panel, "PWM 0–255" if self.args.speed_template else "Chưa cấu hình lệnh tốc độ", MUTED, 8).pack(pady=(0, 15))
        credits = tk.Frame(parent, bg=BG)
        credits.pack(side="bottom", fill="x", pady=(12, 2), before=panel)
        mark = tk.Canvas(credits, width=44, height=49, bg=BG, highlightthickness=0)
        mark.pack(side="left", padx=(5, 11))
        mark.create_rectangle(1, 1, 43, 48, fill="#992b32", outline="#c34b4e")
        mark.create_line(28, 8, 13, 26, 26, 24, 17, 41, fill="#ffd277", width=4)
        names = tk.Frame(credits, bg=BG)
        names.pack(side="left")
        self.label(names, "HỆ THỐNG XE THÔNG MINH", TEXT, 8, True).pack(anchor="w")
        self.label(names, "Arduino  ×  Computer Vision", MUTED, 8).pack(anchor="w", pady=3)

    def _connection_panel(self, parent):
        settings = self.card(parent)
        settings.pack(fill="x", pady=(0, 12))
        self.label(settings, "KẾT NỐI THIẾT BỊ", MUTED, 9, True).pack(anchor="w", padx=17, pady=(17, 12))
        row = tk.Frame(settings, bg=PANEL)
        row.pack(fill="x", padx=17)
        self.port_box = ttk.Combobox(row, textvariable=self.port, width=10, style="Dark.TCombobox")
        self.port_box.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="↻", command=self.refresh_ports, font=(FONT, 16), bg=PANEL,
                  fg=MUTED, bd=0, cursor="hand2", activebackground=PANEL).pack(side="right", padx=(6, 0))
        self.connect_button = FlatButton(settings, "KẾT NỐI ARDUINO", self.toggle_connection, bg=CYAN, fg=BG)
        self.connect_button.pack(fill="x", padx=17, pady=(12, 17))
        gauge_panel = self.card(parent)
        gauge_panel.pack(fill="both", expand=True)
        self.label(gauge_panel, "THÔNG SỐ VẬN HÀNH", MUTED, 9, True).pack(anchor="w", padx=17, pady=(19, 10))
        self.distance_label = self.label(gauge_panel, "Khoảng cách: -- cm", TEXT, 13)
        self.distance_label.pack(pady=6)
        self.label(gauge_panel, "Dữ liệu cảm biến từ Arduino", MUTED, 8).pack()
        self.gauge = tk.Canvas(gauge_panel, bg=PANEL, height=200, highlightthickness=0)
        self.gauge.pack(fill="both", expand=True, padx=5, pady=(5, 10))
        self.gauge.bind("<Configure>", lambda event: self._draw_gauge())

    def _camera_panel(self, panel):
        top = tk.Frame(panel, bg=PANEL)
        top.pack(fill="x", padx=22, pady=(17, 13))
        self.label(top, "CAMERA AI", TEXT, 10, True).pack(side="left")
        self.live_badge = self.label(top, "●  XEM TRƯỚC" if self.args.demo else "●  OFFLINE", MUTED, 9, True)
        self.live_badge.pack(side="right")
        self.camera_view = tk.Canvas(panel, bg=INNER, height=210, highlightthickness=1, highlightbackground=BORDER)
        self.camera_view.pack(fill="both", expand=True, padx=22)
        self.camera_view.bind("<Configure>", lambda event: self._render_camera())
        camera_tools = tk.Frame(panel, bg=PANEL)
        camera_tools.pack(fill="x", padx=22, pady=(12, 12))
        self.camera_button = FlatButton(camera_tools, "BẬT CAMERA", self.toggle_camera)
        self.camera_button.pack(side="left")
        self.ai_toggle = tk.Checkbutton(camera_tools, text="Điều khiển bằng AI", variable=self.ai,
                                        command=self.change_mode, bg=PANEL, fg=TEXT, selectcolor=INNER,
                                        activebackground=PANEL, activeforeground=CYAN,
                                        font=(FONT, 10), cursor="hand2")
        self.ai_toggle.pack(side="right")
        tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", padx=22)
        bottom = tk.Frame(panel, bg=PANEL)
        bottom.pack(fill="x", padx=22, pady=(17, 15))
        state = tk.Frame(bottom, bg=PANEL)
        state.pack(side="left")
        self.label(state, "TRẠNG THÁI XE", CYAN, 10, True).pack(anchor="w")
        self.motion_label = self.label(state, "●  ĐÃ DỪNG", TEXT, 17, True)
        self.motion_label.pack(anchor="w", pady=(5, 0))
        self.mode_label = self.label(state, "Chế độ thủ công", MUTED, 9)
        self.mode_label.pack(anchor="w", pady=(3, 0))
        self.disconnect_button = FlatButton(bottom, "NGẮT KẾT NỐI", self.disconnect, bg=RED, fg="white")
        self.disconnect_button.pack(side="right", ipady=7)
        self.label(panel, "1 ngón: tiến   ·   2: trái   ·   3: phải   ·   5: lùi", MUTED, 9).pack(pady=(0, 4))
        self.label(panel, "Nắm tay / 4 ngón / mất tay: dừng", MUTED, 8).pack(pady=(0, 15))

    def _draw_gauge(self):
        c = self.gauge
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        radius = max(30, min(w*0.37, (h-55)*0.48))
        cx, cy = w/2, h*0.47
        for i in range(21):
            angle = math.radians(225-i*13.5)
            major = i % 5 == 0
            r1, r2 = radius-(9 if major else 5), radius
            c.create_line(cx+r1*math.cos(angle), cy-r1*math.sin(angle), cx+r2*math.cos(angle),
                          cy-r2*math.sin(angle), fill=CYAN if i <= self.speed.get()/5 else BORDER, width=2)
            if major:
                r = radius+17
                c.create_text(cx+r*math.cos(angle), cy-r*math.sin(angle), text=str(i*5), fill=MUTED, font=(FONT, 8))
        box = (cx-radius+13, cy-radius+13, cx+radius-13, cy+radius-13)
        c.create_arc(*box, start=-45, extent=270, style="arc", outline=BORDER, width=3)
        if self.speed.get() > 0:
            c.create_arc(*box, start=225-self.speed.get()*2.7, extent=self.speed.get()*2.7,
                         style="arc", outline=CYAN, width=3)
        angle = math.radians(225-self.speed.get()*2.7)
        c.create_line(cx, cy, cx+(radius-23)*math.cos(angle), cy-(radius-23)*math.sin(angle), fill="#f5a548", width=3)
        c.create_oval(cx-9, cy-9, cx+9, cy+9, fill=INNER, outline="#f5a548", width=2)
        c.create_text(cx, cy+radius*0.65, text=f"{int(self.speed.get()):02d}%", fill=TEXT, font=(FONT, 22, "bold"))
        c.create_text(cx, cy+radius+29, text="MỨC TỐC ĐỘ ĐẶT", fill=MUTED, font=(FONT, 8, "bold"))

    def _render_camera(self, image=None, fingers=None):
        c = self.camera_view
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10 or h < 10:
            return
        c.delete("all")
        if image is not None:
            from PIL import Image, ImageTk
            image = image.copy()
            image.thumbnail((w-2, h-2), Image.Resampling.LANCZOS)
            self.photo = ImageTk.PhotoImage(image)
            c.create_image(w/2, h/2, image=self.photo)
        else:
            for x in range(0, w, 32):
                c.create_line(x, 0, x, h, fill="#222a36")
            for y in range(0, h, 32):
                c.create_line(0, y, w, y, fill="#222a36")
            cx, cy = w/2, h/2-18
            c.create_oval(cx-39, cy-39, cx+39, cy+39, outline=BORDER, width=2)
            c.create_oval(cx-21, cy-21, cx+21, cy+21, outline=CYAN, width=2)
            c.create_oval(cx-5, cy-5, cx+5, cy+5, fill=CYAN, outline="")
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                x, y = cx+dx*57, cy+dy*49
                c.create_line(x, y-dy*12, x, y, x-dx*12, y, fill=CYAN, width=2)
            c.create_text(cx, cy+79, text="CAMERA AI", fill=TEXT, font=(FONT, 14, "bold"))
            c.create_text(cx, cy+105, text="Bật camera để nhận diện cử chỉ bàn tay", fill=MUTED, font=(FONT, 9))
        c.create_rectangle(13, 12, 178, 43, fill=INNER, outline=BORDER)
        value = "--" if fingers is None else str(fingers)
        c.create_text(25, 27, text=f"SỐ NGÓN TAY: {value}", anchor="w", fill=CYAN, font=(FONT, 10, "bold"))

    def refresh_ports(self):
        if self.connected or self.connecting:
            return
        try:
            from serial.tools import list_ports
            ports = [port.device for port in list_ports.comports()]
        except ImportError:
            ports = []
        self.port_box["values"] = ports
        if ports and self.port.get() not in ports:
            self.port.set(ports[0])

    def toggle_connection(self):
        if self.connected or self.connecting:
            self.disconnect()
        elif self.args.demo:
            self.note.set("Chế độ xem trước giao diện · không kết nối phần cứng.")
        elif not self.port.get().strip():
            self.note.set("Hãy nhập hoặc chọn cổng COM của Arduino.")
        elif self.link.connect(self.port.get().strip(), self.args.baud):
            self.connecting = True
            self.port_box.configure(state="disabled")
            self.connect_button.configure(text="ĐANG KẾT NỐI…")
            self.note.set("Đang mở cổng COM và chờ Arduino khởi động…")
        else:
            self.note.set("Đang đóng cổng COM cũ. Vui lòng kết nối lại sau giây lát.")

    def disconnect(self):
        self.stop(disarm=True)
        self.connected = self.connecting = False
        self.link.disconnect()
        self._connection_state()
        self.note.set("Đã yêu cầu dừng xe và ngắt kết nối Arduino.")

    def _connection_state(self):
        self.connection_badge.configure(text=f"●  {self.port.get()} ĐÃ KẾT NỐI" if self.connected else "●  CHƯA KẾT NỐI",
                                        fg=GREEN if self.connected else MUTED)
        self.connect_button.configure(text="ĐÃ KẾT NỐI" if self.connected else "KẾT NỐI ARDUINO")
        self.port_box.configure(state="disabled" if self.connected else "normal")
        if not self.connected:
            self.distance = None
            self.distance_label.configure(text="Khoảng cách: -- cm")

    def drive(self, command):
        if self.ai.get():
            self.note.set("Tắt điều khiển AI để dùng nút bấm hoặc bàn phím.")
            return
        if not self.connected and not self.args.demo:
            self.note.set("Chưa kết nối Arduino. Chọn cổng COM và bấm KẾT NỐI ARDUINO.")
            return
        self.manual_key = command
        self._command(command)

    def _command(self, command):
        if command != self.last_command:
            if self.connected:
                self.link.send(command)
            self.last_command = command
        name = COMMAND_NAMES.get(command, "ĐÃ DỪNG")
        prefix = "XEM TRƯỚC · " if self.args.demo else ""
        self.motion_label.configure(text=f"●  {name}", fg=CYAN if command in COMMAND_NAMES else TEXT)
        self.mode_label.configure(text=prefix+("Chế độ AI" if self.ai.get() else "Chế độ thủ công"))
        for cmd, button in self.direction_buttons.items():
            button.configure(bg=CYAN if cmd == command else "#f1f4f8")

    def stop(self, disarm=False):
        self.manual_key = None
        if disarm:
            self.ai.set(False)
            self.candidate = None
        self._command(self.args.stop_command)

    def _speed_changed(self, value):
        self.speed_label.configure(text=f"Mức tốc độ: {int(float(value))}%")
        self._draw_gauge()
        if self.connected and self.args.speed_template:
            if hasattr(self, "speed_job"):
                self.root.after_cancel(self.speed_job)
            self.speed_job = self.root.after(120, self._send_speed)

    def _send_speed(self):
        if self.connected and self.args.speed_template:
            self.link.send_speed(self.args.speed_template.format(value=round(self.speed.get()*255/100)))

    def toggle_camera(self):
        if self.args.demo:
            self.note.set("Chạy không có --demo để sử dụng camera thật.")
        elif self.camera.alive():
            self.stop(disarm=True)
            self.camera.stop()
            self.camera_running = False
            self.camera_button.configure(text="ĐANG TẮT…")
        else:
            self.camera_button.configure(text="ĐANG MỞ…")
            self.camera.start()

    def change_mode(self):
        if self.ai.get() and not (self.connected and self.camera_running and self.ai_available):
            self.ai.set(False)
            self.note.set("Chế độ AI cần kết nối Arduino và camera nhận diện bàn tay đang hoạt động.")
        self.candidate = None
        self.stop()
        if self.ai.get():
            self.frame_time = time.monotonic()
            self.note.set("AI đang điều khiển. Nhấn Space hoặc nút đỏ để dừng và tắt AI.")

    def _gesture(self, fingers):
        command = GESTURES.get(fingers, self.args.stop_command)
        now = time.monotonic()
        if command == self.args.stop_command:
            self.candidate = None
            self._command(command)
        elif command != self.candidate:
            self.candidate, self.candidate_since = command, now
            self._command(self.args.stop_command)
        elif now-self.candidate_since >= 0.25:
            self._command(command)

    def _bind_keys(self):
        keys = {"Up": "1", "w": "1", "Left": "2", "a": "2", "Right": "3", "d": "3", "Down": "5", "s": "5"}
        def press(event):
            if isinstance(event.widget, (tk.Entry, ttk.Combobox, PercentSlider)):
                return
            command = keys.get(event.keysym)
            if command:
                self.drive(command)
                return "break"
        def release(event):
            if keys.get(event.keysym) == self.manual_key:
                self.stop()
        self.root.bind("<KeyPress>", press)
        self.root.bind("<KeyRelease>", release)
        self.root.bind("<space>", lambda event: self.stop(disarm=True))
        self.root.bind("<Escape>", lambda event: self.stop(disarm=True))
        self.root.bind("<FocusOut>", lambda event: self.root.after(80, self._check_focus))

    def _check_focus(self):
        if not self.closing and self.root.focus_displayof() is None:
            self.stop(disarm=True)

    def _poll(self):
        if self.closing:
            return
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "connected":
                if not self.connecting:
                    self.link.disconnect()
                    continue
                self.connected, self.connecting = True, False
                self.last_command = self.args.stop_command
                self._connection_state()
                self._send_speed()
                self.note.set(f"Đã kết nối {value} · {self.args.baud} baud. Giữ nút hoặc dùng W / A / S / D.")
            elif kind == "disconnected":
                self.stop(disarm=True)
                self.connected = self.connecting = False
                self._connection_state()
            elif kind == "distance" and self.connected:
                self.distance, self.distance_time = value, time.monotonic()
                self.distance_label.configure(text=f"Khoảng cách: {value:g} cm")
            elif kind in ("error", "notice"):
                self.note.set(value)
            elif kind == "camera_started":
                self.camera_running, self.ai_available = True, value
                self.frame_time = time.monotonic()
                self.camera_button.configure(text="TẮT CAMERA")
                self.live_badge.configure(text="●  LIVE", fg=RED)
            elif kind == "camera_stopped":
                self.stop(disarm=True)
                self.camera_running = self.ai_available = False
                self.camera_button.configure(text="BẬT CAMERA")
                self.live_badge.configure(text="●  OFFLINE", fg=MUTED)
                self._render_camera()
        try:
            image, fingers, captured_at = self.camera.frames.get_nowait()
        except queue.Empty:
            pass
        else:
            if self.camera_running and not self.camera.halt.is_set():
                self.frame_time = captured_at
                self._render_camera(image, fingers)
                if self.ai.get():
                    if time.monotonic()-captured_at < 0.6:
                        self._gesture(fingers)
                    else:
                        self.stop(disarm=True)
        now = time.monotonic()
        if self.ai.get() and now-self.frame_time > 0.8:
            self.stop(disarm=True)
            self.note.set("Camera phản hồi chậm. Đã gửi lệnh dừng và tắt AI.")
        if self.distance is not None and now-self.distance_time > 3:
            self.distance = None
            self.distance_label.configure(text="Khoảng cách: -- cm")
        self.root.after(40, self._poll)

    def close(self):
        if self.closing:
            return
        self.stop(disarm=True)
        self.closing = True
        self.camera.stop()
        self.link.disconnect()
        self.root.withdraw()
        deadline = time.monotonic()+2.5
        def finish():
            if (self.link.alive() or self.camera.alive()) and time.monotonic() < deadline:
                self.root.after(50, finish)
            else:
                self.root.destroy()
        finish()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Xem giao diện, không truy cập phần cứng")
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--stop-command", choices=("0", "4"), default="0")
    parser.add_argument("--speed-template", help="Mẫu lệnh PWM theo firmware, ví dụ V{value}\\n")
    parser.add_argument("--model", type=Path, default=Path(__file__).parent/"models"/"hand_landmarker.task")
    args = parser.parse_args(argv)
    if args.speed_template:
        args.speed_template = args.speed_template.replace("\\n", "\n").replace("\\r", "\r")
        try:
            for value in (0, 255):
                args.speed_template.format(value=value).encode("ascii")
            if "{value}" not in args.speed_template:
                raise ValueError("cần có {value}")
        except (KeyError, ValueError, UnicodeError, IndexError) as exc:
            parser.error(f"--speed-template không hợp lệ: {exc}")
    return args


def main():
    args = parse_args()
    root = tk.Tk()
    CarDashboard(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
