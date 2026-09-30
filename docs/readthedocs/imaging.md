# Setting Up Many Devices

Installing SAVIOUR one device at a time is slow for a large rig. Instead, set up **one** card, capture it as a master image, flash that image to all the other cards, then tell each card what it is. Each card configures itself on first boot, with no monitor, keyboard or SSH needed on the target Pi.

## What you need

- **A Linux machine to run the scripts on.** Your **controller** works well, and is the easiest option if you use Windows: plug the card readers into the controller and connect to it over SSH (`ssh pi@<controller-ip>` works from Windows PowerShell). Windows itself can't do this, because it can't read the Linux partition on the cards.
- **USB SD card readers**, ideally several on a powered USB hub.
- **Free disk space at least the size of the source card** (e.g. 64 GB for a 64 GB card) for capturing the image. The controller's NVMe drive is fine for this; another Pi's SD card usually isn't.

All commands below are run from the SAVIOUR folder on that machine:

```
cd /usr/local/src/saviour
```

Each script opens a menu when run with no arguments. Cards are listed with their size and, where possible, their current hostname and role, so you can tell identical cards apart.

## 1. Prepare a source card

Flash Raspberry Pi OS to one card, boot it and install SAVIOUR (see [Getting Started](getting_started.md#installing-saviour)). Leave its role unset, then shut it down cleanly and move the card to a reader.

## 2. Capture the master image

```
sudo scripts/capture_master_image.sh
```

Pick the source card and where to save the `.img` file. The image is shrunk to the space actually used, so it's usually only a few GB. Keep it: you can reuse it for every future batch.

<!-- screenshot: capture_master_image.sh source/output picker -->

## 3. Flash the cards

Put the blank cards in the readers, then:

```
sudo scripts/multiclone.sh
```

Pick the image and tick the target cards. **Everything on the ticked cards is erased.** All cards are written in parallel, and each one is then given its own identity (SSH keys, machine ID, a temporary `saviour-unprov-XXXX` hostname) and expanded to fill the card.

<!-- screenshot: multiclone.sh target picker -->

> **Just need a few cards now?** `sudo scripts/clone_direct.sh` copies a source card straight to the targets without making an image file. It's slower per card, but needs no free disk space.

## 4. Set each card's role and type

Leave the cards in the readers and run:

```
sudo scripts/configure_card.sh
```

Tick the cards, choose **Module** or **Controller**, then the type (camera, microphone, TTL, ...). You can configure a batch of cards of the same type in one go; run it again for the next type. A controller also asks for its network settings, and only one controller can be configured at a time.

<!-- screenshot: configure_card.sh card picker and type menu -->

To do the same thing in one line (e.g. four camera modules):

```
sudo scripts/configure_card.sh module camera sda sdb sdc sdd
```

## 5. Boot

Put each card in its Pi and power it on. On first boot it configures itself for its role, which takes a few minutes, then restarts SAVIOUR. When it's done:

- its hostname changes to the type, role and last four characters of its MAC address, e.g. `camera-module-a2d2`;
- it appears on the controller's Dashboard like any other module.

If a card doesn't appear, SSH into it and check `/var/log/saviour-config.log`.

## Good to know

- **If the master image was made before this setup process existed**, `configure_card.sh` warns that the card won't configure itself. Run `sudo saviour-config` on that Pi instead, or capture a fresh master.
- **Types that need extra software** (e.g. `hailo_camera`) install it on first boot, which needs an internet connection, unless the master image was already set up as that type.
- **Plugging a card into Windows** shows only its small `bootfs` partition. If Windows offers to "scan and fix" it, you can skip that. If it says **"You need to format the disk"**, always click **Cancel**: that's the card's Linux partition, and formatting it erases the operating system.
