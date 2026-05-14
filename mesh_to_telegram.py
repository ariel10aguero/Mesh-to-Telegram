# mesh_to_telegram.py

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Optional

import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface
import meshtastic.util
import telebot
from pubsub import pub

logging.getLogger("TeleBot").setLevel(logging.CRITICAL)

DEFAULT_CONFIG_FILENAME = "mesh_telegram_config.json"


@dataclass
class BridgeConfig:
    """Runtime configuration for Meshtastic and Telegram."""

    transport: str = "tcp"  # "tcp" | "serial"
    host: str = ""
    serial_dev: str = ""  # empty: auto-detect single port
    telegram_token: str = ""
    telegram_chat_id: int = 0

    def validate(self) -> None:
        if not (self.telegram_token or "").strip():
            raise ValueError("telegram_token is required")
        if self.telegram_chat_id == 0:
            raise ValueError("telegram_chat_id is required (non-zero)")
        t = (self.transport or "tcp").lower().strip()
        if t == "tcp":
            if not (self.host or "").strip():
                raise ValueError("host is required for TCP transport")
        elif t == "serial":
            pass
        else:
            raise ValueError("transport must be 'tcp' or 'serial'")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BridgeConfig":
        cid = d.get("telegram_chat_id", 0)
        if isinstance(cid, str):
            s = cid.strip()
            if s:
                try:
                    cid = int(s)
                except ValueError:
                    cid = 0
        return cls(
            transport=str(d.get("transport", "tcp")).lower().strip(),
            host=str(d.get("host", "")).strip(),
            serial_dev=str(d.get("serial_dev", "")).strip(),
            telegram_token=str(d.get("telegram_token", "")).strip(),
            telegram_chat_id=int(cid) if cid is not None else 0,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json_file(cls, path: str) -> "BridgeConfig":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def to_json_file(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)


class BridgeRuntime:
    """Shared bridge state (config, bot, shutdown)."""

    def __init__(self) -> None:
        self.config: Optional[BridgeConfig] = None
        self.bot: Optional[telebot.TeleBot] = None
        self.shutdown_event = threading.Event()
        self.log_fn: Callable[[str], None] = print
        self._pubsub_subscribed = False

    def reset_shutdown(self) -> None:
        self.shutdown_event = threading.Event()

    def is_shutdown(self) -> bool:
        return self.shutdown_event.is_set()


_runtime = BridgeRuntime()

iface: Optional[Any] = None
iface_lock = threading.Lock()


def set_bridge_log_fn(fn: Optional[Callable[[str], None]]) -> None:
    """GUI can replace stdout logging (e.g. queue to Tk)."""
    _runtime.log_fn = fn or print


def bridge_log(msg: str) -> None:
    try:
        _runtime.log_fn(msg)
    except Exception:
        print(msg, file=sys.stderr)


def get_runtime() -> BridgeRuntime:
    return _runtime


def mesh_connection_summary() -> str:
    cfg = _runtime.config
    if not cfg:
        return "(no config)"
    t = (cfg.transport or "tcp").lower().strip()
    if t == "serial":
        dev = cfg.serial_dev.strip() or "(auto)"
        return f"USB serial {dev}"
    return f"TCP {cfg.host}"


def _open_meshtastic_interface():
    cfg = _runtime.config
    if not cfg:
        raise ValueError("Bridge not configured")
    t = (cfg.transport or "tcp").lower().strip()
    if t == "serial":
        dev_path = cfg.serial_dev.strip() or None
        if not dev_path:
            ports = meshtastic.util.findPorts(True)
            if len(ports) == 0:
                raise Exception(
                    "No hay puerto serial Meshtastic (USB desconectado o sin driver)"
                )
            if len(ports) > 1:
                raise Exception(
                    "Varios puertos seriales; elige uno en serial_dev: "
                    + ", ".join(ports)
                )
            dev_path = ports[0]
        return meshtastic.serial_interface.SerialInterface(devPath=dev_path)
    if t == "tcp":
        return meshtastic.tcp_interface.TCPInterface(hostname=cfg.host.strip())
    raise ValueError(f"transport must be 'tcp' or 'serial', not {cfg.transport!r}")


def connect_meshtastic() -> None:
    """Conecta a Meshtastic con reintentos hasta shutdown."""
    global iface
    while not _runtime.shutdown_event.is_set():
        try:
            bridge_log(f"[INFO] Conectando a Meshtastic ({mesh_connection_summary()})...")

            with iface_lock:
                if iface is not None:
                    try:
                        iface.close()
                    except Exception:
                        pass
                    iface = None

            new_iface = _open_meshtastic_interface()

            with iface_lock:
                iface = new_iface

            bridge_log("[OK] Meshtastic conectado")
            return

        except Exception as e:
            if _runtime.shutdown_event.is_set():
                return
            bridge_log(f"[ERROR] Conexion fallida: {e}")
            bridge_log("[INFO] Reintentando en 10 segundos...")
            if _runtime.shutdown_event.wait(10):
                return


def send_mesh(text: str, dest: str, want_ack: bool = False) -> bool:
    global iface
    max_attempts = 3

    for attempt in range(max_attempts):
        if _runtime.shutdown_event.is_set():
            return False
        try:
            with iface_lock:
                if iface is None:
                    raise Exception("iface es None")
                iface.sendText(text=text, destinationId=dest, wantAck=want_ack)
            bridge_log(f"[OK] Enviado a Meshtastic: {text}")
            return True

        except Exception as e:
            bridge_log(f"[ERROR] Intento {attempt + 1}/{max_attempts} fallido: {e}")

            if attempt < max_attempts - 1 and not _runtime.shutdown_event.is_set():
                bridge_log("[INFO] Reconectando Meshtastic...")
                send_telegram(f"⚠️ Reconectando nodo... intento {attempt + 1}")
                connect_meshtastic()
                time.sleep(2)

    bridge_log(f"[ERROR] No se pudo enviar despues de {max_attempts} intentos")
    return False


def watchdog_meshtastic() -> None:
    global iface
    consecutive_fails = 0

    while not _runtime.shutdown_event.is_set():
        if _runtime.shutdown_event.wait(20):
            break
        try:
            with iface_lock:
                if iface is None:
                    raise Exception("iface es None")

                nodes = iface.nodes
                if nodes is None:
                    raise Exception("nodes es None")

            consecutive_fails = 0

        except Exception as e:
            if _runtime.shutdown_event.is_set():
                break
            consecutive_fails += 1
            bridge_log(f"[WATCHDOG] Fallo #{consecutive_fails}: {e}")

            if consecutive_fails >= 2:
                bridge_log("[WATCHDOG] Conexion perdida, reconectando...")
                try:
                    send_telegram("⚠️ Nodo Meshtastic desconectado, reconectando...")
                except Exception:
                    pass
                connect_meshtastic()
                consecutive_fails = 0
                try:
                    send_telegram("✅ Nodo Meshtastic reconectado")
                except Exception:
                    pass


def send_telegram(message: str) -> None:
    cfg = _runtime.config
    bot = _runtime.bot
    if not cfg or not bot:
        return
    try:
        bot.send_message(
            chat_id=cfg.telegram_chat_id,
            text=message,
            parse_mode="Markdown",
        )
        bridge_log("[OK] Enviado a Telegram")
    except Exception as e:
        bridge_log(f"[ERROR] send_telegram: {e}")


def handle_telegram_message(message: telebot.types.Message) -> None:
    cfg = _runtime.config
    if not cfg:
        return

    chat_id = message.chat.id
    text = message.text or ""
    firstname = message.from_user.first_name or "Alguien"
    is_bot = message.from_user.is_bot

    bridge_log(f"\n[TELEGRAM] De: {firstname} | Chat: {chat_id} | Texto: {text}")

    if is_bot:
        return
    if chat_id != cfg.telegram_chat_id:
        return
    if not text.strip():
        return

    if text.lower().startswith("/todos"):
        parts = text.split(" ", 1)
        broadcast_msg = parts[1].strip() if len(parts) > 1 else ""

        if not broadcast_msg:
            send_telegram(
                "ℹ️ *Comandos (MediumFast)*\n\n"
                "📢 Toda la red:\n"
                "`/todos Hola a todos!`\n\n"
                "🎯 Un nodo (8 hex, con o sin `!`):\n"
                "`/!9ee852b8 Hola nodo!`"
            )
            return

        if len(broadcast_msg) > 228:
            broadcast_msg = broadcast_msg[:225] + "..."

        bridge_log(f"\n[TELEGRAM -> MESH BROADCAST] {broadcast_msg}")

        ok = send_mesh(broadcast_msg, "^all", want_ack=False)
        if ok:
            send_telegram(
                "📢 *Broadcast enviado (MediumFast / toda la red)*\n"
                f"💬 {broadcast_msg}"
            )
        else:
            send_telegram("❌ No se pudo enviar el broadcast")
        return

    if text.startswith("/"):
        parts = text.split(" ", 1)
        cmd = parts[0][1:]
        msg = parts[1].strip() if len(parts) > 1 else ""
        cmd_clean = cmd.lstrip("!")

        is_node_id = bool(re.match(r"^[0-9a-fA-F]{8}$", cmd_clean))

        if is_node_id:
            dest_id = f"!{cmd_clean.lower()}"

            if not msg:
                send_telegram(
                    "⚠️ Falta el mensaje\n"
                    f"Uso: `/!{cmd_clean} tu mensaje aqui`"
                )
                return

            if len(msg) > 228:
                msg = msg[:225] + "..."

            bridge_log(f"\n[TELEGRAM -> MESH DIRECTO] Destino: {dest_id} | Msg: {msg}")

            ok = send_mesh(msg, dest_id, want_ack=True)
            if ok:
                send_telegram(
                    "🎯 *Mensaje enviado a nodo especifico*\n"
                    f"📍 Destino: `{dest_id}`\n"
                    f"💬 {msg}"
                )
            else:
                send_telegram(f"❌ No se pudo enviar a `{dest_id}`")
            return

        send_telegram(
            f"❓ Comando no reconocido: `{text}`\n\n"
            "ℹ️ *Comandos (MediumFast)*\n"
            "📢 `/todos Hola a todos!`\n"
            "🎯 `/!9ee852b8 Hola nodo!`"
        )
        return

    send_telegram(
        "⚠️ *Solo comandos al mesh*\n\n"
        "📢 Toda la red:\n"
        "`/todos tu mensaje`\n\n"
        "🎯 Un nodo:\n"
        "`/!xxxxxxxx tu mensaje`\n"
        "(8 caracteres hex del nodo, despues de un espacio)"
    )


def on_receive(packet: dict, interface: Any) -> None:
    try:
        if packet.get("decoded", {}).get("portnum") != "TEXT_MESSAGE_APP":
            return

        now = datetime.now().strftime("%H:%M:%S")
        text = packet["decoded"].get("text", "")
        from_id = packet.get("fromId", "unknown")
        to_id = packet.get("toId", "unknown")
        rssi = packet.get("rxRssi", "N/A")
        snr = packet.get("rxSnr", "N/A")

        hop_limit = packet.get("hopLimit", None)
        hop_start = packet.get("hopStart", None)
        hops_away = packet.get("hopsAway", None)

        if hop_start is not None and hop_limit is not None:
            hops_used = hop_start - hop_limit
        elif hops_away is not None:
            hops_used = hops_away
        else:
            hops_used = None

        node_name = from_id
        if interface.nodes:
            node_info = interface.nodes.get(from_id, {})
            node_name = node_info.get("user", {}).get("longName", from_id)

        if to_id == "^all":
            dest_text = "Todos"
            dest_line = f"📨 {dest_text}"
        else:
            dest_name = to_id
            if interface.nodes:
                dest_info = interface.nodes.get(to_id, {})
                dest_name = dest_info.get("user", {}).get("longName", to_id)
            dest_text = dest_name
            dest_line = f"📨 {dest_text} ({to_id})"

        if hops_used is None:
            hops_text = "⚪ Saltos: ?"
        elif hops_used == 0:
            hops_text = "📶 Directo"
        elif hops_used == 1:
            hops_text = "1️⃣ 1 salto"
        elif hops_used == 2:
            hops_text = "2️⃣ 2 saltos"
        elif hops_used == 3:
            hops_text = "3️⃣ 3 saltos"
        else:
            hops_text = f"🔁 {hops_used} saltos"

        if rssi != "N/A":
            if rssi > -70:
                signal_icon = "🟢"
            elif rssi > -90:
                signal_icon = "🟡"
            else:
                signal_icon = "🔴"
            signal_text = f"{signal_icon} RSSI: {rssi} dBm"
        else:
            signal_text = "⚪ RSSI: N/A"

        snr_text = f"📉 SNR: {snr} dB" if snr != "N/A" else "📉 SNR: N/A"

        message = (
            f"👤 {node_name} ({from_id})  →  {dest_line}  🕐 {now}\n"
            f"{hops_text}  |  {signal_text}  |  {snr_text}\n"
            f"\n"
            f"💬 *{text}*"
        )

        bridge_log(f"\n[MESH -> TELEGRAM] De: {node_name} ({from_id}) | Texto: {text}")
        send_telegram(message)

    except Exception as e:
        bridge_log(f"[ERROR] on_receive: {e}")


def on_connection(interface: Any, topic: Any = pub.AUTO_TOPIC) -> None:
    dev_path = getattr(interface, "devPath", None)
    host = getattr(interface, "hostname", None)
    if dev_path:
        node_line = f"USB {dev_path}"
    elif host:
        node_line = f"TCP {host}"
    else:
        node_line = mesh_connection_summary()
    bridge_log(f"[OK] Conectado a Meshtastic {node_line}")
    send_telegram(
        "✅ Bridge Online\n"
        f"Nodo: {node_line}\n"
        f"Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def _subscribe_pubsub() -> None:
    if _runtime._pubsub_subscribed:
        return
    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_connection, "meshtastic.connection.established")
    _runtime._pubsub_subscribed = True


def _unsubscribe_pubsub() -> None:
    if not _runtime._pubsub_subscribed:
        return
    try:
        pub.unsubscribe(on_receive, "meshtastic.receive")
    except Exception:
        pass
    try:
        pub.unsubscribe(on_connection, "meshtastic.connection.established")
    except Exception:
        pass
    _runtime._pubsub_subscribed = False


def _create_bot() -> telebot.TeleBot:
    cfg = _runtime.config
    if not cfg:
        raise ValueError("No config")
    bot = telebot.TeleBot(cfg.telegram_token, parse_mode=None)
    bot.register_message_handler(
        handle_telegram_message,
        content_types=["text"],
        func=lambda m: True,
    )
    return bot


def start_telegram_polling() -> None:
    bot = _runtime.bot
    if not bot:
        return
    while not _runtime.shutdown_event.is_set():
        try:
            bridge_log("[INFO] Iniciando Telegram polling...")
            bot.infinity_polling(
                timeout=60,
                long_polling_timeout=60,
                skip_pending=True,
                restart_on_change=False,
                logger_level=logging.CRITICAL,
            )
        except Exception as e:
            if _runtime.shutdown_event.is_set():
                break
            bridge_log(f"[ERROR] Telegram polling caido: {e}")
            bridge_log("[INFO] Reconectando Telegram en 5 segundos...")
            if _runtime.shutdown_event.wait(5):
                break


def _coordinator_after_mesh() -> None:
    connect_meshtastic()
    if _runtime.shutdown_event.is_set():
        bridge_log("[INFO] Meshtastic: detenido antes de arrancar workers")
        return
    threading.Thread(
        target=start_telegram_polling,
        daemon=True,
        name="TelegramBot",
    ).start()
    threading.Thread(
        target=watchdog_meshtastic,
        daemon=True,
        name="WatchdogMesh",
    ).start()


def apply_bridge_config(config: BridgeConfig) -> None:
    config.validate()
    _runtime.config = config


def start_bridge_workers() -> None:
    """Pubsub + Meshtastic connect thread + Telegram + watchdog (daemon threads)."""
    if not _runtime.config:
        raise ValueError("apply_bridge_config first")
    _runtime.reset_shutdown()
    _runtime.bot = _create_bot()
    _subscribe_pubsub()
    threading.Thread(
        target=_coordinator_after_mesh,
        daemon=True,
        name="MeshCoordinator",
    ).start()


def stop_bridge() -> None:
    """Signal workers to stop; stop Telegram polling; close mesh iface."""
    global iface
    _runtime.shutdown_event.set()
    try:
        if _runtime.bot:
            _runtime.bot.stop_bot()
    except Exception:
        pass
    _unsubscribe_pubsub()
    with iface_lock:
        if iface is not None:
            try:
                iface.close()
            except Exception:
                pass
            iface = None
    _runtime.bot = None


def load_config_from_env() -> Optional[BridgeConfig]:
    token = (
        os.environ.get("MESH_TELEGRAM_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or ""
    ).strip()
    if not token:
        return None
    chat_raw = os.environ.get("MESH_TELEGRAM_CHAT_ID") or os.environ.get(
        "TELEGRAM_CHAT_ID", "0"
    )
    try:
        chat_id = int(str(chat_raw).strip())
    except ValueError:
        chat_id = 0
    transport = (os.environ.get("MESH_TELEGRAM_TRANSPORT") or "tcp").lower().strip()
    host = (os.environ.get("MESH_TELEGRAM_HOST") or "").strip()
    serial_dev = (os.environ.get("MESH_TELEGRAM_SERIAL_DEV") or "").strip()
    return BridgeConfig(
        transport=transport,
        host=host,
        serial_dev=serial_dev,
        telegram_token=token,
        telegram_chat_id=chat_id,
    )


def load_config_cli(args: argparse.Namespace) -> Optional[BridgeConfig]:
    path = args.config or os.environ.get("MESH_TELEGRAM_CONFIG")
    if path and os.path.isfile(path):
        return BridgeConfig.from_json_file(path)
    cwd_default = os.path.join(os.getcwd(), DEFAULT_CONFIG_FILENAME)
    if os.path.isfile(cwd_default):
        return BridgeConfig.from_json_file(cwd_default)
    return load_config_from_env()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Meshtastic <-> Telegram bridge (headless CLI)"
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=f"JSON config file (default: try ./{DEFAULT_CONFIG_FILENAME} or env)",
    )
    args = parser.parse_args()

    cfg = load_config_cli(args)
    if not cfg:
        bridge_log(
            "No configuration found. Use one of:\n"
            f"  • JSON file: ./{DEFAULT_CONFIG_FILENAME} or --config PATH\n"
            "  • Env: MESH_TELEGRAM_CONFIG, or TELEGRAM_BOT_TOKEN + MESH_TELEGRAM_CHAT_ID + "
            "MESH_TELEGRAM_TRANSPORT + MESH_TELEGRAM_HOST (tcp) / MESH_TELEGRAM_SERIAL_DEV (serial)\n"
            "  • Or run the GUI: python mesh_bridge_gui.py"
        )
        sys.exit(1)

    try:
        cfg.validate()
    except ValueError as e:
        bridge_log(f"Invalid configuration: {e}")
        sys.exit(1)

    apply_bridge_config(cfg)

    bridge_log(f"\n{'=' * 50}")
    bridge_log("  MESHTASTIC <-> TELEGRAM BRIDGE")
    bridge_log(f"{'=' * 50}")
    bridge_log(f"  Nodo    : {mesh_connection_summary()}")
    bridge_log("  Telegram: solo `/todos` (broadcast) o `/!nodeid` (DM)")
    bridge_log(f"  Chat ID : {cfg.telegram_chat_id}")
    bridge_log(f"{'=' * 50}\n")

    start_bridge_workers()

    bridge_log("[OK] Bridge listo con auto-reconexion!")
    bridge_log("Presiona Ctrl+C para detener\n")

    try:
        while not _runtime.shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    bridge_log("\n[INFO] Deteniendo bridge...")
    try:
        send_telegram(
            "🔴 Bridge Offline\n"
            f"Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception:
        pass
    stop_bridge()
    bridge_log("[OK] Bridge detenido")


if __name__ == "__main__":
    main()
