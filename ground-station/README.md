# Ground Station

## Building Pi Image

## Install SDM

## Run SDM

```bash
# store apps in a file called apps.txt
apps=$(cat <<EOF
gpsd
libgps-dev
gpsd-clients
libusb-1.0-0-dev
git
cmake
pkg-config
build-essential
direwolf
rpd-plym-splash
xserver-xorg
x11-xserver-utils
xinit
openbox
fonts-noto-color-emoji
chromium-browser
EOF
)
# write the apps to a file
echo "$apps" > apps.txt

# do we need to use --svc-disable systemd-timesyncd
sudo sdm --customize \
--extend --xmb 4096 --expand-root \
--plugin user:"adduser=gs|password=goodsoup" \
--plugin L10n:host \
--plugin disables:piwiz \
--plugin apps:"apps=@apps.txt" \
--plugin serial \
--plugin system:"ledheartbeat" \
--plugin bootconfig:"disable_splash=1" \
--plugin quietness:"consoleblank=0|quiet|nosplash|plymouth" \
--plugin copyfile:"from=./splash.png|to=/usr/share/plymouth/themes/pix/splash|chown=root.root|chmod=0644|mkdirif" \
--plugin btwifiset:"country=US" \
--plugin chrony \
--cscript ./post-install.sh \
--nowait-timesync \
--regen-ssh-host-keys \
--autologin \
--restart 2024-11-19-raspios-bookworm-arm64-lite.img

# this takes ~20 minutes to run
```

```bash
# post stuff
# edit /etc/xdg/openbox/autostart with
xset s off
xset s noblank
xset -dpms

# Allow quitting the X server with CTRL-ATL-Backspace
setxkbmap -option terminate:ctrl_alt_bksp

# Start Chromium in kiosk mode
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' ~/.config/chromium/'Local State'
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]\+"/"exit_type":"Normal"/' ~/.config/chromium/Default/Preferences
chromium-browser --disable-infobars --kiosk 'http://your-url-here'

# then edit .bash_profile with
[[ -z $DISPLAY && $XDG_VTNR -eq 1 ]] && startx -- -nocursor

rtl_fm -f 144390000 -D 4 | direwolf -n 1 -r 24000 -B 1200 -t 0 -d upomhxxxx -c sdr.conf -

ACHANNELS 1
ADEVICE null null

GPSD

CHANNEL 0

MYCALL KD8CJT-1
IGSERVER noam.aprs2.net
IGLOGIN KD8CJT-9 19121

MODEM 1200
AGWPORT 8000
KISSPORT 8001

TBEACON SENDTO=IG DELAY=0:30 EVERY=1 SYMBOL=R& comment="UM Balloon Ground Station 1"
PBEACON SENDTO=IG DELAY=0:30 EVERY=1 SYMBOL="car" OVERLAY=R lat=42.2943757 long=-83.7110013 alt=271 comment="UM Balloon Ground Station 1"

IGTXLIMIT 6 10


```

```bash
# get the SD card device name
# this will be something like /dev/sdb or /dev/mmcblk0
sudo fdisk -l

# write the image to the SD card
sudo sdm --burn /dev/sda --hostname gs-1 --expand-root 2024-11-19-raspios-bookworm-arm64-lite.img

# this takes ~7.5 minutes to run
```


To build a custom Pi image, we will use the `CustomPiOS` docker image.

### 1. Create Distro

> [!NOTE]
> We are using the `FullPageOS` image as a base for our distro, so you can pull the `FullPageOS` git submodule or use the pre-edited copy of it in `UMBGroundStation` and skip straight to the ["Build Distro"](#2-build-distro) section.

First we use CustomPiOS to help us pull base images and set up the folder structure. We can then modify the folder structure of the image to our liking, preparing scripts and configs.

We use FullPageOS's prebuilt folder structure now, so this step is no longer needed. Modify the configs and scripts in `pi-image/FullPageOS` to your liking instead.
<!-- You can also add files to the `os_make` folder, which will be copied to the image. -->

<details>
<summary>Show manual setup without FullPageOS</summary>

Running this docker-compose, you'll see:

```bash
cd pi-image
docker compose -f docker-compose-step-1.yml up -d

Creating network "a_default" with the default driver
Creating mydistro-create ... done
```

Then create a distro, we will call it `UMBGroundStation` for "Umich-Balloons Ground Station" since the distro name cannot contain a hyphen.

```bash
# Optional -g flag will also download you the latest version of raspbian in to the image folder, don't need if you are using another base image
docker exec -it mydistro-create CustomPiOS/make_custom_pi_os -g /os_make/UMBGroundStation

# Run this with your current user ID so you have permissions to edit the file
docker exec -it mydistro-create chown 1000:1000 -R /os_make/UMBGroundStation
```

</details>

### 2. Build Distro

Now there should be a folder called `UMBGroundStation` in your `pi-image` directory. This folder contains the distro files. You can edit the files in this folder to customize your distro.

Then you can build the example distro:

```bash
# Setup the docker-compose file (should exist in 'pi-image' folder)
# NOTE: If you built a custom distro in the last step, you'll need modify
# the docker-compose file to use your distro name instead of `UMBGroundStation`
docker compose -f docker-compose-step-2.yml up -d

# now set a base board and download the image
# get a list of available base boards with:
docker exec -it mydistro-build build --board list

# then set the base board and download with:
docker exec -it mydistro-build build --download --board raspberrypiarm64
```

Note: this can take a while. On my MacBook Air M4 it took ~8 minutes for reference.
