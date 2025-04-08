#!/bin/bash
function loadparams() {
    source $SDMPT/etc/sdm/sdm-readparams
}
#
# $1 is the phase: "0", "1", or "post-install"
#
phase=$1
pfx="$(basename $0)"
if [ "$phase" == "0" ]; then
    loadparams
    logtoboth "Running $pfx in phase $phase"
    exit 0
fi
if [ "$phase" == "1" ]; then
    loadparams
    logtoboth "Running $pfx in phase $phase"

    sudo apt-get update
    sudo apt-get install ca-certificates curl -y
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    # Add the repository to Apt sources:
    echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update

    sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
if [ "$phase" == "post-install" ]; then
    loadparams
    logtoboth "Running $pfx in phase $phase"

    logtoboth "Installing dependencies for RTL-SDR drivers"
    echo 'blacklist dvb_usb_rtl28xxu' | sudo tee --append /etc/modprobe.d/blacklist-dvb_usb_rtl28xxu.conf
    git clone https://github.com/rtlsdrblog/rtl-sdr-blog
    cd rtl-sdr-blog/
    mkdir build
    cd build
    cmake ../ -DINSTALL_UDEV_RULES=ON
    make
    sudo make install
    sudo cp ../rtl-sdr.rules /etc/udev/rules.d/
    sudo ldconfig
    logtoboth "RTL-SDR drivers installed"

    sudo systemctl enable gpsd

    # edit /etc/xdg/openbox/autostart
    cat <<EOF
    xset s off
    xset s noblank
    xset -dpms

    # Allow quitting the X server with CTRL-ATL-Backspace
    setxkbmap -option terminate:ctrl_alt_bksp

    # Start Chromium in kiosk mode
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' ~/.config/chromium/'Local State'
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]\+"/"exit_type":"Normal"/' ~/.config/chromium/Default/Preferences
    chromium-browser --disable-infobars --kiosk 'http://your-url-here'

fi