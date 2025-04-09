"""
Habitat System - Network communication utilities

Author: Andrew SG
Created: 2025-03-17
License: GPLv3
"""

import socket
import json
import time

# functions
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
