"""
Example of the basic communication method that will be used for controllers and modules.
This features a publisher sending JSONs.

@author Andrew SG
@created 25/11/25
"""
import zmq
import random
import time
import threading

context = zmq.Context()

# Set up the send socket
send_port = "6001"
send_socket = context.socket(zmq.PUB)
send_socket.bind(f"tcp://localhost:{send_port}")

devices = ["camera_dc67", "all", "group_2", "mic_ee2x", "group_3", "camera_uy78"]
commands = ["get_config", "check_ready", "zipzap"] # Include zip zap, a junk command

# Receive port
receive_port = "6002"
receive_socket = context.socket(zmq.SUB)
receive_socket.bind(f"tcp://localhost:{receive_port}")
receive_socket.setsockopt_string(zmq.SUBSCRIBE, '')

msg_id = 0 # A unique message id
sent_messages = {}

def send_commands():
    global msg_id, commands, devices, send_socket, sent_messages
    while True:
        topic = random.choice(devices)
        command = random.choice(commands)
        messagedata = random.randrange(1,215) - 80
        msg_id += 1

        message = {
            "for": topic,
            "msg_id": msg_id,
            "sent_at": time.time(),
            "data": messagedata,
            "command": command
        }

        # print(f"{message}")
        sent_messages[str(msg_id)] = message
        send_socket.send_json(message)
        # socket.send_string(message)/
        time.sleep(1)

def receive_responses():
    global msg_id, receive_socket, sent_messages
    while True:
        message = receive_socket.recv_json()
        delay = time.time() - message.get('response_sent_at')
        this_msg_id = message.get("msg_id")
        round_trip_delay = message.get('response_sent_at') - sent_messages[str(this_msg_id)]["sent_at"]
        print(f"Received message {message.get('message')} from {message.get('from')} with delay {round(delay*1000,3)}ms, round trip delay {round(round_trip_delay*1000,3)}ms and ID {this_msg_id}")


send_thread = threading.Thread(target=send_commands).start()
receive_thread = threading.Thread(target=receive_responses).start()