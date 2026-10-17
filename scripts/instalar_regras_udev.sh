#!/usr/bin/env bash
# Prepara o Linux do AGV para o hardware (rodar uma vez, com sudo):
#  - regras udev: /dev/agv-arduino e /dev/rplidar fixos e ModemManager ignorando as portas;
#  - blacklist do gspca_kinect: o driver de webcam do kernel prende o Kinect, que e lido
#    pela libfreenect (sem isso aparece LIBUSB_ERROR_BUSY e o Kinect vira /dev/video0).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "rode com sudo: sudo bash $0" >&2
  exit 1
fi

PASTA="$(cd "$(dirname "$0")" && pwd)"
install -m 0644 "$PASTA/99-agv-serial.rules" /etc/udev/rules.d/99-agv-serial.rules
echo "blacklist gspca_kinect" > /etc/modprobe.d/agv-kinect-blacklist.conf
modprobe -r gspca_kinect 2>/dev/null || true

udevadm control --reload-rules
udevadm trigger --subsystem-match=tty
sleep 1
echo ">> portas do AGV:"
ls -l /dev/agv-arduino /dev/rplidar 2>/dev/null || echo "   (conecte o Arduino e o RPLIDAR e confira de novo com: ls -l /dev/agv-arduino /dev/rplidar)"
