#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
chmod +x ./iniciar_sistema ./launch_production || true
./launch_production
