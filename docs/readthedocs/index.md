# SAVIOUR

SAVIOUR (Synchronised Audio Video Input Output Recorder) is a modular and open-source approach to generating synchronised datasets from a multitude of sources including cameras, microphones, RFIDs, and TTLs. 

A SAVIOUR system consists of a "controller" device talking to one or more "module" devices (camera, microphone, RFID reader, TTL I/O, etc.), each handling one sensor or piece of equipment. All devices are Raspberry Pi 5 (for now!), connected together via LAN, typically with Power-over-Ethernet (PoE) so a single cable carries both power and network to each device. Researchers control everything - starting and stopping recordings, checking device status - from a web page on their own PC.

SAVIOUR was developed to meet the needs of behavioural neuroscience researchers (at the Simon's Initative for the Developing Brain, University of Edinburgh) who wanted an affordable, high throughput means of running their experiments with low barriers to entry. 

To learn more about making recordings with SAVIOUR, visit [Getting Started](getting_started.md) and [FAQs](faqs.md).

To learn about developing modules, GUIs, and the process of contributing to SAVIOUR, start with [How it Works](how_it_works.md) and then visit [Contributing to SAVIOUR](about/contributing.md).

## All pages

- [Getting Started](getting_started.md) - assigning device roles, connecting modules, running and exporting a recording session.
- [FAQs](faqs.md)
- [Using SAVIOUR with Ephys](open_ephys.md) - syncing and aligning SAVIOUR recordings with an Open Ephys rig.
- [Synchronisation](synchronisation.md) - the three layers that put every stream on one clock: fleet-wide PTP, FrameSync, ephys sync.
- [Post-Processing & Aligning](aligning.md) - correcting sample-rate drift and frame timing after recording, and the outputs you can build.
- [How it Works](how_it_works.md) - architecture and internals, for contributors.
- [Hardware](hardware.md) - the parts list for a controller, and for each module type.
- [CAD/3D Prints](cad.md)
- [Contributing to SAVIOUR](about/contributing.md)
- [License](about/license.md)
- [Acknowledgements](about/acknowledgements.md)
