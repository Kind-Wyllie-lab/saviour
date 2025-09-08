# Modular Synchronised Data Capture System

## System Requirements Specification 

**Version History**

| Date | Version | Author(s) | Comments |
| :---- | :---- | :---- | :---- |
| 170325 | 1 | ASG | File created |
| 180325 | 1.1 | ASG | Reformat and add requirements |
| 190325 | 1.2 | PS | Updates to 1.2, 3.1.2, 3.1.5, 3.1.8, 3.2.4, 3.2.6.2, 3.2.9.2, 5.1, 6.5 (new), 7.1, 7.3, 8.1, 8.2, 8.4, 11.3 (new) |

**Definitions**  
Shall \- binding requirement. Project only successful if this is achieved.  
Should \- preferential requirement. We want to do all of this but not it’s necessary for the minimum viable product.  
May \- suggestion or allowance.

**Acronyms and Abbreviations**  
SIDB \- Simons Initiative for the Developing Brain  
PTP \- Precision Time Protocol  
TTL \- Transistor Transistor Logic  
GUI \- Graphical User Interface  
TCP \- Transmission Control Protocol  
UDP \- User Datagram Protocol  
IP \- Internet Protocol  
PoE \- Power over Ethernet  
RFID \- Radio Frequency Identification   
I/O \- Input / Output

1. **System Overview**  
   1. **Background**

The habitat project seeks to explore the behaviour and development of up to 50 rodents in a single large enclosure (“the habitat”). The research is being conducted by SIDB/UofE/Kind Lab with the intention of exploring the genetic factors relating to autism in rodents. To achieve this, experimental data must be gathered. Numerous sensors and actuators are currently distributed throughout the environment \- cameras, microphones, RFID, and TTL I/O. To date, measurements have been gathered manually. This is time consuming and leads to unsynchronised measurements. A modular, autonomous approach is desired. Such an approach will have externalities well beyond the Habitat project, which serves as the initial context for the system.

2. **System Purpose**

The proposed system provides a modular, scalable, and synchronized data capture solution for behavioral testing labs. It enables precise multi-sensor data collection (video, audio, TTL events, RFID) while controlling external equipment. The controller manages synchronization, health monitoring, and data collation, while sensor modules autonomously capture and transmit data to a central repository. All power, synchronisation, control signalling and data transfer shall use a single POE (Power over Ethernet) connection.

3. **Scope**

The system consists of a central controller with multiple PoE sensor modules.   
Planned sensor modules include cameras, microphones, TTL I/O, and RFID units.  
The controller detects, synchronises, and manages these modules.  
Data is collected, formatted, and stored for analysis.  
The system is modular, allowing for easy expansion and maintenance.

4. **System Context**

![][image1]

- A main controller responsible for time synchronisation, data processing, and module management.  
- A PoE switch which connects the controller to all modules.  
- An array of module types, including	  
  - A camera module, providing compressed video capture.  
  - A microphone module, recording ultrasonic mice vocalisations.  
  - A TTL I/O module, interfacing with various I/O devices.  
  - An RFID module, providing subject tracking.

2. **System Functions**  
   1. **Controller Functions**  
      1. Detect connected PoE sensor modules (auto-discovery).  
      2. Synchronize all devices using PTP (Precision Time Protocol).  
      3. Start/stop recording sessions across all modules.  
      4. Monitor health (temperature, connectivity, power draw).  
      5. Aggregate and process sensor data into NWB format.  
      6. Provide a GUI/dashboard for real-time monitoring.

   2. **Module Functions**  
      1. Capture data based on role (camera, mic, I/O, RFID).  
      2. Synchronise timestamps with controller using PTP.  
      3. Stream live data to the controller for preview.  
      4. Store buffered data and upload post-capture.  
      5. Report module status and diagnostics.

3. **Functional Requirements**  
   1. **Controller Requirements**  
      1. Controller shall use PTP (IEEE 1588\) to synchronize all modules.  
      2. Controller may use PoE (Power over Ethernet) for communication & power.  
      3. Controller shall support Gigabit Ethernet for high-speed data transfer.  
      4. Controller shall include a GUI dashboard for monitoring & control.  
      5. Controller should package all data in NWB format.  
      6. Controller may support MQTT and ZeroMQ for low-latency messaging.  
      7. Controller may use pre-made PCBs or a custom PCB for efficiency.  
      8. Controller shall distribute file share, database, other access information to modules. 

   2. **Module Requirements (Generic)**  
      1. Each module shall connect via PoE (Ethernet Cat5e/6).  
      2. Each module shall support PTP synchronization.  
      3. Each module shall communicate with the controller via UDP/TCP protocols.  
      4. Each module shall be able to stream live data or buffer for later upload and should do both simultaneously.  
      5. Each module should report health status (temperature, power usage, network latency).

      6. **Camera Module**   
         1. Shall record at ≥100 FPS (H.264/H.265).  
         2. Should provide a low-latency live preview at lower framerates .  
         3. Shall timestamp frames with PTP.

      7. **Microphone Module**   
         1. Shall capture ≥192 kHz lossless audio.  
         2. Shall sync audio timestamps with PTP.

      8. **TTL I/O**  
         1. Shall provide digital & analog inputs/outputs for external device control.  
         2. Shall log event timestamps with millisecond precision.

      9. **RFID**  
         1. Shall log subject detections with timestamps.  
         2. Shall upload readings to a central database immediately.  
         3. Should support multiple RFID tags simultaneously.

   

4. **Usability Requirements**  
   1. System shall be written in Python and C++ for flexibility.  
   2. System code shall be well-documented and commented for maintainability.  
   3. System GUI shall be readable for colorblind/dyslexic users.  
   4. System shall support headless operation (SSH/web interface).  
   5. System shall be deployable with minimal technical expertise and require ≤30 minutes for a full setup.  
   6. System shall include a method for full recovery within 10 minutes in case of catastrophic failure.

   

5. **Performance Requirements**  
   1. System shall support ≥40 concurrent PoE modules.  
   2. System should support expansion up to 32 sensor modules without redesigning the core architecture.  
   3. System shall achieve ≤1ms timestamp drift across modules.  
   4. System shall provide a live preview with ≤100ms latency.  
   5. System shall support real-time event logging with millisecond precision.  
   6. System shall execute control commands (e.g., "start recording", "change exposure time") with a maximum latency of 10ms.

   

6. **System Interface Requirements**  
   1. System shall use Ethernet (PoE) to connect modules to the controller.  
   2. System shall use UDP/IP or ZeroMQ for module-controller communication.  
   3. System shall package data to be compatible with third-party data analysis tools (e.g., MATLAB, Python Pandas, NWB).  
   4. System shall allow remote data export via NFS, FTP, or cloud sync.  
   5. System shall feature a standard mount attachment.

   

7. **Reliability & Maintainability Requirements**  
   1. System shall operate 24/7 with 99% uptime in data collection mode.  
   2. System should allow for remote firmware updates.  
   3. System may allow for replacement of failed modules without interrupting active ones.  
   4. System shall report hardware failures (e.g., overheating, disconnects).  
   5. System should detect failures and recover automatically without user intervention.  
   6. System shall continuously monitor its own status and report health metrics to the user.  
   7. System shall prevent data loss by implementing redundant storage & backups.

   

8. **Environmental & Physical Characteristics**  
   1. System elements exposed to the inside of the habitat should be rated IP67 (water/dustproof) and should tolerate autoclaving at 134C.  
   2. System shall not use materials known to have negative effects on the health of rodents.   
   3. System shall be constructed with off-the-shelf components where possible.  
   4. Sensor modules should be compact (\<10x10x5cm) and lightweight (\<500g).  
   5. The system shall operate in various lab conditions, including exposure to temperature fluctuations, dust, and potential electrical noise.

   

9. **Security Requirements**  
   1. System shall require user authentication for admin controls.  
   2. System may encrypt all network communications.  
   3. System shall log all system events & failures.

10. **Cost & Lifecycle Requirements**  
    1. System shall be constructed primarily from affordable, off-the-shelf components.  
    2. System should minimize recurring costs (e.g., licensing, cloud storage).  
    3. System shall be cost-effective for labs with limited budgets (\<£? per module).  
    4. System may use custom PCBs where cost savings outweigh off-the-shelf alternatives.  
    5. System should avoid proprietary hardware unless necessary for performance.

    

11. **Adaptability & Future Expansion**  
    1. System shall allow additional modules to be plug-and-play.  
    2. System may integrate with cloud-based storage & analysis platforms.  
    3. System should be able to trigger events at specific times.

    

12. **Verification & Testing**  
    1. System shall be validated against the Test Plan Document.  
    2. Modules shall be tested for latency, synchronization, and data integrity.  
    3. System shall be considered successful if all modules remain within 1ms sync.

[image1]: <data:image/png;base64,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>