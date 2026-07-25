#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
repo_root=$(CDPATH= cd -- "$project_root/../.." && pwd)
build_path=${MERLIN_BUILD_PATH:-/private/tmp/merlin-macos-build}
app_path="$build_path/Merlin.app"

swift build --package-path "$project_root" --build-path "$build_path"

binary_path=$(find "$build_path" -type f -name MerlinMac -perm -111 -print -quit)
if [ -z "$binary_path" ]; then
  echo "Built MerlinMac executable was not found." >&2
  exit 1
fi

mkdir -p "$app_path/Contents/MacOS"
mkdir -p "$app_path/Contents/Resources/Branding"
cp "$binary_path" "$app_path/Contents/MacOS/MerlinMac"
cp "$project_root/AppBundle/Info.plist" "$app_path/Contents/Info.plist"
cp "$project_root/AppBundle/Resources/Merlin.icns" "$app_path/Contents/Resources/Merlin.icns"
cp "$project_root/AppBundle/Resources/Branding/"*.png "$app_path/Contents/Resources/Branding/"
printf '%s\n' "$repo_root" > "$app_path/Contents/Resources/repository-root.txt"
codesign --force --deep --sign - "$app_path"
codesign --verify --deep --strict --verbose=2 "$app_path"
open -n "$app_path"
echo "Launched $app_path"
