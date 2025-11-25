"""
Example of the basic communication method that will be used for controllers and modules.
This features a receiver parsing JSONs and identifying if it is the intended recipient.

@author Andrew SG
@created 25/11/25
"""
import zmq
import time

# Set up context and socket for subscriber
context = zmq.Context()

receive_port = "6001"
receive_socket = context.socket(zmq.SUB)
receive_socket.connect(f"tcp://localhost:{receive_port}")
receive_socket.setsockopt_string(zmq.SUBSCRIBE, '')

filters = ["camera_dc67", "all", "group_2"]
known_commands = ["get_config", "check_ready"]

send_port = "6002"
send_socket = context.socket(zmq.PUB)
send_socket.connect(f"tcp://localhost:{send_port}")


while True:
    message = receive_socket.recv_json()
    delay = time.time() - message.get('sent_at')
    if message.get("for") in filters:
        if message.get("command") in known_commands:
            print(f"Received: {message.get('command')} with delay {round(delay*1000, 5)}ms")
            response = {
                "msg_id": message.get("msg_id"),
                "message": "SUCCESS",
                "response_sent_at": time.time(),
                "from": "camera_dc67"
            }
            send_socket.send_json(response)
        else:
            print(f"Received unknown command: {message.get('command')}")
    