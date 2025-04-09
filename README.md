# Modular Synchronised Data Capture System
Coding the SIDB Modular Synchronised Data Capture System.

## What is the system?
The system is intended to provide a modular and highly usable approach to I/O tasks within the Kind lab and beyond.

### Background
The habitat project seeks to explore the behaviour and development of up to 50 rodents in a single large enclosure (“the habitat”). The research is being conducted by SIDB/UofE/Kind Lab with the intention of exploring the genetic factors relating to autism in rodents. To achieve this, experimental data must be gathered. Numerous sensors and actuators are currently distributed throughout the environment - cameras, microphones, RFID, and TTL I/O. To date, measurements have been gathered manually. This is time consuming and leads to unsynchronised measurements. A modular, autonomous approach is desired. Such an approach will have externalities well beyond the Habitat project, which serves as the initial context for the system.

### System Purpose
The proposed system provides a modular, scalable, and synchronized data capture solution for behavioral testing labs. It enables precise multi-sensor data collection (video, audio, TTL events, RFID) while controlling external equipment. The controller manages synchronization, health monitoring, and data collation, while sensor modules autonomously capture and transmit data to a central repository. All power, synchronisation, control signalling and data transfer shall use a single POE (Power over Ethernet) connection.

### Scope
The system consists of a central controller with multiple PoE sensor modules. 
Planned sensor modules include cameras, microphones, TTL I/O, and RFID units.
The controller detects, synchronises, and manages these modules.
Data is collected, formatted, and stored for analysis.
The system is modular, allowing for easy expansion and maintenance.

### System Context
![Habitat drawio](https://github.com/user-attachments/assets/e1f6af6f-19d7-4cd7-baaa-4ba889ef3ccf)
- A main controller responsible for time synchronisation, data processing, and module management.
- A PoE switch which connects the controller to all modules.
- An array of module types, including	
  - A camera module, providing compressed video capture.
  - A microphone module, recording ultrasonic mice vocalisations.
  - A TTL I/O module, interfacing with various I/O devices.
  - An RFID module, providing subject tracking.

### Requirements
A full set of requirements is given in the System Requirements Specification document.

