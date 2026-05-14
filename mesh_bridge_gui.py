#!/usr/bin/env python3
"""
Simple desktop UI for the Meshtastic <-> Telegram bridge.
"""

from __future__ import annotations

import json
import os
import queue
import tkinter as tk
from tkinter import messagebox, ttk

import meshtastic.util
import platformdirs

import mesh_to_telegram as bridge


APP_NAME = "mesh-telegram-bridge"
SETTINGS_FILENAME = "settings.json"


def settings_path() -> str:
    d = platformdirs.user_config_dir(APP_NAME, appauthor=False)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, SETTINGS_FILENAME)


def load_gui_settings() -> dict:
    path = settings_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_gui_settings(data: dict) -> None:
    path = settings_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


class BridgeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Meshtastic ↔ Telegram bridge")
        self.geometry("640x520")
        self.minsize(520, 420)

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._running = False

        self._var_transport = tk.StringVar(value="tcp")
        self._var_remember_token = tk.BooleanVar(value=False)

        frm = ttk.Frame(self, padding=8)
        frm.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        r = 0
        ttk.Label(frm, text="Telegram bot token").grid(row=r, column=0, sticky="w")
        self._entry_token = ttk.Entry(frm, width=56, show="*")
        self._entry_token.grid(row=r, column=1, columnspan=2, sticky="ew")
        r += 1

        ttk.Label(frm, text="Telegram chat ID").grid(row=r, column=0, sticky="w")
        self._entry_chat = ttk.Entry(frm, width=24)
        self._entry_chat.grid(row=r, column=1, sticky="w")
        r += 1

        ttk.Label(frm, text="Meshtastic").grid(row=r, column=0, sticky="nw")
        tf = ttk.Frame(frm)
        tf.grid(row=r, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(
            tf,
            text="TCP (Wi‑Fi / LAN)",
            variable=self._var_transport,
            value="tcp",
            command=self._toggle_transport_fields,
        ).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(
            tf,
            text="USB serial",
            variable=self._var_transport,
            value="serial",
            command=self._toggle_transport_fields,
        ).pack(side="left")
        r += 1

        ttk.Label(frm, text="TCP host / IP").grid(row=r, column=0, sticky="w")
        self._entry_host = ttk.Entry(frm, width=40)
        self._entry_host.grid(row=r, column=1, columnspan=2, sticky="ew")
        r += 1

        ttk.Label(frm, text="Serial device").grid(row=r, column=0, sticky="w")
        self._combo_serial = ttk.Combobox(frm, width=48, values=())
        self._combo_serial.grid(row=r, column=1, sticky="ew")
        btn_refresh = ttk.Button(frm, text="Refresh ports", command=self._refresh_ports)
        btn_refresh.grid(row=r, column=2, padx=(6, 0))
        r += 1

        ttk.Checkbutton(
            frm,
            text="Remember bot token in settings file (plain text on disk)",
            variable=self._var_remember_token,
        ).grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        r += 1

        bf = ttk.Frame(frm)
        bf.grid(row=r, column=0, columnspan=3, pady=(10, 0), sticky="w")
        self._btn_start = ttk.Button(bf, text="Start bridge", command=self._on_start)
        self._btn_start.pack(side="left", padx=(0, 8))
        self._btn_stop = ttk.Button(bf, text="Stop bridge", command=self._on_stop, state="disabled")
        self._btn_stop.pack(side="left", padx=(0, 8))
        r += 1

        frm.columnconfigure(1, weight=1)

        log_frame = ttk.LabelFrame(self, text="Log", padding=6)
        log_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self._log_text = tk.Text(log_frame, height=14, state="disabled", wrap="word")
        self._log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self._log_text.configure(yscrollcommand=scroll.set)

        self._load_fields_from_settings()
        self._toggle_transport_fields()
        bridge.set_bridge_log_fn(self._log_sink)
        self._drain_log_loop()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _log_sink(self, msg: str) -> None:
        self._log_queue.put(msg)

    def _drain_log_loop(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self._log_text.configure(state="normal")
                self._log_text.insert("end", line + "\n")
                self._log_text.see("end")
                self._log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(200, self._drain_log_loop)

    def _toggle_transport_fields(self) -> None:
        t = self._var_transport.get()
        if t == "tcp":
            self._entry_host.configure(state="normal")
            self._combo_serial.configure(state="disabled")
        else:
            self._entry_host.configure(state="disabled")
            self._combo_serial.configure(state="normal")

    def _refresh_ports(self) -> None:
        try:
            ports = meshtastic.util.findPorts(True)
        except Exception as e:
            messagebox.showerror("Serial ports", str(e))
            return
        self._combo_serial.configure(state="normal")
        self._combo_serial["values"] = ports
        if ports:
            self._combo_serial.set(ports[0])
        self._toggle_transport_fields()

    def _load_fields_from_settings(self) -> None:
        s = load_gui_settings()
        self._var_transport.set(s.get("transport", "tcp") or "tcp")
        self._entry_host.insert(0, s.get("host", ""))
        self._combo_serial.set(str(s.get("serial_dev", "")))
        self._entry_chat.insert(0, str(s.get("telegram_chat_id", "")))
        self._var_remember_token.set(bool(s.get("remember_token", False)))
        tok = s.get("telegram_token", "")
        if tok:
            self._entry_token.insert(0, tok)

    def _collect_settings_dict(self, include_token_from_field: bool) -> dict:
        token = self._entry_token.get().strip() if include_token_from_field else ""
        chat_raw = self._entry_chat.get().strip()
        try:
            chat_id = int(chat_raw) if chat_raw else 0
        except ValueError:
            chat_id = 0
        return {
            "transport": self._var_transport.get().strip(),
            "host": self._entry_host.get().strip(),
            "serial_dev": self._combo_serial.get().strip(),
            "telegram_chat_id": chat_id,
            "remember_token": self._var_remember_token.get(),
            "telegram_token": token if self._var_remember_token.get() else "",
        }

    def _save_settings_to_disk(self) -> None:
        d = self._collect_settings_dict(include_token_from_field=True)
        save_gui_settings(d)

    def _build_bridge_config(self) -> bridge.BridgeConfig:
        chat_raw = self._entry_chat.get().strip()
        try:
            chat_id = int(chat_raw)
        except ValueError as e:
            raise ValueError("Telegram chat ID must be an integer") from e
        return bridge.BridgeConfig(
            transport=self._var_transport.get().strip(),
            host=self._entry_host.get().strip(),
            serial_dev=self._combo_serial.get().strip(),
            telegram_token=self._entry_token.get().strip(),
            telegram_chat_id=chat_id,
        )

    def _set_running_ui(self, running: bool) -> None:
        self._running = running
        state_e = "disabled" if running else "normal"
        for w in (self._entry_token, self._entry_chat, self._entry_host):
            w.configure(state=state_e)
        if running:
            self._combo_serial.configure(state="disabled")
        else:
            self._toggle_transport_fields()
        self._btn_start.configure(state="disabled" if running else "normal")
        self._btn_stop.configure(state="normal" if running else "disabled")

    def _on_start(self) -> None:
        if self._running:
            return
        try:
            cfg = self._build_bridge_config()
            cfg.validate()
        except ValueError as e:
            messagebox.showerror("Configuration", str(e))
            return
        try:
            bridge.apply_bridge_config(cfg)
            bridge.start_bridge_workers()
        except Exception as e:
            messagebox.showerror("Bridge", str(e))
            return
        self._set_running_ui(True)
        self._save_settings_to_disk()
        self._log_sink("[GUI] Bridge started")

    def _on_stop(self) -> None:
        if not self._running:
            return
        bridge.stop_bridge()
        self._set_running_ui(False)
        self._log_sink("[GUI] Bridge stopped")

    def _on_close(self) -> None:
        if self._running:
            bridge.stop_bridge()
            self._running = False
        bridge.set_bridge_log_fn(None)
        self.destroy()


def main() -> None:
    app = BridgeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
