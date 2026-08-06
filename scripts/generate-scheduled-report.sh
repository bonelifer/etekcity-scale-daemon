#!/usr/bin/bash
# Generates a timestamped PDF report. Intended to be run on a schedule (the
# etekcity-scale-report-generate.timer systemd unit, or a cron job) rather
# than invoked directly. Configure via environment variables, not flags,
# since a scheduler invokes this with a fixed command line.
set -e

CONFIG="${ETEKCITY_CONFIG:-/etc/etekcity-scale-daemon/config.ini}"
REPORT_DIR="${ETEKCITY_REPORT_DIR:-/var/lib/etekcity-scale-daemon/reports}"

mkdir -p "${REPORT_DIR}"
timestamp="$(date +%Y%m%d-%H%M%S)"
etekcity-scale-report --config "${CONFIG}" --output "${REPORT_DIR}/report-${timestamp}.pdf"
