# Hardware
## PoE+ Switch
Every SAVIOUR rig begins with the Power over Ethernet+ (PoE+) switch that will be used to connect your modules, controller, and computer in a single local network. A PoE+ switch is essential as it provides power and data to your controller and modules in a single cable. You should choose a PoE+ switch with enough ports for the number of modules you plan on using +2 (1 controller and 1 user computer). For example, an acoustic startle rig might involve 2 camera modules, 1 microphone module, 1 TTL module for ephys sync, and 1 sound emitting module. The PoE switch for this rig should have 7 ports - 5 for the modules + 1 for the controller + 1 for the user PC. An appropriate switch might be, for example, the [Zyxel GS1008HP](https://www.zyxel.com/uk/en-gb/products/switch/8-port-gbe-unmanaged-poe-switch-gs1008hp/specifications) or the [NETGEAR 300 Series GS308LP](https://www.netgear.com/uk/business/wired/switches/unmanaged/gs308lp/). You will also need one ethernet cable per module/controller/computer you plan on using - make sure to order lengths that make sense for your setup!

(Note - if you find you want to user more modules than you initially planned for, you may be able to connect multiple switches together to extend your existing network without replacing your original switch)

## Controller
Once you have a switch, the next most important thing is a controller. Our recommended controller :
- A Raspberry Pi 5. Min. 2GB ram, recommended >=4GB ram
- A hybrid NVMe-PoE hat, like [this](https://thepihut.com/products/52pi-m-2-nvme-2280-poe-hat-for-raspberry-pi-5)
- An M.2 NVMe drive to run the OS and store data before it is backed up. Size depends on the volume of data you expect to generate and whether you plan on routinely backing up your data to non-volatile storage and deleting it off the controller (Hint: do this as often as possible!). We usually use 256-512GB drives. Make sure the form factor (22xx) fits the NVMe-PoE hat you buy! (2242 fits most hats). [Example.](https://thepihut.com/products/raspberry-pi-ssd?srsltid=AfmBOoqY3G97bpN7NWNJDVrh69g_xWELkoY9LI2hc6UaZMXG7EMG1Qdl)
- An [active cooler](https://thepihut.com/products/active-cooler-for-raspberry-pi-5) to keep it from melting
- An [rtc battery](https://thepihut.com/products/rtc-battery-for-raspberry-pi-5) to ensure the clock stays accurate between reboots
- A [solid case](https://thepihut.com/products/industrial-grade-metal-case-for-raspberry-pi-5) to hold it all together

At time of writing, the parts for a controller should cost around £200. 

## Modules
SAVIOUR modules come in many varieties but share the same basic hardware, with specific additions depending on their function. 

### Shared Hardware
Every module requires:
- A Raspberry Pi 5. RAM requirement depends on application, 1GB may be sufficient for some modules while others may need 2GB or greater
- A PoE hat like [this](https://thepihut.com/products/poe-hat-for-raspberry-pi-5-with-cooling-fan)
- An SD card for the OS to run on, and to store recorded data until it is exported to the controller. Size again depends on application and expected time until export - 32GB is usually sufficient. For maximum reliability, we recommend the use of a [high endurance SD card](https://www.argos.co.uk/product/2847230)
- Some form of case. Most modules can make use of the same [metal case](https://thepihut.com/products/industrial-grade-metal-case-for-raspberry-pi-5) as the controller, others such as cameras may also require special 3D printed parts. We have 

### Cameras
All of the [shared module hardware](#shared-hardware), plus one of:
- A [Pi HQ camera module](https://thepihut.com/products/raspberry-pi-high-quality-camera-module) and [lens](https://thepihut.com/products/raspberry-pi-high-quality-camera-lens). These come with an IR filter built in, so for low light experiments this must be manually removed. Or alternatively...
- A Pi Camera Module 3, typically [the NOIR version](https://thepihut.com/products/raspberry-pi-camera-module-3-noir) (comes with the IR filter already removed)
- A suitable means of mounting the camera. The standard thread size for mounting cameras is UNC 1/4-20. The HQ camera comes with this built in, for Camera Module 3 there exist solutions like [this](https://thepihut.com/products/tripod-mount-for-raspberry-pi-camera-modules). Once you have a female UNC 1/4-20 thread exposed, you can use a tripod or something like [this](https://thepihut.com/products/heavy-duty-tripod-swivel-ball-adapter), which we have found to be very versatile. Refer to [the CAD page](cad.md) for 3D prints that may help with mounting your cameras.

#### Special Cameras
We are currently working on deepening the computer vision capabilities of SAVIOUR. There are exciting technologies being developed within the space for improving the inference performance of Pis. In particular, we are looking at using the [Hailo Pi AI Hats](https://thepihut.com/products/raspberry-pi-ai-hat-2) and [Pi AI Camera](https://thepihut.com/products/raspberry-pi-ai-camera). Contributions from the open-source community around these technologies and performant low resource computer vision models are very welcome!

Pi HQ cameras can be directly substituted with [Global Shutter cameras](https://thepihut.com/products/raspberry-pi-global-shutter-camera) where an application would benefit from this, however note that these are (to our knowledge) limited to a lower FPS than the HQ cameras.

### Ultrasonic Microphones
Each microphone module is capable of supporting up to 4 AudioMoth USB ultrasonic microphones. The requirements are thus all of the [shared module hardware](#shared-hardware), plus:
- 1-4x [AudioMoth USB microphone](https://www.openacousticdevices.info/product-page/audiomoth-usb-microphone)
- The same number of [USB-Micro USB cables](https://thepihut.com/products/usb-to-micro-usb-cable-0-5m)
- If desired, a [3.5mm microphone module with a good ultrasonic response](https://micbooster.com/product/primo-em258-mono-module-with-35mm-plug), to extend the range and positioning of the AudioMoth  

### TTL (/Ephys Sync)
TTL modules can be used for a variety of I/O purposes, for example receiving inputs from triggers such as nose pokes and providing outputs to stimuli such as LEDs. However, their most common usage is for generating signals that can be used to align a SAVIOUR dataset with an Open-Ephys dataset. 

For more information on the alignment process, please refer to the [dedicated GitHub repo](https://github.com/Kind-Wyllie-lab/saviour-ephys-analysis). The appropriate hardware is dependent on the Open-Ephys setup and the users preference. 

**IMPORTANT**: If you want your experiment to include aligned SAVIOUR and ephys data, you **must** read the [guide to using SAVIOUR with ephys](open_ephys.md). Researchers have lost hours of data because the HDMI cable was plugged into the wrong port of their HDMI board. Read the guide, and double check everything is working correctly before hitting record!

Generally, the hardware is all of the [shared module hardware](#shared-hardware), plus:
- If an [open ephys I/O board](https://open-ephys.org/acquisition-system/io-board-pcb) is available, we recommend using [BNC test cables](https://uk.rs-online.com/web/p/test-leads/2967747) or an equivalent.
- If no such board is available, we recommend a HDMI cable and a [HDMI breakout connector](https://uk.rs-online.com/web/p/hdmi-connectors/7248959) with some [Dupont leads](https://thepihut.com/products/thepihuts-jumper-bumper-pack-120pcs-dupont-wire). 

