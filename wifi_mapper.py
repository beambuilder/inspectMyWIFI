#!/usr/bin/env python
"""inspectMyWIFI - simple WiFi heatmap tool for macOS.

Measures only the network the Mac is currently connected to. The band
(2.4 / 5 GHz) is detected automatically, so one session holds both walks.

Usage:
    python wifi_mapper.py [floorplan.png]          # new survey
    python wifi_mapper.py --replot session.json    # re-create heatmap from saved data

Workflow:
    1. Connect to the 2.4 GHz network, walk the flat: at each spot click your
       position on the floor plan -> signal gets measured.
    2. Switch to the 5 GHz network, walk again, click the same spots.
    3. "Undo last" removes the last point.
    4. "Finish & heatmap" saves the heatmap (PNG) next to the session data.
"""
import json
import os
import sys
import threading
import time
from datetime import datetime

import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk

import CoreWLAN

SAMPLES_PER_POINT = 10
SAMPLE_INTERVAL = 0.3  # s -> ~3 s per point
BANDS = {1: "2.4 GHz", 2: "5 GHz", 3: "6 GHz"}  # CoreWLAN channelBand values
DBM_MIN, DBM_MAX = -90, -30
HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- WiFi access

def connection():
    """Current link: {band, channel, rssi, noise, tx_rate} or None if not connected."""
    iface = CoreWLAN.CWWiFiClient.sharedWiFiClient().interface()
    ch = iface.wlanChannel()
    rssi = int(iface.rssiValue())
    if ch is None or rssi == 0:
        return None
    return {"band": BANDS.get(int(ch.channelBand()), "?"), "channel": int(ch.channelNumber()),
            "rssi": rssi, "noise": int(iface.noiseMeasurement()),
            "tx_rate": float(iface.transmitRate())}


def measure():
    """Average the connected link over a few seconds. Fails if band changes mid-measurement."""
    samples = []
    for _ in range(SAMPLES_PER_POINT):
        c = connection()
        if c is None:
            raise RuntimeError("Not connected to WiFi")
        if samples and c["band"] != samples[0]["band"]:
            raise RuntimeError("Band changed during measurement")
        samples.append(c)
        time.sleep(SAMPLE_INTERVAL)
    first = samples[0]
    return {"band": first["band"], "channel": first["channel"],
            "rssi": round(float(np.mean([s["rssi"] for s in samples])), 1),
            "rssi_min": min(s["rssi"] for s in samples),
            "rssi_max": max(s["rssi"] for s in samples),
            "noise": round(float(np.mean([s["noise"] for s in samples])), 1),
            "tx_rate": round(float(np.mean([s["tx_rate"] for s in samples])), 1)}


def color_for(rssi):
    return "green" if rssi > -60 else "orange" if rssi > -75 else "red"


# ---------------------------------------------------------------- Heatmap

def make_heatmaps(session_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    session_path = os.path.abspath(session_path)
    with open(session_path) as f:
        data = json.load(f)
    plan = Image.open(data["floorplan"]).convert("RGB")
    W, H = plan.size

    # compute on a coarse grid, matplotlib upscales via extent
    step = max(1, int(max(W, H) / 300))
    gx, gy = np.meshgrid(np.arange(0, W, step), np.arange(0, H, step))
    diag = np.hypot(W, H)

    bands = [b for b in BANDS.values() if any(p["band"] == b for p in data["points"])]
    fig, axes = plt.subplots(1, len(bands), figsize=(8 * len(bands), 8 * H / W + 1), squeeze=False)
    axes = axes[0]
    for ax, band in zip(axes, bands):
        pts = [(p["x"], p["y"], p["rssi"]) for p in data["points"] if p["band"] == band]
        P = np.array(pts, dtype=float)
        d = np.hypot(gx[..., None] - P[:, 0], gy[..., None] - P[:, 1])  # (h, w, n)
        w = 1.0 / np.maximum(d, 1.0) ** 2                                 # inverse distance weighting
        z = (w * P[:, 2]).sum(-1) / w.sum(-1)
        # fade out far away from any measured point
        near = d.min(-1) / diag
        alpha = np.clip(1 - (near - 0.12) / 0.15, 0, 1) * 0.6
        rgba = plt.cm.RdYlGn((np.clip(z, DBM_MIN, DBM_MAX) - DBM_MIN) / (DBM_MAX - DBM_MIN))
        rgba[..., 3] = alpha

        ax.imshow(plan, extent=(0, W, H, 0))
        ax.imshow(rgba, extent=(0, W, H, 0), interpolation="bilinear")
        ax.scatter(P[:, 0], P[:, 1], c="black", s=12)
        for x, y, v in pts:
            ax.annotate(f"{v:.0f}", (x, y), xytext=(4, 4), textcoords="offset points",
                        fontsize=8, color="black",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", alpha=0.7, lw=0))
        ax.set_title(f"{band}  ({len(pts)} points)")
        ax.axis("off")
    sm = plt.cm.ScalarMappable(cmap="RdYlGn", norm=plt.Normalize(DBM_MIN, DBM_MAX))
    fig.colorbar(sm, ax=axes, shrink=0.7, label="Signal (dBm)  -  > -60 good, < -75 weak")
    path = os.path.join(os.path.dirname(session_path), "heatmap.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ---------------------------------------------------------------- Main app

class App:
    def __init__(self, root, plan_path):
        self.root = root
        self.busy = False
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.out_dir = os.path.join(HERE, "surveys", stamp)
        os.makedirs(self.out_dir, exist_ok=True)
        self.session_path = os.path.join(self.out_dir, "session.json")
        self.data = {"floorplan": os.path.abspath(plan_path), "started": stamp, "points": []}
        self.items = []  # canvas items per point, parallel to data["points"]

        img = Image.open(plan_path).convert("RGB")
        # fit plan to screen (leave room for menu bar, dock, toolbar)
        max_w, max_h = root.winfo_screenwidth() - 100, root.winfo_screenheight() - 170
        self.scale = min(max_w / img.width, max_h / img.height, 1.0)
        view = img.resize((int(img.width * self.scale), int(img.height * self.scale)))
        self.photo = ImageTk.PhotoImage(view)

        root.title("inspectMyWIFI")
        bar = tk.Frame(root)
        bar.pack(fill="x")
        tk.Button(bar, text="Undo last", command=self.undo).pack(side="left", padx=4, pady=4)
        tk.Button(bar, text="Finish & heatmap", command=self.finish).pack(side="left", padx=4)
        self.live = tk.Label(bar, width=34, anchor="w", font=("Menlo", 12, "bold"))
        self.live.pack(side="left", padx=10)
        self.status = tk.Label(bar, text="Click your current position on the plan.", anchor="w")
        self.status.pack(side="left", padx=10)

        self.canvas = tk.Canvas(root, width=view.width, height=view.height, cursor="crosshair")
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.bind("<Button-1>", self.on_click)
        tk.Label(root, anchor="w", padx=6, justify="left", wraplength=view.width, text=(
            "Circle = 2.4 GHz, square = 5 GHz. Walk once per band: "
            "switch WiFi network in the menu bar between walks.")).pack(fill="x")
        self.update_live()

    def update_live(self):
        c = connection()
        self.live.config(text="Not connected" if c is None else
                         f"Connected: {c['band']} ch {c['channel']}  {c['rssi']} dBm")
        self.root.after(1000, self.update_live)

    def on_click(self, ev):
        if self.busy:
            return
        self.busy = True
        x, y = ev.x / self.scale, ev.y / self.scale
        marker = self.canvas.create_oval(ev.x - 6, ev.y - 6, ev.x + 6, ev.y + 6,
                                         fill="gray", outline="black")
        self.status.config(text="Measuring... stand still (~3 s)")

        def work():
            try:
                res = measure()
                self.root.after(0, self.done, x, y, ev.x, ev.y, marker, res, None)
            except Exception as e:
                self.root.after(0, self.done, x, y, ev.x, ev.y, marker, None, e)

        threading.Thread(target=work, daemon=True).start()

    def done(self, x, y, vx, vy, marker, res, err):
        self.busy = False
        self.canvas.delete(marker)
        if err is not None:
            self.status.config(text=f"Measurement failed: {err}")
            return
        r = 6
        shape = self.canvas.create_oval if res["band"] == "2.4 GHz" else self.canvas.create_rectangle
        dot = shape(vx - r, vy - r, vx + r, vy + r, fill=color_for(res["rssi"]), outline="black")
        text = self.canvas.create_text(vx + 9, vy, text=f"{res['rssi']:.0f}", anchor="w",
                                       font=("Helvetica", 11, "bold"))
        self.items.append([dot, text])
        self.data["points"].append({"x": round(x, 1), "y": round(y, 1),
                                    "time": datetime.now().isoformat(timespec="seconds"), **res})
        self.save()
        counts = {b: sum(p["band"] == b for p in self.data["points"]) for b in ("2.4 GHz", "5 GHz")}
        self.status.config(text=f"{res['band']}: {res['rssi']:.0f} dBm   "
                           f"(points: 2.4 GHz {counts['2.4 GHz']}, 5 GHz {counts['5 GHz']})")

    def undo(self):
        if self.busy or not self.data["points"]:
            return
        self.data["points"].pop()
        for item in self.items.pop():
            self.canvas.delete(item)
        self.save()
        self.status.config(text=f"Removed last point ({len(self.data['points'])} left).")

    def save(self):
        with open(self.session_path, "w") as f:
            json.dump(self.data, f, indent=2)

    def finish(self):
        if self.busy:
            return
        if not self.data["points"]:
            messagebox.showinfo("inspectMyWIFI", "Measure some points first.")
            return
        self.save()
        self.status.config(text="Creating heatmap...")
        self.root.update()
        path = make_heatmaps(self.session_path)
        os.system(f'open "{path}"')
        self.status.config(text=f"Saved to {self.out_dir}")


def main():
    args = sys.argv[1:]
    if args[:1] == ["--replot"]:
        path = make_heatmaps(args[1])
        print("saved", path)
        os.system(f'open "{path}"')
        return

    root = tk.Tk()
    root.withdraw()
    plan = args[0] if args else filedialog.askopenfilename(
        title="Select floor plan image", initialdir=HERE,
        filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.tif *.tiff")])
    if not plan:
        return
    root.deiconify()
    App(root, plan)
    root.mainloop()


if __name__ == "__main__":
    main()
