# RFID Field Mapper

RFID Field Mapper is a desktop application for measuring and visualizing RAIN RFID coverage. It records tag reads at manually entered coordinates and displays the results as a two-dimensional heatmap for a selected height and reader power.

The application supports a live Yanzeo SA810 reader over TCP and includes a simulator for testing without RFID hardware.

## Features

- Connect to a Yanzeo SA810 reader over a local network
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
python -m pip install PySide6 matplotlib numpy
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

1. Select **Live SA810**.
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
sa810_reader.py     SA810 TCP protocol client and tag report parser
run_heatmap.bat     Optional Windows launcher
```

## Current Limitations

- Measurement coordinates are entered manually; physical position is not tracked automatically.
- Live reader support is currently focused on the Yanzeo SA810 protocol.
- RSSI values depend on the reader firmware and are not a direct distance measurement.
- Only one application should control the reader connection at a time.
