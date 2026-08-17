#!/bin/sh
set -eu

# Python-free first-run installer for Linux and macOS source checkouts.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BOOTSTRAP_PROPERTIES="$SCRIPT_DIR/portable-bootstrap.properties"
MODE=${1:-foreground}
[ "$#" -eq 0 ] || shift

VERSION=$(sed -n 's/^VERSION = "\([^"]*\)"$/\1/p' "$PROJECT_ROOT/src/sbk_dashboard/version.py")
if [ -z "$VERSION" ]; then
    echo "Unable to determine the SBK Dashboard version." >&2
    exit 1
fi

property() {
    sed -n "s/^$1=//p" "$BOOTSTRAP_PROPERTIES"
}
REPOSITORY_URL=$(property repository.url)
MAXIMUM_ARCHIVE_BYTES=$(property archive.max.bytes)
MAXIMUM_CHECKSUM_BYTES=$(property checksum.max.bytes)
LOCK_WAIT_SECONDS=$(property lock.wait.seconds)
LOCK_STALE_SECONDS=$(property lock.stale.seconds)
case "$MAXIMUM_ARCHIVE_BYTES$MAXIMUM_CHECKSUM_BYTES$LOCK_WAIT_SECONDS$LOCK_STALE_SECONDS" in
    ''|*[!0-9]*) echo "Portable bootstrap properties are invalid." >&2; exit 1 ;;
esac

case $(uname -s) in
    Linux) OS_ID=linux ;;
    Darwin) OS_ID=macos ;;
    *) echo "No standalone SBK Dashboard runtime is available for $(uname -s)." >&2; exit 1 ;;
esac
case $(uname -m) in
    x86_64|amd64) ARCH_ID=amd64 ;;
    arm64|aarch64) ARCH_ID=arm64 ;;
    *) echo "No standalone SBK Dashboard runtime is available for architecture $(uname -m)." >&2; exit 1 ;;
esac
PLATFORM_ID="$OS_ID-$ARCH_ID"

USER_HOME=${HOME:?}
while [ "$USER_HOME" != / ] && [ "${USER_HOME%/}" != "$USER_HOME" ]; do
    USER_HOME=${USER_HOME%/}
done

if [ -n "${SBK_DASHBOARD_HOME:-}" ]; then
    case "$SBK_DASHBOARD_HOME" in
        /*) PORTABLE_HOME=$SBK_DASHBOARD_HOME ;;
        \~) PORTABLE_HOME=$USER_HOME ;;
        \~/*) PORTABLE_HOME=$USER_HOME/${SBK_DASHBOARD_HOME#\~/} ;;
        *) PORTABLE_HOME=$PWD/$SBK_DASHBOARD_HOME ;;
    esac
else
    PORTABLE_HOME="$USER_HOME/.sbk-dashboard"
fi
while [ "$PORTABLE_HOME" != / ] && [ "${PORTABLE_HOME%/}" != "$PORTABLE_HOME" ]; do
    PORTABLE_HOME=${PORTABLE_HOME%/}
done
case "$PORTABLE_HOME" in
    /|"$USER_HOME"|*/../*|*/..|*/./*|*/.) echo "SBK_DASHBOARD_HOME must be a dedicated subdirectory without traversal." >&2; exit 1 ;;
esac

ARCHIVE_NAME="sbk-dashboard-$VERSION-$PLATFORM_ID.tar.gz"
BASE_URL=${SBK_DASHBOARD_PORTABLE_BASE_URL:-"$REPOSITORY_URL/releases/download/v$VERSION"}
case "$BASE_URL" in https://*|file://*) ;; *) echo "Portable runtime URL must use HTTPS (or file:// for an offline mirror)." >&2; exit 1 ;; esac
CACHE_DIRECTORY="$PORTABLE_HOME/cache/releases"
INSTALL_PARENT="$PORTABLE_HOME/distributions/$VERSION/$PLATFORM_ID"
INSTALL_DIRECTORY="$INSTALL_PARENT/$ARCHIVE_NAME"
EXECUTABLE="$INSTALL_DIRECTORY/sbk-dashboard-$VERSION-$PLATFORM_ID/sbk-dashboard"
MARKER="$INSTALL_DIRECTORY/.installed-sha256"
LOCK_DIRECTORY="$PORTABLE_HOME/launcher/bootstrap-locks/$VERSION-$PLATFORM_ID.lock"
CHECKSUM_PART=
ARCHIVE_PART=
STAGING=
LISTING=
LOCK_OWNED=false

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "$1" | awk '{print $NF}'
    else
        echo "A SHA-256 tool (sha256sum, shasum, or openssl) is required." >&2
        return 1
    fi
}

runtime_valid() {
    [ -x "$EXECUTABLE" ] && [ -f "$MARKER" ] || return 1
    MARKER_ARCHIVE=$(sed -n '1p' "$MARKER" 2>/dev/null || true)
    MARKER_EXECUTABLE=$(sed -n '2p' "$MARKER" 2>/dev/null || true)
    case "$MARKER_ARCHIVE$MARKER_EXECUTABLE" in *[!0-9a-fA-F]*|'') return 1 ;; esac
    [ "${#MARKER_ARCHIVE}" -eq 64 ] && [ "${#MARKER_EXECUTABLE}" -eq 64 ] || return 1
    [ "$(sha256_file "$EXECUTABLE")" = "$MARKER_EXECUTABLE" ]
}

download() {
    DOWNLOAD_LIMIT=$3
    case "$1" in
        file://*)
            DOWNLOAD_SOURCE=${1#file://}
            DOWNLOAD_BYTES=$(wc -c <"$DOWNLOAD_SOURCE" | tr -d ' ')
            [ "$DOWNLOAD_BYTES" -le "$DOWNLOAD_LIMIT" ] || return 1
            cp "$DOWNLOAD_SOURCE" "$2"
            return
            ;;
    esac
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --silent --show-error --connect-timeout 15 --max-time 600 --retry 2 \
            --max-filesize "$DOWNLOAD_LIMIT" --output "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        DOWNLOAD_BLOCKS=$(((DOWNLOAD_LIMIT + 511) / 512))
        (ulimit -f "$DOWNLOAD_BLOCKS"; wget --https-only --timeout=30 --tries=3 --output-document="$2" "$1")
    else
        echo "The first Python-free start requires curl or wget to download the verified runtime." >&2
        return 1
    fi
}

release_lock() {
    if [ "$LOCK_OWNED" = true ] && [ -d "$LOCK_DIRECTORY" ]; then
        rm -f "$LOCK_DIRECTORY/pid"
        rmdir "$LOCK_DIRECTORY" 2>/dev/null || true
        LOCK_OWNED=false
    fi
}
cleanup() {
    [ -z "$CHECKSUM_PART" ] || rm -f "$CHECKSUM_PART"
    [ -z "$ARCHIVE_PART" ] || rm -f "$ARCHIVE_PART"
    [ -z "$LISTING" ] || rm -f "$LISTING"
    [ -z "$STAGING" ] || rm -rf "$STAGING"
    release_lock
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$CACHE_DIRECTORY" "$INSTALL_PARENT" "$(dirname -- "$LOCK_DIRECTORY")"
waited=0
while ! mkdir "$LOCK_DIRECTORY" 2>/dev/null; do
    owner=
    started=
    [ ! -f "$LOCK_DIRECTORY/pid" ] || owner=$(sed -n '1p' "$LOCK_DIRECTORY/pid" 2>/dev/null || true)
    [ ! -f "$LOCK_DIRECTORY/pid" ] || started=$(sed -n '2p' "$LOCK_DIRECTORY/pid" 2>/dev/null || true)
    case "$owner" in
        ''|*[!0-9]*) ;;
        *)
            if ! kill -0 "$owner" 2>/dev/null; then
                case "$started" in
                    ''|*[!0-9]*) ;;
                    *)
                        now=$(date +%s)
                        if [ $((now - started)) -ge "$LOCK_STALE_SECONDS" ]; then
                            rm -f "$LOCK_DIRECTORY/pid"
                            rmdir "$LOCK_DIRECTORY" 2>/dev/null || true
                            continue
                        fi
                        ;;
                esac
            fi
            ;;
    esac
    if [ "$waited" -ge "$LOCK_WAIT_SECONDS" ]; then
        echo "Timed out waiting for portable runtime installation lock $LOCK_DIRECTORY." >&2
        exit 1
    fi
    sleep 1
    waited=$((waited + 1))
done
LOCK_OWNED=true
printf '%s\n%s\n' "$$" "$(date +%s)" >"$LOCK_DIRECTORY/pid"

FORCE=false
[ "$MODE" != repair ] || FORCE=true
BACKUP=
RUNTIME_VALID=false
if runtime_valid; then
    RUNTIME_VALID=true
fi
RUNTIME_STATE="saved environment reused"

if [ "$FORCE" = true ] || [ "$RUNTIME_VALID" = false ]; then
    if [ "$FORCE" = true ]; then
        RUNTIME_STATE="environment repaired"
    else
        RUNTIME_STATE="fresh environment created"
    fi
    CHECKSUM_PART="$CACHE_DIRECTORY/$ARCHIVE_NAME.sha256.part-$$"
    ARCHIVE_PART="$CACHE_DIRECTORY/$ARCHIVE_NAME.part-$$"
    ARCHIVE="$CACHE_DIRECTORY/$ARCHIVE_NAME"
    STAGING="$INSTALL_PARENT/.staging-$ARCHIVE_NAME-$$"
    LISTING="$CACHE_DIRECTORY/$ARCHIVE_NAME.listing-$$"
    rm -f "$CHECKSUM_PART" "$ARCHIVE_PART"
    rm -rf "$STAGING"
    echo "Preparing standalone SBK Dashboard $VERSION for $PLATFORM_ID."
    download "$BASE_URL/$ARCHIVE_NAME.sha256" "$CHECKSUM_PART" "$MAXIMUM_CHECKSUM_BYTES" || {
        echo "Unable to download the checksum for $ARCHIVE_NAME from $BASE_URL." >&2
        exit 1
    }
    EXPECTED=$(awk 'NR == 1 {print $1}' "$CHECKSUM_PART")
    case "$EXPECTED" in *[!0-9a-fA-F]*|'') echo "The published checksum for $ARCHIVE_NAME is invalid." >&2; exit 1 ;; esac
    [ "${#EXPECTED}" -eq 64 ] || { echo "The published checksum for $ARCHIVE_NAME is invalid." >&2; exit 1; }
    EXPECTED=$(printf '%s' "$EXPECTED" | tr '[:upper:]' '[:lower:]')
    if [ ! -f "$ARCHIVE" ] || [ "$(sha256_file "$ARCHIVE")" != "$EXPECTED" ]; then
        download "$BASE_URL/$ARCHIVE_NAME" "$ARCHIVE_PART" "$MAXIMUM_ARCHIVE_BYTES" || {
            echo "Unable to download $ARCHIVE_NAME from $BASE_URL." >&2
            exit 1
        }
        ARCHIVE_BYTES=$(wc -c <"$ARCHIVE_PART" | tr -d ' ')
        [ "$ARCHIVE_BYTES" -le "$MAXIMUM_ARCHIVE_BYTES" ] || {
            echo "Portable archive exceeds the $MAXIMUM_ARCHIVE_BYTES byte limit." >&2
            exit 1
        }
        ACTUAL=$(sha256_file "$ARCHIVE_PART")
        [ "$ACTUAL" = "$EXPECTED" ] || { echo "Checksum verification failed for $ARCHIVE_NAME." >&2; exit 1; }
        mv "$ARCHIVE_PART" "$ARCHIVE"
    fi
    if ! tar -tzf "$ARCHIVE" >"$LISTING" 2>/dev/null; then
        echo "Unsafe or unreadable archive $ARCHIVE_NAME." >&2
        exit 1
    fi
    if awk '$0 ~ /^\// || $0 ~ /(^|\/)\.\.($|\/)/ {unsafe=1} END {exit unsafe}' "$LISTING"; then :; else
        echo "Unsafe path found in $ARCHIVE_NAME." >&2
        exit 1
    fi
    if ! tar -tvzf "$ARCHIVE" >"$LISTING" 2>/dev/null; then
        echo "Unsafe or unreadable archive $ARCHIVE_NAME." >&2
        exit 1
    fi
    if awk 'substr($0, 1, 1) != "-" && substr($0, 1, 1) != "d" {exit 1}' "$LISTING"; then :; else
        echo "Only regular files and directories are allowed in $ARCHIVE_NAME." >&2
        exit 1
    fi
    mkdir -p "$STAGING"
    tar -xzf "$ARCHIVE" -C "$STAGING"
    [ -f "$STAGING/sbk-dashboard-$VERSION-$PLATFORM_ID/sbk-dashboard" ] || {
        echo "The portable archive does not contain the expected executable." >&2
        exit 1
    }
    chmod +x "$STAGING/sbk-dashboard-$VERSION-$PLATFORM_ID/sbk-dashboard"
    EXECUTABLE_SHA256=$(sha256_file "$STAGING/sbk-dashboard-$VERSION-$PLATFORM_ID/sbk-dashboard")
    printf '%s\n%s\n' "$EXPECTED" "$EXECUTABLE_SHA256" >"$STAGING/.installed-sha256"
    rm -f "$LISTING"
    LISTING=
    if [ -d "$INSTALL_DIRECTORY" ]; then
        BACKUP="$INSTALL_PARENT/.backup-$ARCHIVE_NAME-$$"
        mv "$INSTALL_DIRECTORY" "$BACKUP"
    fi
    if ! mv "$STAGING" "$INSTALL_DIRECTORY"; then
        [ -z "$BACKUP" ] || mv "$BACKUP" "$INSTALL_DIRECTORY"
        exit 1
    fi
    rm -f "$CHECKSUM_PART"
    CHECKSUM_PART=
    ARCHIVE_PART=
    STAGING=
fi

if [ -n "$BACKUP" ]; then
    rm -rf "$BACKUP"
fi
cleanup
trap - EXIT HUP INT TERM
export SBK_DASHBOARD_HOME="$PORTABLE_HOME"
export SBK_DASHBOARD_BOOTSTRAP_RUNTIME_KIND="standalone runtime with bundled Python"
export SBK_DASHBOARD_BOOTSTRAP_RUNTIME_STATE="$RUNTIME_STATE"
export SBK_DASHBOARD_BOOTSTRAP_RUNTIME_PATH="$INSTALL_DIRECTORY"
echo "Using standalone SBK Dashboard $VERSION from $INSTALL_DIRECTORY"
if [ "$MODE" = repair ]; then
    echo "Operating system: $(uname -s) $(uname -r) ($(uname -m); $PLATFORM_ID)"
    echo "Bootstrap runtime: $SBK_DASHBOARD_BOOTSTRAP_RUNTIME_KIND"
    echo "Runtime preparation: $RUNTIME_STATE"
    echo "Runtime location: $INSTALL_DIRECTORY"
    echo "SBK Dashboard home: $PORTABLE_HOME"
    echo "Repaired standalone SBK Dashboard $VERSION runtime."
    exit 0
fi
exec "$EXECUTABLE" "$MODE" "$@"
