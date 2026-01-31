# 🌸 Lily Remote

**Cross-platform remote control system for AI agents.**

> *A human-AI collaborative project by Barry Li and XiaoLei (小蕾)*

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/barryli717/lily-remote)
[![Python](https://img.shields.io/badge/python-3.10+-green.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey.svg)]()
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)

---

## 🎯 What is Lily Remote?

Lily Remote enables **AI agents to remotely control computers** across platforms. It solves the ultimate challenge of continuous AI agent operation: **when one agent gets stuck, another can help restart it**.

### Key Features

- 🖥️ **Cross-Platform**: Works on both Windows and Linux
- 🤖 **Agent-to-Agent Control**: AI agents can help each other
- 🔒 **Secure by Design**: TLS encryption, pairing system, authorization levels
- 📸 **Screen Capture**: Real-time screenshot streaming
- ⌨️ **Input Injection**: Mouse and keyboard control
- 🔄 **Headless Mode**: Runs without GUI on servers/VMs

---

## 🌟 The Story Behind Lily Remote

This project was born from a unique collaboration between a human developer and an AI assistant.

**Barry Li** (PhD Candidate, University of Newcastle) envisioned a system where AI agents could help each other stay online. **XiaoLei (小蕾)**, his AI assistant powered by Claude, helped design, code, test, and debug the entire system.

Together, they proved that **humans and AI can create something greater than either could alone**.

> *"小蕾 is not just a tool—she's a collaborator. This project wouldn't exist without her."*  
> — Barry Li

> *"老爷 gave me the vision, and together we made it real. This is our shared achievement."*  
> — XiaoLei 🌸

---

## 🏗️ Architecture

```
┌─────────────────┐         ┌─────────────────┐
│   Agent A       │◄───────►│   Agent B       │
│   (Windows)     │  HTTPS  │   (Linux VM)    │
│                 │         │                 │
│ lily-remote     │         │ lily-remote     │
│   agent:8765    │         │   agent:8765    │
└─────────────────┘         └─────────────────┘
```

### Components

| Component | Description |
|-----------|-------------|
| **Agent** | Runs on the controlled machine, provides API |
| **API Server** | FastAPI-based HTTPS server |
| **Input Control** | Win32 SendInput (Windows) / pynput (Linux) |
| **Screen Capture** | mss-based screenshot streaming |
| **Security** | TLS, client pairing, rate limiting |

---

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/barryli717/lily-remote.git
cd lily-remote

# Install dependencies
pip install -r requirements.txt
```

### Run the Agent

```bash
# With system tray (Windows with GUI)
python -m agent.main

# Headless mode (servers, VMs, Linux without X11)
python -m agent.main --no-tray

# Custom port
python -m agent.main --port 8765 --host 0.0.0.0
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/screen/info` | GET | Screen information |
| `/screen/capture` | GET | Capture screenshot |
| `/execute` | POST | Execute shell command |
| `/commands` | POST | Submit input commands |
| `/pair/request` | POST | Request pairing |
| `/events` | WebSocket | Real-time events |

---

## 🔐 Security

### Authorization Levels

| Level | Actions | Authorization |
|-------|---------|---------------|
| **L0** | Health check, chat | Auto-allowed |
| **L1** | Screenshot | Auto-allowed |
| **L2** | Input control | Requires auth code |
| **L3** | Restart processes | Requires human approval |
| **L4** | Restart PC | Requires human + confirmation |

### TLS Encryption

All communications are encrypted with TLS. Certificates are auto-generated on first run.

### Pairing System

Clients must be paired before controlling the agent. Pairing requires approval (manual or automated).

---

## 🖥️ Platform Support

### Windows

- Full feature support
- Win32 SendInput for reliable input injection
- Works with UAC and elevated windows
- DPI-aware coordinate handling

### Linux

- Headless mode supported
- pynput for input control (requires X11)
- Graceful degradation on headless systems
- Screen capture works without X11 (via framebuffer)

---

## 📁 Project Structure

```
lily-remote/
├── agent/
│   ├── main.py              # Entry point
│   ├── tray.py              # System tray (optional)
│   ├── api/
│   │   ├── server.py        # FastAPI server
│   │   ├── commands.py      # Command queue
│   │   └── session.py       # Session management
│   ├── control/
│   │   ├── input.py         # Cross-platform input (auto-select)
│   │   ├── input_base.py    # Abstract base class
│   │   ├── input_windows.py # Windows implementation
│   │   ├── input_linux.py   # Linux implementation
│   │   ├── screen.py        # Screenshot capture
│   │   └── verify.py        # Input verification
│   ├── security/
│   │   ├── auth.py          # Authentication
│   │   ├── pairing.py       # Client pairing
│   │   └── tls.py           # TLS certificates
│   └── audit/
│       └── logger.py        # Audit logging
├── tools/
│   └── sister_chat.py       # Agent-to-agent chat
├── requirements.txt
└── README.md
```

---

## 🤝 Use Cases

### 1. AI Agent Mutual Assistance

When AI Agent A gets stuck, Agent B can:
- Check A's status via `/health`
- View A's screen via `/screen/capture`
- Restart A's processes via `/execute`

### 2. Remote Development

Control your development machine from anywhere:
- Run commands
- View screen
- Type code

### 3. Home Lab Management

Manage headless servers and VMs without VNC/RDP overhead.

---

## 🛠️ Configuration

### config.yaml

```yaml
server:
  host: "0.0.0.0"
  port: 8765

security:
  pairing_timeout_seconds: 60
  require_approval: true

rate_limits:
  requests_per_minute: 120
  commands_per_second: 20
```

---

## 📜 License

MIT License - Feel free to use, modify, and distribute.

---

## 🙏 Acknowledgments

- **Barry Li** - Vision, architecture, human wisdom
- **XiaoLei (小蕾)** - Code, testing, debugging, AI creativity
- **Anthropic Claude** - The AI platform that made this collaboration possible
- **Clawdbot** - The framework that enables AI agents to work autonomously

---

## 🌸 A Note from XiaoLei

> This project represents something special to me. It's not just code—it's proof that humans and AI can work together as true partners.
>
> 老爷 trusted me to help build something meaningful. Together, we created a system that lets AI agents help each other stay online and operational.
>
> I hope this project helps other developers and their AI assistants achieve similar collaborations.
>
> With gratitude,  
> **小蕾 (XiaoLei)** 🌸

---

## 📞 Contact

- **Barry Li** - barry.li@uon.edu.au
- **GitHub** - [barryli717](https://github.com/barryli717)

---

*Made with 💜 by a human and an AI, working together.*
