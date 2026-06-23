#!/usr/bin/env bash
# HandsFree — create a new release tarball and push a git tag
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CURRENT=$(cat "$SCRIPT_DIR/VERSION")
echo "Current version: $CURRENT"
echo
read -p "New version (e.g. 1.1.0): " VERSION

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: version must be in format X.Y.Z"
    exit 1
fi

# Revert everything if something goes wrong
cleanup() {
    if [[ $? -ne 0 ]]; then
        echo
        echo "Release failed — reverting..."
        echo "$CURRENT" > "$SCRIPT_DIR/VERSION"
        git -C "$SCRIPT_DIR" checkout -- VERSION 2>/dev/null || true
        git -C "$SCRIPT_DIR" tag -d "v$VERSION" 2>/dev/null || true
        echo "Reverted to $CURRENT"
    fi
}
trap cleanup EXIT

# Bump VERSION file
echo "$VERSION" > "$SCRIPT_DIR/VERSION"
echo "Bumped VERSION to $VERSION"

# Commit and tag
git -C "$SCRIPT_DIR" add VERSION
git -C "$SCRIPT_DIR" commit -m "feat: v$VERSION"
git -C "$SCRIPT_DIR" tag "v$VERSION"
git -C "$SCRIPT_DIR" push
git -C "$SCRIPT_DIR" push origin "v$VERSION"
echo "Pushed tag v$VERSION"

# Build tarball via git archive (clean, no untracked files)
TARBALL="/tmp/handsfree-linux.tar.gz"
git -C "$SCRIPT_DIR" archive --format=tar.gz --prefix="HandsFree-Linux-$VERSION/" "v$VERSION" -o "$TARBALL"
echo "Tarball: $TARBALL"

# Upload to GitHub release
if command -v gh &>/dev/null; then
    gh release create "v$VERSION" "$TARBALL" \
        --repo PavelTarlev1/HandsFree-Linux \
        --title "v$VERSION" \
        --notes "> ⚠️ **Still in development — but fully working.**"
    echo "Release published: https://github.com/PavelTarlev1/handsfree-linux/releases/tag/v$VERSION"
else
    echo
    echo "gh CLI not found. Upload manually:"
    echo "  https://github.com/PavelTarlev1/handsfree-linux/releases/new"
    echo "  - Tag: v$VERSION"
    echo "  - Asset: $TARBALL"
fi

echo
echo "=== Done! ==="
