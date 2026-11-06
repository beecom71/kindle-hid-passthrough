# Bluetooth audio

With the **Bluetooth audio** switch on (KOReader plugin, next to the daemon toggle), the daemon streams what the Kindle plays to a paired headset over A2DP. While the switch is on, the stock audio daemon is replaced by a mock, GStreamer playback that would go to the stock mixer is redirected into `/tmp/kindle_audio.fifo`, and the daemon encodes it to SBC. The switch is off by default; leave it off on a device where audio misbehaves.

This page covers the headset's buttons: what they send, how to map them, and how a player takes over pause and track skipping.

## Headset buttons

An audio device has no HID descriptor of its own, so the daemon gives its input node a Consumer Control descriptor and turns the AVRCP pass-through keys the headset sends into reports on it. The kernel's hid-input maps those usages to ordinary key codes:

| AVRCP key from the headset | Consumer usage | Linux key | Code |
|---|---|---|---|
| FORWARD (also UP, RIGHT) | Scan Next Track (0xB5) | `KEY_NEXTSONG` | 163 |
| BACKWARD (also DOWN, LEFT) | Scan Previous Track (0xB6) | `KEY_PREVIOUSSONG` | 165 |
| PLAY, PAUSE | Play/Pause (0xCD) | `KEY_PLAYPAUSE` | 164 |
| VOLUME_UP | Volume Increment (0xE9) | `KEY_VOLUMEUP` | 115 |
| VOLUME_DOWN | Volume Decrement (0xEA) | `KEY_VOLUMEDOWN` | 114 |

Any other key is dropped. Which gesture sends which key is up to the headset; the daemon log shows each one as it arrives (`[AVRCP] Key FORWARD pressed`), and in `/proc/bus/input/devices` the node's `KEY=` bitmap carries exactly the five codes above (`38 0 c0000 0 0 0` on the Kindle's 32-bit kernel).

Nothing happens on a key by default: the daemon only forwards it, and [kindle-button-mapper](https://github.com/zampierilucas/kindle-button-mapper-rs) runs whatever it is mapped to.

## Mapping them

From KOReader: **Settings → Network → BT Manager - HID Passthrough → Key mappings**, pick the headset, **Map a button…**, press the button, and choose from the **Audio** section:

| Action | Runs |
|---|---|
| Play/Pause any audio | `media.sh toggle` |
| Pause any audio | `media.sh pause` |
| Resume any audio | `media.sh play` |
| Next track | `media.sh next` |
| Previous track | `media.sh previous` |

The plugin writes the mapping to `/mnt/us/kindle-button-mapper/config.ini`. By hand, the same thing is:

```ini
[device.headset.buttons]
163 = /mnt/us/kindle_hid_passthrough/assets/audio-hack/media.sh next
164 = /mnt/us/kindle_hid_passthrough/assets/audio-hack/media.sh toggle
165 = /mnt/us/kindle_hid_passthrough/assets/audio-hack/media.sh previous
```

`[device.headset]` stands for the block the daemon writes for each configured device, the one whose `uniq` is the headset's address.

## media.sh

`assets/audio-hack/media.sh` takes one verb:

- `toggle`, `play`, `pause`: pause or resume what is playing. If a player holds the media session, it is asked first; otherwise, or if it declines, the daemon's universal pause does it.
- `next`, `previous`: skip a track in the player that holds the media session. Without a session they print why on stderr and exit non-zero, and nothing else happens.
- `status`: the daemon's `/status`.

The universal pause works for any application: the daemon stops draining the FIFO, the writer blocks and freezes in place, and resuming picks up at the same sample. `/media/toggle`, `/media/play` and `/media/pause` answer with `{"ok": true, "paused": ...}`. What the daemon cannot do is skip a track, because only the player knows what the next one is. That is what the media session is for.

## Media session

A player that is playing registers itself by writing one line to `/tmp/kindle_media_session`: the absolute path of an executable handler under `/mnt/us/`. The last player to write owns the session. Write it atomically, a temporary file in `/tmp` and then `mv` over the session, so `media.sh` never reads half a line:

```sh
tmp=$(mktemp /tmp/kindle_media_session.XXXXXX) &&
    echo /mnt/us/myplayer/media-handler.sh > "$tmp" &&
    mv -f "$tmp" /tmp/kindle_media_session
```

When it stops, the player removes the file only if it still names its own handler, so it does not end the session of a player that took over since:

```sh
[ "$(cat /tmp/kindle_media_session 2>/dev/null)" = /mnt/us/myplayer/media-handler.sh ] &&
    rm -f /tmp/kindle_media_session
```

`media.sh` ignores a session that is empty, is not an absolute path, is outside `/mnt/us/`, has a `.` or `..` segment, or does not name an executable file, and says so on stderr. An ignored session counts as no session.

### The handler

`media.sh` runs it as `handler VERB`, with `VERB` one of `next`, `previous`, `toggle`, `play` and `pause`.

- Exit 0 means the handler took care of it.
- Any other exit means it did not: a verb it does not implement or does not recognise, or its player is gone. `toggle`, `play` and `pause` then fall back to the universal pause; `next` and `previous` do nothing and `media.sh` exits with the handler's status.
- It runs inside a button press, so it has to return quickly: signal the player and leave.
- It must not call `media.sh`, which would call the handler again. To let the universal pause handle a verb, exit non-zero.

A sketch, with `myplayer-ctl` standing for whatever control the player offers:

```sh
#!/bin/sh
# A session left behind by a player that died must not swallow the buttons.
pid=$(cat /tmp/myplayer.pid 2>/dev/null) && kill -0 "$pid" 2>/dev/null || exit 1
case "$1" in
    next|previous|toggle|play|pause) exec /mnt/us/myplayer/myplayer-ctl "$1" ;;
esac
exit 1
```

Sending pause to the player first is what keeps the two pauses from disagreeing. With the headset button on the universal pause and the player's own pause on screen, pausing one way and resuming the other left the daemon holding the FIFO while the player believed it was playing, which is silence.

For tests, `KINDLE_HID_API`, `KINDLE_MEDIA_SESSION` and `KINDLE_MEDIA_HANDLER_DIR` override the daemon URL, the session file and the handler directory; `tests/test_media_sh.py` uses them.
