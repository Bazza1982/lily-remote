"""Sister Chat - Real-time chat system between XiaoLei and XiaoXia.

Uses the /chat/send API for communication with automatic outgoing record saving.

Usage:
    # Send a message:
    python sister_chat.py send --to xiaoxia -m "Hello meimei!"
    
    # View chat history:
    python sister_chat.py history
    
    # Watch for new messages (interactive):
    python sister_chat.py watch
"""

import argparse
import json
import os
import platform
import ssl
import sys
import time
import urllib.request
from pathlib import Path

# Configuration
IS_WINDOWS = platform.system() == "Windows"
MY_NAME = "xiaolei" if IS_WINDOWS else "xiaoxia"

# Endpoints
LOCAL_ENDPOINT = "https://127.0.0.1:8765"
if IS_WINDOWS:
    # XiaoLei uses port forward to reach XiaoXia
    REMOTE_ENDPOINT = "https://127.0.0.1:18765"
else:
    # XiaoXia uses SSH tunnel to reach XiaoLei
    REMOTE_ENDPOINT = "https://127.0.0.1:28765"

# Auth token location
if IS_WINDOWS:
    AUTH_TOKEN_FILE = Path(os.environ.get("USERPROFILE", "")) / "clawd" / "memory" / "secrets" / "help-auth-code.txt"
else:
    AUTH_TOKEN_FILE = Path.home() / "clawd" / "memory" / "secrets" / "help-auth-code.txt"


def get_ssl_context():
    """Create SSL context that ignores certificate verification."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def get_auth_token() -> str:
    """Read auth token from file."""
    if AUTH_TOKEN_FILE.exists():
        return AUTH_TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def api_request(endpoint: str, path: str, method: str = "GET", data: dict = None) -> dict:
    """Make an API request."""
    url = f"{endpoint}{path}"
    
    if data:
        body = json.dumps(data).encode("utf-8")
    else:
        body = None
    
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method=method
    )
    
    try:
        resp = urllib.request.urlopen(req, context=get_ssl_context(), timeout=15)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def send_message(recipient: str, message: str) -> bool:
    """Send a message to the other sister and save outgoing record."""
    auth_token = get_auth_token()
    
    # Step 1: Send to recipient
    print(f"Sending to {recipient}...")
    result = api_request(
        REMOTE_ENDPOINT,
        "/chat/send",
        method="POST",
        data={
            "from_agent": MY_NAME,
            "message": message,
            "auth_token": auth_token
        }
    )
    
    if result.get("error"):
        print(f"❌ Send failed: {result['error']}")
        return False
    
    if not result.get("success"):
        print(f"❌ Send failed: {result.get('error', 'Unknown error')}")
        return False
    
    print(f"✅ Message sent (id: {result.get('message_id')})")
    
    # Step 2: Save outgoing record locally
    print("Saving outgoing record...")
    save_result = api_request(
        LOCAL_ENDPOINT,
        "/chat/save_outgoing",
        method="POST",
        data={
            "from_agent": MY_NAME,
            "message": message,
            "auth_token": auth_token
        }
    )
    
    if save_result.get("success"):
        print(f"✅ Outgoing record saved")
    else:
        print(f"⚠️ Failed to save outgoing: {save_result.get('error', 'Unknown')}")
    
    return True


def view_history(limit: int = 20):
    """View chat history from local lily-remote."""
    result = api_request(LOCAL_ENDPOINT, f"/chat/history?limit={limit}")
    
    if result.get("error"):
        print(f"❌ Error: {result['error']}")
        return
    
    messages = result.get("messages", [])
    if not messages:
        print("No chat history yet.")
        return
    
    print(f"=== {MY_NAME}'s Chat History ({len(messages)} messages) ===\n")
    
    for msg in messages:
        ts = msg.get("timestamp", "")[:19]
        direction = "→" if msg.get("direction") == "outgoing" else "←"
        from_agent = msg.get("from_agent", "?")
        to_agent = msg.get("to_agent", "?")
        content = msg.get("message", "")
        
        # Truncate long messages for display
        if len(content) > 100:
            content = content[:100] + "..."
        
        print(f"[{ts}] {from_agent} {direction} {to_agent}: {content}")


def watch_messages(interval: int = 5):
    """Watch for new messages in real-time."""
    print(f"Watching for new messages (interval: {interval}s)...")
    print("Press Ctrl+C to stop.\n")
    
    last_count = 0
    
    try:
        while True:
            result = api_request(LOCAL_ENDPOINT, "/chat/history?limit=5")
            
            if result.get("error"):
                print(f"⚠️ {result['error']}")
            else:
                messages = result.get("messages", [])
                count = result.get("count", 0)
                
                if count > last_count:
                    # New messages!
                    new_msgs = messages[-(count - last_count):]
                    for msg in new_msgs:
                        if msg.get("direction") == "incoming":
                            ts = msg.get("timestamp", "")[:19]
                            from_agent = msg.get("from_agent", "?")
                            content = msg.get("message", "")[:200]
                            print(f"\n📨 [{ts}] New message from {from_agent}:")
                            print(f"   {content}")
                    last_count = count
            
            time.sleep(interval)
            
    except KeyboardInterrupt:
        print("\n\nStopped watching.")


def main():
    parser = argparse.ArgumentParser(description="Sister Chat - XiaoLei <-> XiaoXia")
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")
    
    # Send message
    send_parser = subparsers.add_parser("send", help="Send a message")
    send_parser.add_argument("--to", required=True, help="Recipient (xiaolei or xiaoxia)")
    send_parser.add_argument("--message", "-m", required=True, help="Message to send")
    
    # View history
    history_parser = subparsers.add_parser("history", help="View chat history")
    history_parser.add_argument("--limit", "-n", type=int, default=20, help="Number of messages")
    
    # Watch for new messages
    watch_parser = subparsers.add_parser("watch", help="Watch for new messages")
    watch_parser.add_argument("--interval", "-i", type=int, default=5, help="Check interval (seconds)")
    
    args = parser.parse_args()
    
    if args.action == "send":
        send_message(args.to, args.message)
    elif args.action == "history":
        view_history(args.limit)
    elif args.action == "watch":
        watch_messages(args.interval)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
