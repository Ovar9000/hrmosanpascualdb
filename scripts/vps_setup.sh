#!/bin/sh
# One-time preparation of a fresh Ubuntu 24.04 droplet (README "Run it on a DigitalOcean droplet").
# Run as root on the server:   sh scripts/vps_setup.sh
# Installs Docker and git, adds swap, opens only SSH/HTTP/HTTPS in the firewall, keeps
# security updates automatic and, when you logged in with an SSH key, turns off password logins.
# Safe to run again.
set -e

if [ "$(id -u)" != "0" ]; then
    echo "Run this as root (or with sudo)." >&2
    exit 1
fi

echo "==> System updates"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q docker.io docker-compose-v2 git ufw unattended-upgrades
systemctl enable --now docker

echo "==> Automatic security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades

# 2 GB of swap keeps the image build and Postgres from running out of memory on a 2 GB droplet.
if ! swapon --show | grep -q .; then
    echo "==> Adding 2 GB swap"
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Firewall: SSH, HTTP and HTTPS only"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null

# Only when a key is installed, so this can never lock out a password-only login.
if [ -s /root/.ssh/authorized_keys ]; then
    echo "==> SSH: key logins only"
    printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' > /etc/ssh/sshd_config.d/60-keys-only.conf
    systemctl reload ssh
else
    echo "==> No SSH key found for root: password logins left on. Add a key and run this again."
fi

echo "==> Done. Docker $(docker --version | cut -d' ' -f3 | tr -d ,) ready."
