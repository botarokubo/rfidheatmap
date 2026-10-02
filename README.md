# RFID Field Mapper

RFID Field Mapper is a desktop application for measuring and visualizing RAIN RFID coverage. It records tag reads at manually entered coordinates and displays the results as a two-dimensional heatmap for a selected height and reader power.

The application supports a live Yanzeo SA810 reader over LAN or USB HID and includes a simulator for testing without RFID hardware.

This repository also includes a separate **RFID Walk-Through Tester** for comparing two readers while one or more people carrying tags walk through a detection area.

## Features

- Connect to a Yanzeo SA810 reader over LAN or USB HID
- Discover EPC tags and select a reference tag
- Listen for Yanzeo Pairing- or CRC-encrypted tags using reader-side configuration
- Configure reader transmit power
- Record measurements at manual X, Y, and Z coordinates
- Store individual reads in a local SQLite database
- Visualize RSSI average, read rate, read count, and RSSI stability
- Mark points where the selected tag was not detected
- Filter the heatmap by height and transmit power
- Export raw reads to CSV
- Export the heatmap and grid data as a PNG report
- Reset all recorded grid data from the interface
- Run in simulator mode without a physical reader

## Requirements

- Python 3.10 or newer
- Windows, Linux, or macOS with desktop GUI support
- A Yanzeo SA810 reader for live measurements
- A network connection between the computer and reader

Required Python packages:

- PySide6
- Matplotlib
- NumPy
- hidapi

## Installation

Clone or download the repository, then open a terminal in the project directory.

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Activate it on Linux or macOS:

```bash
source .venv/bin/activate
```

Install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Start the application:

```bash
python heatmap.py
```

## Reader Configuration

Before connecting, configure the reader with its vendor utility:

1. Set the reader to **TCP Server** mode.
2. Assign an IP address that is reachable from the computer running the application.
3. Set or confirm the TCP port. Some SA810 configurations use port `49152`, but the value must match the reader's settings.
4. Close the vendor utility or any other program that may already hold the reader connection.

In RFID Field Mapper:

1. Select **Live SA810 (LAN)**.
2. Enter the reader IP address and TCP port.
3. Select the desired transmit power.
4. Click **Connect**.
5. Click **Apply power to reader** if the power needs to be changed.

### Listening for encrypted tags

The **Encrypted tag listener** section configures the SA810 to inventory tags that were previously encrypted with the Yanzeo demo or another compatible tool.

1. Stop any active tag listener or measurement.
2. Select **Pairing** for a password from `0` to `255`, or **CRC** for a password from `0` to `65535`.
3. Enter the same decimal password shown in the Yanzeo demo. The demo displays CRC passwords as five digits, such as `00000`.
4. Click **Apply listen configuration**.
5. Start the tag listener or measurement normally.

Select **None** and apply the configuration to return the reader to normal, unencrypted-tag inventory.

This application only changes the reader's listening configuration. It does not contain or send the Yanzeo command that encrypts or modifies a physical tag. The password must match the tag's existing encryption configuration.

If the connection fails, confirm that the reader is reachable, the IP address and port are correct, and the local firewall allows the connection.

### USB HID connection

The Yanzeo SA810/QB0A USB command interface is supported on USB interface `MI_00` (`VID 04D8`, `PID 033F`). The keyboard-emulation interface is not used.

1. Connect the reader to the computer by USB.
2. Close the Yanzeo demo so it cannot send competing commands to the reader.
3. Select **Live SA810 (USB)**.
4. Click **Connect**.
5. Use the tag listener and measurements in the same way as the LAN connection.

If USB support is missing, install it with `python -m pip install hidapi`. Only one program should control the reader at a time.

## Measurement Workflow

1. Connect to the reader, or select **Simulator** for a hardware-free test.
2. Click **Start listening** to discover tags.
3. Select the required EPC and click **Use selected tag as reference**.
4. Enter the X, Y, and Z position of the tag or measurement point.
5. Choose a measurement duration.
6. Click **Measure and save point**.
7. Move to the next coordinate and repeat.
8. Select a metric, Z level, and power value to update the heatmap.

When a reference EPC is configured but is not detected during a measurement, the point is still saved. Its read count and read rate are zero, and the heatmap displays the cell as **No read** instead of treating the result as an application error.

## Metrics

| Metric | Meaning |
| --- | --- |
| RSSI average | Mean received signal strength for the selected tag, in dBm. Values closer to zero indicate a stronger received signal. |
| RSSI range | Minimum and maximum RSSI recorded during the measurement. |
| Read count | Number of times the selected tag was reported during the measurement. |
| Read rate | Read count divided by the measurement duration, in reads per second. |
| RSSI stability | Standard deviation of the RSSI samples, in dB. A smaller value indicates a more consistent signal. |

RSSI is reported by the reader and should be treated as a relative coverage measurement unless the device has been independently calibrated. Results can vary with antenna orientation, tag orientation, reflections, reader power, nearby materials, and radio interference.

## Heatmap and Grid Data

Each heatmap cell represents one saved X/Y coordinate at the selected Z level and transmit power. The value printed inside the cell corresponds to the selected metric.

Use **Export heatmap and grid data** to save a PNG report containing:

- The current heatmap
- Coordinates for every visible grid point
- Average, minimum, maximum, and standard deviation of RSSI
- Read count
- Read rate
- Explicit no-read results

Use **Export all reads to CSV** when individual raw reader reports are required for further analysis.

## Data Storage and Reset

Measurements are stored locally in `rfid_measurements.sqlite3`. The database is created automatically in the project directory.

The reset control permanently clears the stored measurement grid and returns the heatmap to its empty, no-data state. Export any required results before resetting.

Generated databases, CSV files, and PNG reports may contain test data and normally should not be committed to a public repository.

## SA810 Protocol Support

The live reader integration uses the SA810 IR protocol over TCP. The current implementation supports:

- Inventory start and stop
- UII/EPC tag reports
- RSSI extraction
- Transmit-power configuration
- Reader-side encrypted-tag listener configuration (None, Pairing, and CRC)

The application stores unavailable frequency and phase fields as zero because they are not present in the reader messages currently handled by the integration. Firmware or protocol variants may require adjustments in `sa810_reader.py`.

## Project Structure

```text
heatmap.py          Main interface, storage, analysis, and export logic
sa810_reader.py     SA810 LAN/USB protocol client and tag report parser
run_heatmap.bat     Optional Windows launcher
walkthrough.py      Two-reader walk-through detection tester
run_walkthrough.bat Optional Windows launcher for the walk-through tester
```

## Current Limitations

- Measurement coordinates are entered manually; physical position is not tracked automatically.
- Live reader support is currently focused on the Yanzeo SA810 protocol.
- RSSI values depend on the reader firmware and are not a direct distance measurement.
- Only one application should control the reader connection at a time.

## Two-Reader Walk-Through Tester

Run the second application with:

```bash
python walkthrough.py
```

On Windows, `run_walkthrough.bat` can be used instead.

The walk-through tester is intended for experiments where people carry RFID tags at positions such as the neck, chest, front pocket, back pocket, waist, wrist, or bag and walk between two readers.

### Walk-through workflow

1. Configure and connect **Reader A** and **Reader B** over LAN or USB HID.
2. Set the required transmit power and encrypted-tag listener settings.
3. Register each person using a person ID, EPC, and tag placement.
4. Enter a trial name and duration.
5. Click **Start walk-through trial**, then have the participants walk through the reader area.
6. Review detection results for each person and reader.
7. Export the accumulated trial results to CSV.

The result table reports:

- Read count from each reader
- Average and strongest RSSI from each reader
- First detection time relative to the trial start
- Detection order (`A -> B` or `B -> A`) and elapsed time between the two readers
- Whether the person was detected by Reader A, Reader B, both, or neither
- Registered tags that were completely missed

Only EPCs added in **People and tags** are recorded in walk-through trials,
displayed in results, and included in CSV exports. Other reports are discarded
by the app; this does not control the reader's physical buzzer.
Registered tags that are not detected remain in the results as **Missed**.
Stop the trial before changing participants.

The CSV uses one row per person/tag and reader so results from multiple trials can be compared easily.
