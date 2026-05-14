import time
import subprocess
import random
from datetime import datetime

DEST = "!9ee852b8"
HOST = "192.168.0.226"

messages = [
    "Todo piola wachin",
    "Dale que va",
    "Chequeo automático OK",
    "Nodo activo",
    "Todo funcionando",
    "Ping de estado"
]

while True:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    random_msg = random.choice(messages)

    full_message = f"{now} | {random_msg}"

    command = [
        "meshtastic",
        "--host", HOST,
        "--sendtext", full_message,
        "--dest", DEST,
        "--ack"
    ]

    try:
        subprocess.run(command, check=True)
        print(f"Sent: {full_message}")
    except subprocess.CalledProcessError as e:
        print("Error sending message:", e)

    # wait 5 minutes (300 seconds)
    time.sleep(100)
