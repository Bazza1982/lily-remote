# Lily Remote - Project Specification

## Overview

A remote PC control system for AI agents, allowing control over LAN via a single WSS channel.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Main PC (Lily/Clawdbot)                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Lily Remote Client                                   │  │
│  │  - Discovery (mDNS scan)                              │  │
│  │  - Pairing (handshake)                                │  │
│  │  - Session Manager                                    │  │
│  │  - Command Submitter                                  │  │
│  │  - Event Listener (WS)                                │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────┬───────────────────────────────┘
                              │
                    WSS :8765 (Single Channel, TLS)
                              │
┌─────────────────────────────┼───────────────────────────────┐
│                             ▼         Remote PC (Tray App)  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │           Lily Remote Agent                           │  │
│  │  - Pairing & Auth (TLS, Token)                        │  │
│  │  - Command Queue (atomic commands)                    │  │
│  │  - Screen Capture (mss, JPEG)                         │  │
│  │  - Input Control (Win32 SendInput)                    │  │
│  │  - Audit Log                                          │  │
│  │  - Kill Switch                                        │  │
│  │  [Tray Icon: "Lily Remote"]                           │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Tech Stack

- **Language:** Python 3.11+
- **API Framework:** FastAPI + uvicorn
- **WebSocket:** websockets / FastAPI WebSocket
- **Screen Capture:** mss
- **Input Control:** ctypes + Win32 SendInput (NOT PyAutoGUI)
- **Tray App:** pystray + PIL
- **Network Discovery:** zeroconf
- **TLS:** cryptography (self-signed certs)
- **Packaging:** PyInstaller

## API Design (Command Queue Model)

### Single Port: `wss://remote-pc:8765`

```yaml
# === Pairing & Session ===
POST /pair/request     # Request pairing → {challenge, expires}
POST /pair/confirm     # Confirm pairing → {paired, cert, token}
POST /session/start    # Start control session → {session_id}
POST /session/end      # End session

# === Command Queue ===
POST /commands         # Submit commands (batch)
  Body: {
    "session_id": "xxx",
    "commands": [
      {"id": "cmd-1", "type": "click", "x": 100, "y": 200, "button": "left"},
      {"id": "cmd-2", "type": "type", "text": "hello"},
      {"id": "cmd-3", "type": "hotkey", "keys": ["ctrl", "s"]}
    ]
  }
  Response: {"queued": ["cmd-1", "cmd-2", "cmd-3"]}

GET /commands/{id}     # Query command status
  Response: {
    "id": "cmd-1",
    "status": "succeeded",  # queued/running/succeeded/failed
    "result": {"cursor_after": [105, 198], "foreground_window": "Notepad"},
    "error": null
  }

# === WebSocket Events ===
WS /events
  → {"type": "frame", "data": "<base64 jpeg>", "timestamp": ...}
  → {"type": "command_done", "id": "cmd-1", "result": {...}}
  → {"type": "foreground_changed", "window": "Chrome"}

# === Health ===
GET /health            # Health check (no auth required)
GET /screen/info       # Screen info (resolution, DPI)
```

## Security Model

### Pairing Flow
1. Client sends `/pair/request` with public key
2. Server shows popup: "Allow Lily to connect?"
3. User clicks "Allow"
4. Client sends `/pair/confirm` with signed challenge
5. Server returns token + certificate
6. All subsequent requests use token + TLS

### Security Boundaries
- TLS 1.3 with self-signed certs (exchanged during pairing)
- Token authentication
- Tray icon shows connection status
- Kill switch for immediate disconnect
- Audit log for all commands

## Project Structure

```
lily-remote/
├── agent/                      # Remote Agent (Tray App)
│   ├── main.py                # Entry point
│   ├── tray.py                # System tray (pystray)
│   ├── security/
│   │   ├── pairing.py         # Pairing logic
│   │   ├── tls.py             # Certificate management
│   │   └── auth.py            # Token verification
│   ├── api/
│   │   ├── server.py          # FastAPI + WS server
│   │   ├── session.py         # Session management
│   │   └── commands.py        # Command queue
│   ├── control/
│   │   ├── input.py           # Win32 SendInput
│   │   ├── screen.py          # Screenshot + stream
│   │   └── verify.py          # Read-back verification
│   ├── discovery/
│   │   └── mdns.py            # Zeroconf
│   ├── audit/
│   │   └── logger.py          # Audit logging
│   ├── config.yaml            # Configuration
│   └── requirements.txt
│
├── client/                     # Client (for Lily/Clawdbot)
│   ├── discovery.py           # LAN scanning
│   ├── pairing.py             # Pairing
│   ├── session.py             # Session
│   ├── commander.py           # Command submission
│   └── viewer.py              # Frame receiver
│
└── README.md                   # With boundary disclaimer
```

## Day 1 Tasks (Current)

1. Create project structure (all directories and empty files)
2. Implement TLS certificate generation (`agent/security/tls.py`)
3. Implement pairing logic (`agent/security/pairing.py`)
4. Implement `/health` endpoint (`agent/api/server.py`)
5. Create tray app skeleton (`agent/tray.py`, `agent/main.py`)
6. Create `requirements.txt`
7. Create basic `config.yaml`

## Important Notes

- **DO NOT use PyAutoGUI** - it's unreliable for Windows services/UAC
- Use Win32 SendInput directly via ctypes
- All coordinates use screen pixels with DPI awareness
- Command execution must include read-back verification
