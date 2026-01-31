import sys
sys.path.insert(0, '.')

import uvicorn
from agent.api.server import create_app
from agent.security.pairing import PairingManager
from agent.security.tls import load_or_generate_cert
import socket

def get_local_ip():
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

if __name__ == '__main__':
    # Get local IP for display
    local_ip = get_local_ip()
    
    # Generate or load TLS certificate
    cert_path, key_path = load_or_generate_cert(hostname=local_ip)
    
    print(f"\nLily Remote Agent starting...")
    print(f"Local IP: {local_ip}")
    print(f"TLS Certificate: {cert_path}")
    print()
    
    pm = PairingManager(lan_mode=True)
    app = create_app(pm)
    
    # Run on 0.0.0.0 to accept connections from any interface
    uvicorn.run(
        app, 
        host='0.0.0.0', 
        port=8765, 
        log_level='info',
        ssl_keyfile=str(key_path),
        ssl_certfile=str(cert_path),
    )
