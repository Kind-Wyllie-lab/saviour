"""
Test file for the habitat controller acting as a server.

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
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# Bind the socket to the server address and port
server_socket.bind((SERVER_ADDRESS, SERVER_PORT))

# Listen for incoming connections
server_socket.listen(5) # Queue up to 5 connections. why not 1?

conn, addr = server_socket.accept() # Accept a connection
print(f"Connected to {addr}")

while True:
    data=conn.recv(BUFFER_SIZE) # Receive data from the client
    if not data:
        break
    print(f"Received data: {data.decode('utf-8')}") # Decode data as utf-8 and print it

conn.close() # Close the connection




