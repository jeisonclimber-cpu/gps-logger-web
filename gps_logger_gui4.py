import tkinter as tk
import serial
import pynmea2
import datetime
import time
import math
import csv
import traceback
import json

# ★ WebSocket 追加部分（ここだけ新規）
import websockets
import asyncio

async def ws_send(data):
    try:
        async with websockets.connect("ws://coboler.mydns.jp:8765") as ws:
            await ws.send(json.dumps(data))
    except Exception as e:
        print("[WS ERROR]", e)

# =========================
#  Haversine 距離計算
# =========================
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

# =========================
#  新しいログファイル作成
# =========================
def new_log_file():
    filename = datetime.datetime.now().strftime("gps_%Y%m%d_%H%M.csv")
    f = open(filename, "w", newline="")
    writer = csv.writer(f)
    writer.writerow(["time", "lat", "lon", "speed_kmh", "distance_km",
                     "altitude", "pdop", "hdop", "vdop"])
    return f, writer, filename

# =========================
#  GPS Logger 本体
# =========================
class GPSLoggerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("GPSモニター")

        # シリアル
        self.ser = serial.Serial(port="/dev/ttyAMA0", baudrate=9600, timeout=1)

        # 状態変数
        self.start_time = datetime.datetime.utcnow()
        self.prev_lat = None
        self.prev_lon = None
        self.prev_alt = None
        self.total_distance = 0.0
        self.total_altitude_gain = 0.0
        self.speed_kmh = 0.0
        self.hdop = None
        self.sat_count = None

        # 1秒距離更新
        self.last_dist_update = time.time()

        # 10分ログ切替
        self.segment_start_time = datetime.datetime.utcnow()

        # ログファイル
        self.file_handle, self.writer, self.current_file = new_log_file()
        self.buffer = []
        self.last_write = time.time()
        self.last_flush = time.time()
        self.last_debug = time.time()

        # NMEA
        self.rmc = None
        self.gga = None
        self.gsa = None

        # GUI構築
        self.build_gui()

        # 閉じる処理
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # GPS読み取り開始
        self.root.after(200, self.read_gps)

        print("GPS Logger 完全版 起動完了")

    # =========================
    #  GUI構築
    # =========================
    def build_gui(self):
        self.frame_top = tk.Frame(self.root)
        self.frame_top.pack(pady=5)

        self.label_clock = tk.Label(self.frame_top, text="現在時刻: --:--:--", font=("Arial", 20))
        self.label_clock.pack(side="left", padx=10)

        self.time_label = tk.Label(self.frame_top, text="走行時間:0 00:00:00", font=("Arial", 20))
        self.time_label.pack(side="left", padx=10)

        self.frame_speed = tk.Frame(self.root)
        self.frame_speed.pack(pady=10)

        self.speed_meter = tk.Label(self.frame_speed, text="0.0 km/h",
                                    font=("Arial", 48, "bold"), fg="blue")
        self.speed_meter.pack()

        self.frame_info = tk.Frame(self.root)
        self.frame_info.pack(pady=10)

        self.frame_left = tk.Frame(self.frame_info)
        self.frame_left.pack(side="left", padx=30)

        self.lat_label = tk.Label(self.frame_left, text="緯度: ---", font=("Arial", 18))
        self.lon_label = tk.Label(self.frame_left, text="経度: ---", font=("Arial", 18))
        self.alt_label = tk.Label(self.frame_left, text="高度: --- m", font=("Arial", 18))
        self.label_sat = tk.Label(self.frame_left, text="衛星数: ---", font=("Arial", 18))

        for w in [self.lat_label, self.lon_label, self.alt_label, self.label_sat]:
            w.pack(anchor="w", pady=2)

        self.frame_right = tk.Frame(self.frame_info)
        self.frame_right.pack(side="left", padx=30)

        self.label_hdop = tk.Label(self.frame_right, text="HDOP: ---", font=("Arial", 18))
        self.dist_label = tk.Label(self.frame_right, text="距離: 0.00 km", font=("Arial", 18))
        self.speed_label = tk.Label(self.frame_right, text="速度: 0.0 km/h", font=("Arial", 18))
        self.gain_label = tk.Label(self.frame_right, text="積算高度: 0.0 m", font=("Arial", 18))

        for w in [self.label_hdop, self.dist_label, self.speed_label, self.gain_label]:
            w.pack(anchor="w", pady=2)

    # =========================
    #  閉じる処理
    # =========================
    def on_close(self):
        print("[INFO] 終了処理中…")

        if self.buffer:
            self.writer.writerows(self.buffer)
            self.buffer = []

        self.file_handle.flush()
        self.file_handle.close()

        print("[INFO] ログ保存完了。終了します。")
        self.root.destroy()

    # =========================
    #  GUI更新
    # =========================
    def update_gui(self, lat, lon, alt, dt):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.label_clock.config(text=f"現在時刻: {now}")

        elapsed = datetime.datetime.utcnow() - self.start_time
        self.time_label.config(text=f"走行時間: {str(elapsed).split('.')[0]}")

        self.speed_meter.config(text=f"{self.speed_kmh:.1f} km/h")

        self.lat_label.config(text=f"緯度: {lat:.6f}")
        self.lon_label.config(text=f"経度: {lon:.6f}")
        self.alt_label.config(text=f"高度: {alt:.1f} m")

        if self.sat_count is not None:
            self.label_sat.config(text=f"衛星数: {self.sat_count}")
        else:
            self.label_sat.config(text="衛星数: ---")

        if self.hdop is not None:
            self.label_hdop.config(text=f"HDOP: {self.hdop:.2f}")
        else:
            self.label_hdop.config(text="HDOP: ---")

        self.dist_label.config(text=f"距離: {self.total_distance:.2f} km")
        self.speed_label.config(text=f"速度: {self.speed_kmh:.1f} km/h")
        self.gain_label.config(text=f"積算高度: {self.total_altitude_gain:.1f} m")

    # =========================
    #  10分ログ切替
    # =========================
    def check_segment(self):
        now = datetime.datetime.utcnow()
        diff = (now - self.segment_start_time).total_seconds()

        if diff >= 600:
            print("[INFO] 10分経過 → 新しいログファイルへ切替")

            if self.buffer:
                self.writer.writerows(self.buffer)
                self.buffer = []

            self.file_handle.flush()
            self.file_handle.close()

            self.file_handle, self.writer, self.current_file = new_log_file()
            self.segment_start_time = now

    # =========================
    #  GPS読み取り（完全安定版）
    # =========================
    def read_gps(self):
        try:
            line = self.ser.readline().decode(errors="ignore").strip()
            if not line.startswith("$"):
                self.root.after(200, self.read_gps)
                return

            # --- RMC ---
            if line.startswith("$GNRMC") or line.startswith("$GPRMC"):
                try:
                    self.rmc = pynmea2.parse(line)
                except:
                    self.root.after(200, self.read_gps)
                    return

            # --- GSA ---
            if line.startswith("$GNGSA") or line.startswith("$GPGSA"):
                try:
                    self.gsa = pynmea2.parse(line)
                    self.hdop = float(self.gsa.hdop) if self.gsa.hdop else None
                except:
                    self.hdop = None

            # --- GGA ---
            if line.startswith("$GNGGA") or line.startswith("$GPGGA"):
                try:
                    self.gga = pynmea2.parse(line)
                    self.sat_count = int(self.gga.num_sats)
                except:
                    pass

            if not self.rmc or not self.gga:
                self.root.after(200, self.read_gps)
                return

            lat = self.rmc.latitude
            lon = self.rmc.longitude
            alt = self.gga.altitude if self.gga.altitude else 0.0
            dt = datetime.datetime.utcnow().strftime("%H:%M:%S")

            spd = self.rmc.spd_over_grnd
            self.speed_kmh = (spd if spd else 0) * 1.85

            if not (20 <= lat <= 50 and 120 <= lon <= 150):
                self.root.after(200, self.read_gps)
                return

            # --- 距離計算（1秒ごと） ---
            if time.time() - self.last_dist_update >= 1:
                if self.prev_lat is not None and self.prev_lon is not None:
                    dist_m = haversine(self.prev_lat, self.prev_lon, lat, lon)

                    if dist_m < 80:
                        self.total_distance += dist_m / 1000.0

                self.last_dist_update = time.time()

            # --- 高度積算 ---
            if self.prev_alt is not None and alt > self.prev_alt:
                self.total_altitude_gain += (alt - self.prev_alt)

            self.prev_lat = lat
            self.prev_lon = lon
            self.prev_alt = alt

            self.update_gui(lat, lon, alt, dt)

            self.buffer.append([
                dt, lat, lon, round(self.speed_kmh, 2),
                self.total_distance, alt,
                getattr(self.gsa, "pdop", None),
                getattr(self.gsa, "hdop", None),
                getattr(self.gsa, "vdop", None)
            ])

            if time.time() - self.last_write >= 1:
                if self.buffer:
                    self.writer.writerows(self.buffer)
                    self.buffer = []
                self.last_write = time.time()

            if time.time() - self.last_flush >= 10:
                self.file_handle.flush()
                self.last_flush = time.time()

            # =========================
            # ★ 1分に1回 WebSocket送信（追加）
            # =========================
            if not hasattr(self, "last_ws_send"):
                self.last_ws_send = time.time()

            if time.time() - self.last_ws_send >= 30:
                payload = {
                    "time": dt,
                    "lat": lat,
                    "lon": lon,
                    "alt": alt,
                    "speed": round(self.speed_kmh, 2),
                    "hdop": self.hdop,
                    "sat": self.sat_count
                }
                asyncio.run(ws_send(payload))
                print("[WS] 送信:", payload)
                self.last_ws_send = time.time()

            self.check_segment()
            self.root.after(200, self.read_gps)

        except Exception as e:
            print("=== ERROR ===")
            print(e)
            traceback.print_exc()
            print("=============")
            self.root.after(500, self.read_gps)

# =========================
#  実行
# =========================
def run():
    root = tk.Tk()
    app = GPSLoggerApp(root)
    root.mainloop()

if __name__ == "__main__":
    run()
