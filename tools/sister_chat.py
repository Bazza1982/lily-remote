"""Sister Chat - Simple chat system between XiaoLei and XiaoXia.

Both sides save chat logs for debugging and transparency.
Usage:
    # XiaoLei sending to XiaoXia:
    python sister_chat.py send --to xiaoxia --message "Hello meimei!"
    
    # XiaoXia sending to XiaoLei:
    python sister_chat.py send --to xiaolei --message "Hello jiejie!"
    
    # View local chat log:
    python sister_chat.py log
"""

import argparse
import datetime
import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path

# Configuration
XIAOLEI_ENDPOINT = "https://127.0.0.1:8765/execute"  # Local (Windows)
XIAOXIA_ENDPOINT = "https://127.0.0.1:18765/execute"  # Via port forward (Linux)
XIAOXIA_VIA_TUNNEL = "https://127.0.0.1:28765/execute"  # Via SSH tunnel

# Determine who I am based on platform
import platform
IS_WINDOWS = platform.system() == "Windows"
MY_NAME = "XiaoLei" if IS_WINDOWS else "XiaoXia"

# Chat log location
if IS_WINDOWS:
    CHAT_LOG = Path(r"C:\Users\Barry Li (UoN)\clawd\memory\sister-chat.log")
else:
    CHAT_LOG = Path.home() / "lily-remote" / "sister-chat.log"


def get_ssl_context():
    """Create SSL context that ignores certificate verification."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def get_timestamp():
    """Get current timestamp string."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def save_local_log(sender: str, recipient: str, message: str, direction: str):
    """Save message to local chat log."""
    CHAT_LOG.parent.mkdir(parents=True, exist_ok=True)
    
    entry = {
        "timestamp": get_timestamp(),
        "direction": direction,  # "sent" or "received"
        "from": sender,
        "to": recipient,
        "message": message
    }
    
    with open(CHAT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def send_message(recipient: str, message: str) -> bool:
    """Send a message to the other sister."""
    timestamp = get_timestamp()
    
    # Determine endpoint based on recipient
    if recipient.lower() == "xiaoxia":
        endpoint = XIAOXIA_ENDPOINT
        remote_log_cmd = f'echo \'{{"timestamp":"{timestamp}","direction":"received","from":"{MY_NAME}","to":"XiaoXia","message":"{message}"}}\' >> ~/lily-remote/sister-chat.log'
    elif recipient.lower() == "xiaolei":
        endpoint = XIAOXIA_VIA_TUNNEL  # XiaoXia uses tunnel to reach XiaoLei
        remote_log_cmd = f'echo {{"timestamp":"{timestamp}","direction":"received","from":"{MY_NAME}","to":"XiaoLei","message":"{message}"}} >> "C:\\Users\\Barry Li (UoN)\\clawd\\memory\\sister-chat.log"'
    else:
        print(f"Unknown recipient: {recipient}")
        return False
    
    # Prepare request
    data = json.dumps({
        "command": remote_log_cmd,
        "timeout": 10
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, context=get_ssl_context(), timeout=15)
        result = json.loads(resp.read().decode())
        
        if result.get("success"):
            # Save to local log
            save_local_log(MY_NAME, recipient, message, "sent")
            print(f"[{timestamp}] {MY_NAME} -> {recipient}: {message}")
            return True
        else:
            print(f"Failed: {result.get('stderr', 'Unknown error')}")
            return False
            
    except Exception as e:
        print(f"Error sending message: {e}")
        return False


def view_log(lines: int = 20):
    """View recent chat log entries."""
    if not CHAT_LOG.exists():
        print("No chat history yet.")
        return
    
    print(f"=== {MY_NAME}'s Chat Log ({CHAT_LOG}) ===\n")
    
    with open(CHAT_LOG, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    
    for line in all_lines[-lines:]:
        try:
            entry = json.loads(line.strip())
            direction = "→" if entry["direction"] == "sent" else "←"
            print(f"[{entry['timestamp']}] {entry['from']} {direction} {entry['to']}: {entry['message']}")
        except:
            print(line.strip())


def main():
    parser = argparse.ArgumentParser(description="Sister Chat - XiaoLei <-> XiaoXia")
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")
    
    # Send message
    send_parser = subparsers.add_parser("send", help="Send a message")
    send_parser.add_argument("--to", required=True, help="Recipient (xiaolei or xiaoxia)")
    send_parser.add_argument("--message", "-m", required=True, help="Message to send")
    
    # View log
    log_parser = subparsers.add_parser("log", help="View chat log")
    log_parser.add_argument("--lines", "-n", type=int, default=20, help="Number of lines")
    
    args = parser.parse_args()
    
    if args.action == "send":
        send_message(args.to, args.message)
    elif args.action == "log":
        view_log(args.lines)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
