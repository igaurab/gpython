#!/bin/sh
set -eu

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
build="$here/build"
clone="$build/ghostty"
out="$build/out"
app="$build/gpython.app"
ghostty_commit=${GHOSTTY_COMMIT:-4dd09c4688ada3315daf235135b6cb15cacfa9a2}

fetch_ghostty() {
    if [ ! -d "$clone/.git" ]; then
        mkdir -p "$clone"
        git -C "$clone" init -q
        git -C "$clone" remote add origin https://github.com/ghostty-org/ghostty.git
    fi
    if [ "$(git -C "$clone" rev-parse HEAD 2>/dev/null)" != "$ghostty_commit" ]; then
        git -C "$clone" fetch -q --depth 1 origin "$ghostty_commit"
        git -C "$clone" checkout -q -f FETCH_HEAD
    fi
    if git -C "$clone" apply --check "$here/patches/build-without-xcode.patch" 2>/dev/null; then
        git -C "$clone" apply "$here/patches/build-without-xcode.patch"
    fi
}

build_libghostty() {
    export ZIG_GLOBAL_CACHE_DIR="$build/zig-global"
    export ZIG_LOCAL_CACHE_DIR="$build/zig-local"
    (cd "$clone" && zig build \
        -Dapp-runtime=none \
        -Demit-xcframework=false \
        -Demit-macos-app=false \
        -Doptimize=ReleaseFast \
        -Dsentry=false \
        -Di18n=false \
        -Demit-themes=false \
        --prefix "$out")
}

build_module_directory() {
    mkdir -p "$build/module"
    cp "$out/include/ghostty.h" "$build/module/ghostty.h"
    cp "$clone/include/module.modulemap" "$build/module/module.modulemap"
}

build_icon() {
    swiftc -O "$here/Tools/MakeIcon.swift" -o "$build/make-icon"
    rm -rf "$build/gpython.iconset"
    "$build/make-icon" "$build/gpython.iconset"
    iconutil -c icns "$build/gpython.iconset" -o "$build/gpython.icns"
}

build_app() {
    rm -rf "$app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
    swiftc -O -swift-version 5 \
        -I "$build/module" \
        "$here"/Sources/*.swift \
        "$out/lib/libghostty.a" \
        -lc++ \
        -framework AppKit -framework Metal -framework QuartzCore -framework CoreText \
        -framework CoreGraphics -framework CoreVideo -framework IOSurface -framework Carbon \
        -framework UniformTypeIdentifiers -framework IOKit -framework Security \
        -o "$app/Contents/MacOS/gpython-desktop"
    cp "$here/Resources/Info.plist" "$app/Contents/Info.plist"
    cp "$here/Resources/ghostty.conf" "$app/Contents/Resources/ghostty.conf"
    cp "$build/gpython.icns" "$app/Contents/Resources/gpython.icns"
    printf '%s\n' "$repo/.venv/bin/gpython" > "$app/Contents/Resources/gpython-program.txt"
    codesign --force --sign - "$app"
}

sync_gpython() {
    (cd "$repo" && uv sync -q)
}

fetch_ghostty
build_libghostty
build_module_directory
build_icon
build_app
sync_gpython
echo "$app"
