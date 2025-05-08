import socket
import json
import time

def run_client():
    """Run a simple test client over Ethernet."""
    # Create a socket
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # Connect to the server
    server_address = ('192.168.1.1', 5000)
    print(f"Connecting to {server_address}...")
    client_socket.connect(server_address)
    print("Connected to server")
    
    try:
        # Exchange messages
        while True:
            # Receive message
            data = client_socket.recv(1024)
            if not data:
                break
                
            message = json.loads(data.decode('utf-8'))
            print(f"Received: {message}")
            
            # Send response
            if message.get("type") == "ping":
                response = {
                    "type": "pong",
                    "sequence": message.get("sequence", 0),
                    "timestamp": time.time(),
                    "original_timestamp": message.get("timestamp", 0)
                }
                client_socket.sendall(json.dumps(response).encode('utf-8'))
                print(f"Sent: {response}")
    
    finally:
        client_socket.close()
        print("Client closed")

if __name__ == "__main__":
    run_client()
