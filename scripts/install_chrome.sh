#!/usr/bin/env bash
# Install Google Chrome stable.
#
#   scripts/install_chrome.sh           # no sudo: unpack the official .deb into ~/.local/opt
#   scripts/install_chrome.sh --system  # with sudo: apt install (gets auto-updates via Google's repo)
#
# The no-sudo copy does NOT auto-update: re-run this script to update it.
set -euo pipefail

DEB_URL="https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
DEST="${HOME}/.local/opt/google-chrome"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "downloading ${DEB_URL}"
curl -fsSL -o "$tmp/chrome.deb" "$DEB_URL"

if [[ "${1:-}" == "--system" ]]; then
  sudo apt-get install -y "$tmp/chrome.deb"
  /opt/google/chrome/chrome --version
  exit 0
fi

rm -rf "$DEST.new"
mkdir -p "$DEST.new"
dpkg-deb -x "$tmp/chrome.deb" "$DEST.new"
rm -rf "$DEST"
mv "$DEST.new" "$DEST"

"$DEST/opt/google/chrome/chrome" --version
echo "installed to $DEST/opt/google/chrome/chrome (auto-detected by webtest)"
