# FAQs
***What is SAVIOUR?***<br>
SAVIOUR is a modular, affordable system for recording synchronised video, audio, and sensor data across multiple points in an experiment, built from low-cost networked Raspberry Pis instead of expensive proprietary hardware. 

One Pi acts as a controller, and all other Pis are specialised modules: camera modules, ultrasonic microphone modules, TTL ePhys sync modules, stimulus modules etc. 

All Pis connect together via PoE ethernet cable - simply connect your computer to the same LAN and access the GUI at *http://saviour.local* in a browser and you're ready to start making recordings.  

***What makes SAVIOUR unique?***<br>
SAVIOUR provides a framework for modular test rigs that is highly customisable and extendible to new types of module and setup. Already SAVIOUR has been used to modernise a wide variety of experiments including:

- Habitat, a large scale project that involves 24/7 recording from 16 cameras, microphones, and RFIDs over many weeks
- Active Place Avoidance, in which a special module was created to drive a rotating arena and a shock grid
- Loom, in which a special camera module was created to detect rat location using computer vision and play a loom stimulus when in a specific part of the arena  
- Acoustic startle, in which a special sound module was created to play a variety of sounds at the press of a button

Many SAVIOUR users also capture electrophysiology data in their experiments. A TTL module can be used to sync SAVIOUR's recordings to external systems such as an **Open Ephys** rig, so behavioural and neural data share a common timeline. This makes it incredibly easy to produce real-time multimodal graphs of video, audio, and neuronal activity. For more information, see https://github.com/Kind-Wyllie-lab/saviour-ephys-analysis. 

***Who is it for?***<br>
Primarily behavioural neuroscience labs, though the same approach suits any experiment needing several sensors recording in sync.

***Why was SAVIOUR made?***<br>
SAVIOUR was developed in response to many one-off data gathering rigs being created that did similar things in a messy, expensive, and hard to reproduce way.

***How do I know if PTP sync is good enough to start recording?***<br>
Click "Check Ready" on the Recording page - it checks ptp4l and phc2sys offset on every module against a threshold (50 µs by default) and reports which modules aren't ready. The System page also shows live per-module PTP offset if you want to watch it settle after a reboot.

***A camera was just rebooted - can I record straight away?***<br>
Give it 5–10 minutes first. phc2sys needs that long to converge its frequency estimate for that crystal; recording immediately after a reboot can leave a larger-than-usual (but still bounded) inter-camera phase offset.

***What happens to a recording if the NAS/export share goes down?***<br>
Files are staged locally on the module and export is retried with backoff once the share comes back - recordings aren't lost, but they won't appear on the share until the export queue catches up.

***Can I change which rig UI (basic / loom / apa / habitat / acoustic startle) a controller shows?***<br>
The frontend variant is selected by which App is imported in `src/controller/frontend/src/main.jsx`. Switching rigs means editing that import and rebuilding the frontend - it isn't a runtime setting yet.

***Do all modules need to be the same type?***<br>
No - a system is any mix of camera, microphone, TTL, RFID and rig-specific module types (loom camera, APA camera/arduino) all reporting to one controller. Add or remove modules freely; the controller discovers them automatically over mDNS.