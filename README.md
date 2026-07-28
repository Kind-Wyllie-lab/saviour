<!-- Hi! I hope you're having a good day, wherever you are :) -->
<div align="center" style="margin: 20px"> 
  <img width="502" height="90" alt="SAVIOUR" src="https://github.com/user-attachments/assets/0f85edd9-a86b-4326-b66a-7c86d93b454a" />
    <h3 align="center">Synchronised Audio-Video Input-Output Recorder</h3>
</div>

<!-- PROJECT SHIELDS -->
<div align="center">

  [![Github Contributors](https://img.shields.io/github/contributors/Kind-Wyllie-lab/saviour)](#)
  [![Github Stars](https://img.shields.io/github/stars/Kind-Wyllie-lab/saviour?style=flat)](#)
  [![Github Release](https://img.shields.io/github/v/release/Kind-Wyllie-lab/saviour)](#)
  [![GitHub release date](https://img.shields.io/github/release-date/Kind-Wyllie-lab/saviour)](#)
  [![GitHub last commit](https://img.shields.io/github/last-commit/Kind-Wyllie-lab/saviour)](#)
  [![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE.txt)
  [![Docs](https://img.shields.io/readthedocs/saviour
  )](https://saviour.readthedocs.io/en/latest/)
  [![Report Bug](https://img.shields.io/badge/Report%20a%20bug-ff6600)](https://github.com/Kind-Wyllie-lab/habitat/issues)
  [![3D Printable Cases](https://img.shields.io/badge/3d%20printable%20cases-8A2BE2)]("https://grabcad.com/library/saviour-pi-5-cases-v1-0-0-2")

</div>

<!-- PROJECT LOGO -->
<div align="center">

  <p align="center">
    A modular and highly usable approach to synchronised, multimodal data capture using affordable open source components.
  </p>
</div>

## Installation
### 1. Manual Install

One line install, simply copy this into the terminal of an internet connected Pi.
```sh
curl -fsSL https://raw.githubusercontent.com/Kind-Wyllie-lab/saviour/main/install.sh | bash
```

After completion, run the following and select the correct configuration (e.g. controller, APA or module, camera)
```
sudo saviour-config
```

### 2. Using a pre-baked image

Not yet available. Eventually an OS image will be available which can be copied on to an SD card. 

## Usage

# Docs
Full documentation can be found at https://saviour.readthedocs.io/en/latest/ 

<!-- ABOUT THE PROJECT -->
## About The Project

### Background
The habitat project seeks to explore the behaviour and development of up to 50 rodents in a single large enclosure ("the habitat"). The research is being conducted by SIDB/UofE/Kind Lab with the intention of exploring the genetic factors relating to autism in rodents. To achieve this, experimental data must be gathered. Numerous sensors and actuators are currently distributed throughout the environment - cameras, microphones, RFID, and TTL I/O. To date, measurements have been gathered manually. This is time consuming and leads to unsynchronised measurements. A modular, autonomous approach is desired. Such an approach will have externalities well beyond the Habitat project, which serves as the initial context for the system.

### System Purpose
The proposed system provides a modular, scalable, and synchronized data capture solution for behavioral testing labs. It enables precise multi-sensor data collection (video, audio, TTL events, RFID) while controlling external equipment. The controller manages synchronization, health monitoring, and data collation, while sensor modules autonomously capture and transmit data to a central repository. All power, synchronisation, control signalling and data transfer shall use a single POE (Power over Ethernet) connection.

### Scope
The system consists of a central controller with multiple PoE sensor modules. 
Sensor modules include cameras, microphones, TTL I/O, and RFID units.
The controller detects, synchronises, and manages these modules.
Data is collected, formatted, and stored for analysis.
The system is modular, allowing for easy expansion and maintenance.

Each system includes:
- A main controller responsible for time synchronisation, data processing, and module management
- A PoE switch which connects the controller to all modules
- A storage dump which is likely a samba share running on the controller, a NAS or other dedicated device
- An array of modules, including:
  - A camera module, providing compressed video capture
  - A microphone module, recording ultrasonic mice vocalisations
  - A TTL I/O module, interfacing with various I/O devices
  - An RFID module, providing subject tracking

<!-- CONTRIBUTING -->
## Contributing
SAVIOUR is designed to be easily extensible to new types of modules and new user interfaces to control them in experiment specific ways.
Any contributions you make are **greatly appreciated**.

### Branches
- main - The latest release of saviour e.g. v1.2
- staging - This branch is used for final testing of new releases
- fix/ - Prefix for a branch in which a fix is developed
- feat/ - Prefix for a branch in which a new feature is developed
- refactor/ - Prefix for a branch in which a refactor is implemented

### Commits
Use the conventional commits framework wherever possible
https://www.conventionalcommits.org/en/v1.0.0/#summary 

### Workflow 
1. Fork the Project
2. Create your Feature Branch (`git checkout -b feat/AmazingNewModule`)
3. Commit your Changes (`git commit -m 'feat: Create AmazingNewModule'`)
4. Push to the Branch (`git push origin feat/AmazingNewModule`)
5. Open a Pull Request between your branch and the "develop" branch
6. When a number of changes have been accumulated in the "develop" branch, this will be merged with "staging"
7. When the changes are stable, staging will be given a tag for a new release and this will be merged with main - your AmazingNewModule is now part of SAVIOUR!

<!-- LICENSE -->
## License
Distributed under the MIT License. See `LICENSE.txt` for more information.

<!-- CONTACT -->
## Contact
Andrew Scott-George - ascottg@ed.ac.uk

Project Link: [https://github.com/Kind-Wyllie-lab/saviour](https://github.com/Kind-Wyllie-lab/saviour)


