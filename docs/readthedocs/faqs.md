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