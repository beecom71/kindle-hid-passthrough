#!/bin/sh
# ==============================================================================
# Media control for the daemon's audio path.
#
#   media.sh toggle|play|pause    pause or resume what is playing
#   media.sh next|previous        skip a track in the player that owns the session
#   media.sh status               the daemon's /status
#
# Universal pause: the daemon stops draining /tmp/kindle_audio.fifo, the writer
# blocks and freezes in place, and resuming picks up at the same sample. It
# works for any application with no cooperation from it. The daemon cannot skip
# a track, though: only the player knows what the next one is.
#
# Media session
# -------------
# A player that is playing registers itself by writing one line to
# /tmp/kindle_media_session: the absolute path of an executable handler under
# /mnt/us/. The last player to write owns the session. Write it atomically, so a
# reader never sees half a line -- a temporary file in /tmp, then mv over:
#
#   tmp=$(mktemp /tmp/kindle_media_session.XXXXXX) &&
#       echo /mnt/us/myplayer/media-handler.sh > "$tmp" &&
#       mv -f "$tmp" /tmp/kindle_media_session
#
# On the way out a player removes the file only if it still names its handler,
# so it does not end the session of a player that took over since.
#
# The handler is called as `handler VERB`, VERB one of next, previous, toggle,
# play and pause. It must return quickly: it runs in the button press. Exit 0
# means it handled the verb. Anything else means it did not -- a verb it does
# not implement or does not know, or its player has gone away -- and media.sh
# falls back: toggle, play and pause go to the universal pause, next and
# previous do nothing and media.sh exits non-zero. A handler must not call
# media.sh itself, which would call the handler again.
#
# A session that does not validate (empty, not an absolute path, outside
# /mnt/us/, a . or .. segment, not an executable file) is ignored as if there
# were none. That is reported on stderr; next and previous then exit non-zero,
# and toggle, play and pause use the universal pause.
#
# Going to the player first keeps both pauses from disagreeing. With the button
# on the universal pause and the player's own pause on screen, pausing one way
# and resuming the other left the daemon holding the FIFO while the player
# believed it was playing: silence.
#
# Map the headset buttons from the KOReader plugin (Key mappings, Audio) or by
# hand in /mnt/us/kindle-button-mapper/config.ini. The audio device's node sends
# KEY_NEXTSONG (163), KEY_PLAYPAUSE (164) and KEY_PREVIOUSSONG (165):
#   [device.headset.buttons]
#   163 = /mnt/us/kindle_hid_passthrough/assets/audio-hack/media.sh next
#   164 = /mnt/us/kindle_hid_passthrough/assets/audio-hack/media.sh toggle
#   165 = /mnt/us/kindle_hid_passthrough/assets/audio-hack/media.sh previous
#
# For tests, KINDLE_HID_API, KINDLE_MEDIA_SESSION and KINDLE_MEDIA_HANDLER_DIR
# override the daemon URL, the session file and the handler directory.
# ==============================================================================

API="${KINDLE_HID_API:-http://127.0.0.1:8321}"
SESSION="${KINDLE_MEDIA_SESSION:-/tmp/kindle_media_session}"
HANDLER_DIR="${KINDLE_MEDIA_HANDLER_DIR:-/mnt/us}"
HANDLER_DIR="${HANDLER_DIR%/}"

usage() {
    echo "usage: $(basename "$0") {toggle|play|pause|next|previous|status}" >&2
    exit 2
}

# Print the session's handler, or explain on stderr why there is none.
session_handler() {
    if [ ! -e "$SESSION" ]; then
        echo "media.sh: no media session: no player has registered in $SESSION" >&2
        return 1
    fi
    handler=
    # read fails on a last line without a newline but still sets it.
    IFS= read -r handler < "$SESSION" || [ -n "$handler" ] || handler=
    if [ -z "$handler" ]; then
        echo "media.sh: ignoring media session: $SESSION is empty" >&2
        return 1
    fi
    case "$handler" in
        /*) ;;
        *)
            echo "media.sh: ignoring media session: '$handler' is not an absolute path" >&2
            return 1
            ;;
    esac
    # The directory check below compares text, so a dot segment could walk out.
    case "$handler/" in
        */./*|*/../*)
            echo "media.sh: ignoring media session: '$handler' has a . or .. segment" >&2
            return 1
            ;;
    esac
    case "$handler" in
        "$HANDLER_DIR"/?*) ;;
        *)
            echo "media.sh: ignoring media session: '$handler' is outside $HANDLER_DIR/" >&2
            return 1
            ;;
    esac
    if [ ! -f "$handler" ] || [ ! -x "$handler" ]; then
        echo "media.sh: ignoring media session: '$handler' is not an executable file" >&2
        return 1
    fi
    printf '%s\n' "$handler"
}

daemon() {
    if command -v wget >/dev/null 2>&1; then
        wget -qO- "$API$1"
    elif command -v curl >/dev/null 2>&1; then
        curl -s "$API$1"
    else
        echo "media.sh: neither wget nor curl available" >&2
        exit 1
    fi
}

[ $# -eq 1 ] || usage

case "$1" in
    next|previous)
        handler=$(session_handler) || exit 1
        "$handler" "$1" && exit 0
        status=$?
        echo "media.sh: $handler did not handle $1 (exit $status)" >&2
        exit "$status"
        ;;
    toggle|play|pause)
        # No session is the common case, not an error: stay quiet about it.
        if [ -e "$SESSION" ] && handler=$(session_handler); then
            "$handler" "$1" && exit 0
        fi
        daemon "/media/$1"
        ;;
    status)
        daemon "/status"
        ;;
    *)
        usage
        ;;
esac
