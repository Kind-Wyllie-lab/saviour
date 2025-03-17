"""
Test file for a habitat module acting as a client.

Author: Andrew SG
Created: 2025-03-17
License: GPLv3
"""

import socket

# Define the server address and port
SERVER_ADDRESS = '192.168.0.2' # Laptop IP
SERVER_PORT = 5000 # Dummy port for now
BUFFER_SIZE = 1024 # Buffer size for receiving data

# Create a socket object
client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# Connect to the server 
client_socket.connect((SERVER_ADDRESS, SERVER_PORT))

# Send a message to the server
message = "Hello, server!"
client_socket.send(message.encode('utf-8')) # Encode the message as utf-8 and send it to the server
data = client_socket.recv(BUFFER_SIZE) # Receive data from the server
print(f"Received data: {data.decode('utf-8')}") # Decode data as utf-8 and print it


# Close the connection
client_socket.close()

