# inspectMyWIFI

A small macOS tool to check WiFi coverage in your home. You walk through your
flat or house with a MacBook, click your position on a floor plan, and the tool
measures the signal strength there. At the end it draws a heatmap of the WiFi
signal over the floor plan.

Built to check where a new router reaches well and where it does not.

## How it works

- Measures the network your Mac is currently connected to (via Apple's CoreWLAN).
- The band (2.4 GHz / 5 GHz / 6 GHz) is detected automatically.
- Each point takes about 3 seconds: 10 RSSI samples are averaged. Min/max,
  noise and transmit rate are stored as well.
- The heatmap uses inverse distance weighting between your measured points,
  one panel per band. Scale: -90 dBm (red) to -30 dBm (green).
  Rule of thumb: better than -60 dBm is good, below -75 dBm is weak.

## Requirements

- macOS (uses CoreWLAN, so it does not run on Windows or Linux)
- Python 3 with Tkinter (e.g. Anaconda or the python.org installer)
- A floor plan image of your home (PNG or JPG). A photo or a quick sketch works.

Install the dependencies:

```
pip install -r requirements.txt
```

## Usage

Start a new survey:

```
python wifi_mapper.py path/to/floorplan.png
```

If no image is given, a file dialog opens.

1. Connect your Mac to the WiFi network you want to measure.
2. Walk to a spot, click your position on the floor plan and stand still for
   about 3 seconds. A marker appears with the measured value
   (circle = 2.4 GHz, square = 5 GHz; green / orange / red by strength).
3. Repeat for as many spots as you like. "Undo last" removes the last point.
4. If your router has separate networks per band, switch to the other network
   in the menu bar and walk the same spots again. Both walks go into the same
   session.
5. Click "Finish & heatmap". The heatmap is saved and opened.

Every session is saved to `surveys/<date-time>/` (`session.json` after every
point, plus `heatmap.png`). To redraw a heatmap from saved data:

```
python wifi_mapper.py --replot surveys/<date-time>/session.json
```

## Limitations

- Only the connected network is measured, not neighbouring networks. Scanning
  all networks is unreliable on macOS: without Location Services permission the
  network names are hidden, and scans are throttled to roughly one every 12-15
  seconds.
- Signal strength only. No speed or throughput tests.

## License

MIT
