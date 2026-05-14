# Meshtastic ↔ Telegram bridge

Desktop GUI or headless script that forwards Meshtastic text messages to a Telegram group and sends mesh traffic from Telegram using `/todos` (broadcast) and `/!xxxxxxxx` (direct message to an 8-hex node ID).

## Run the GUI

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python mesh_bridge_gui.py
```

Fill in **Telegram bot token**, **chat ID**, and either **TCP host** or **USB serial** device. Use **Start bridge** / **Stop bridge**.

### Saved settings

The GUI stores settings under the OS config directory (`platformdirs`), file `settings.json`. The bot token is saved **only** if you enable **Remember bot token**; otherwise it is stored as empty on disk (plain JSON is not encrypted—do not share that file).

### Linux and tkinter

If `import tkinter` fails, install the Tk bindings, for example:

```bash
sudo apt install python3-tk
```

## Run headless (CLI)

Use a JSON file (see `mesh_telegram_config.example.json`) or environment variables.

```bash
cp mesh_telegram_config.example.json mesh_telegram_config.json
# edit mesh_telegram_config.json
python mesh_to_telegram.py --config mesh_telegram_config.json
```

Or with environment variables:

| Variable | Meaning |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` or `MESH_TELEGRAM_TOKEN` | Bot token |
| `MESH_TELEGRAM_CHAT_ID` or `TELEGRAM_CHAT_ID` | Integer chat ID |
| `MESH_TELEGRAM_TRANSPORT` | `tcp` or `serial` |
| `MESH_TELEGRAM_HOST` | TCP hostname/IP (if `tcp`) |
| `MESH_TELEGRAM_SERIAL_DEV` | Serial device path (if `serial`; empty = auto-detect one port) |

## Build a standalone executable (PyInstaller)

Build **on each target OS** (or download CI artifacts). One machine cannot produce native binaries for all three platforms in one command.

```bash
pip install -r requirements.txt
pyinstaller mesh_bridge_gui.spec
```

Output: `dist/MeshTelegramBridge` (macOS/Linux) or `dist/MeshTelegramBridge.exe` (Windows).

If PyInstaller reports that **tkinter** is missing, fix your Python installation (e.g. official python.org installers include Tk; Linux needs `python3-tk`).

## GitHub Actions

The workflow `.github/workflows/build.yml` builds on Ubuntu, Windows, and macOS and uploads `dist/` as artifacts. Use Python 3.12 in CI for predictable Tk and wheel support.

## Telegram usage

- `/todos your message` — broadcast on the mesh (`^all`).
- `/!9ee852b8 your message` — DM node `!9ee852b8` (8 hex digits; `/9ee852b8` without `!` is also accepted).

Plain text without these commands is not sent to the mesh (help text is sent in Telegram instead).
