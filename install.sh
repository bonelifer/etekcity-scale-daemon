#!/usr/bin/bash
# Installs etekcity-scale-daemon: creates a venv, installs the package from
# this checkout, seeds the config, creates the service user, and installs
# and enables the systemd unit. Re-running is safe: it skips steps that are
# already done (existing config, existing user) and upgrades the rest.
set -e

if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root (e.g. with sudo)." >&2
    exit 1
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    echo "Usage: sudo ./install.sh"
    echo "Installs etekcity-scale-daemon as a systemd service. No options."
    exit 0
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/etekcity-scale-daemon"
CONFIG_DIR="/etc/etekcity-scale-daemon"
SERVICE_USER="etekcity-scale-daemon"

echo "==> Creating virtual environment at ${INSTALL_DIR}/venv"
python3 -m venv "${INSTALL_DIR}/venv"

echo "==> Installing etekcity-scale-daemon from ${REPO_DIR}"
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install --quiet "${REPO_DIR}"

echo "==> Linking commands into /usr/bin"
ln -sf "${INSTALL_DIR}/venv/bin/etekcity-scale-daemon" /usr/bin/etekcity-scale-daemon
ln -sf "${INSTALL_DIR}/venv/bin/etekcity-scale-report" /usr/bin/etekcity-scale-report
ln -sf "${INSTALL_DIR}/venv/bin/etekcity-scale-prune" /usr/bin/etekcity-scale-prune
ln -sf "${INSTALL_DIR}/venv/bin/etekcity-scale-alert-check" /usr/bin/etekcity-scale-alert-check
ln -sf "${INSTALL_DIR}/venv/bin/etekcity-scale-api" /usr/bin/etekcity-scale-api
cp "${REPO_DIR}/scripts/generate-scheduled-report.sh" "${INSTALL_DIR}/generate-scheduled-report.sh"
chmod +x "${INSTALL_DIR}/generate-scheduled-report.sh"
ln -sf "${INSTALL_DIR}/generate-scheduled-report.sh" /usr/bin/etekcity-scale-generate-report

echo "==> Seeding config"
mkdir -p "${CONFIG_DIR}"
if [[ -f "${CONFIG_DIR}/config.ini" ]]; then
    echo "    ${CONFIG_DIR}/config.ini already exists, leaving it as-is."
else
    cp "${REPO_DIR}/config/etekcity-scale-daemon.ini.example" "${CONFIG_DIR}/config.ini"
    echo "    Wrote ${CONFIG_DIR}/config.ini -- edit it before (or after) starting the service."
fi

echo "==> Creating service user"
if ! id "${SERVICE_USER}" &>/dev/null; then
    useradd --system --no-create-home --group "${SERVICE_USER}"
fi

echo "==> Installing systemd units"
cp "${REPO_DIR}/systemd/etekcity-scale-daemon.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/etekcity-scale-report-generate.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/etekcity-scale-report-generate.timer" /etc/systemd/system/
cp "${REPO_DIR}/systemd/etekcity-scale-alert-check.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/etekcity-scale-alert-check.timer" /etc/systemd/system/
cp "${REPO_DIR}/systemd/etekcity-scale-api.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now etekcity-scale-daemon

echo "==> Done. Edit ${CONFIG_DIR}/config.ini if you haven't, then watch discovery with:"
echo "        journalctl -u etekcity-scale-daemon -f"
echo "==> Scheduled report generation, alert checking, and the HTTP API are installed"
echo "    but not enabled (opt-in). To turn them on:"
echo "        sudo systemctl enable --now etekcity-scale-report-generate.timer"
echo "        sudo systemctl enable --now etekcity-scale-alert-check.timer"
echo "        sudo systemctl enable --now etekcity-scale-api.service"
