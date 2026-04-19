#!/usr/bin/env bash
# HandsFree — create a new release tarball and push a git tag
set -e

CURRENT=$(cat VERSION)
echo "Current version: $CURRENT"
echo
read -p "New version (e.g. 1.1.0): " VERSION

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: version must be in format X.Y.Z"
    exit 1
fi

# Bump VERSION file
echo "$VERSION" > VERSION
echo "Bumped VERSION to $VERSION"

# Commit and tag
git add VERSION
git commit -m "chore: bump version to $VERSION"
git tag "v$VERSION"
git push
git push origin "v$VERSION"
echo "Pushed tag v$VERSION"

# Build tarball
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(dirname "$SCRIPT_DIR")"
TARBALL="$PARENT/handsfree-linux.tar.gz"

tar -czf "$TARBALL" \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*/__pycache__' \
    --exclude='*/.pytest_cache' \
    --exclude='*.pyc' \
    -C "$PARENT" "$(basename "$SCRIPT_DIR")"

echo
echo "=== Done! ==="
echo "Tarball: $TARBALL"
echo
echo "Now go to: https://github.com/PavelTarlev1/handsfree-linux/releases/new"
echo "  - Choose tag: v$VERSION"
echo "  - Upload: $TARBALL  (as handsfree-linux.tar.gz)"
