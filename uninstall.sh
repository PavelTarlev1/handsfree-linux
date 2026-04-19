#!/usr/bin/env bash
# HandsFree — uninstaller
set -e

echo "=== HandsFree uninstaller ==="
echo

# Remove desktop entries
rm -f ~/.config/autostart/handsfree.desktop
rm -f ~/.local/share/applications/handsfree.desktop
echo "Removed desktop entries."

# Remove icons
for SIZE in 16 32 48 64 128 256 512; do
    rm -f "${HOME}/.local/share/icons/hicolor/${SIZE}x${SIZE}/apps/handsfree.png"
done
gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true
echo "Removed icons."

# Ask about user data
echo
read -p "Delete contacts database and config? (~/.config/handsfree) [y/N] " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
    rm -rf ~/.config/handsfree
    echo "Removed user data."
else
    echo "Kept user data at ~/.config/handsfree"
fi

echo
echo "=== Done! ==="
echo "You can now delete the HandsFree-Linux folder manually."
