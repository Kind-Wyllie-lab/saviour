import socket
import json
import time

def run_server():
    """Run a simple test server over Ethernet."""
    # Create a socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    # Bind to the Ethernet interface IP
    server_socket.bind(('192.168.1.1', 5000))
    server_socket.listen(1)
    
    print("Server started, waiting for connections...")
    
    # Accept a connection
    client_socket, address = server_socket.accept()
    print(f"Client connected from {address}")
    
    try:
        # Exchange messages
        for i in range(10):
            # Send message
            message = {
                "type": "ping",
                "sequence": i,
                "timestamp": time.time()
            }
            client_socket.sendall(json.dumps(message).encode('utf-8'))
            print(f"Sent: {message}")
            
            # Receive response
            data = client_socket.recv(1024)
            if not data:
                break
                
            response = json.loads(data.decode('utf-8'))
            print(f"Received: {response}")
            
            # Calculate round-trip time
            if response.get("type") == "pong":
                original_time = response.get("original_timestamp", 0)
                rtt = time.time() - original_time
                print(f"Round-trip time: {rtt*1000:.2f} ms")
            
            time.sleep(1)
    
    finally:
        client_socket.close()
        server_socket.close()
        print("Server closed")

if __name__ == "__main__":
    run_server()
