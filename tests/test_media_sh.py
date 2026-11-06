"""assets/audio-hack/media.sh: the media session and the universal pause fallback.

Runs the script under the system sh and, when it is installed, under BusyBox
sh, the shell the Kindle has. The daemon is a local HTTP server that records
the paths asked of it; the session file and the handler directory live in a
temporary directory through the overrides the script reads for tests.
"""
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

MEDIA_SH = Path(__file__).resolve().parent.parent / "assets" / "audio-hack" / "media.sh"

SHELLS = [["sh"]]
if shutil.which("busybox"):
    SHELLS.append(["busybox", "sh"])


class FakeDaemon:
    """Answers every GET with a fixed body and remembers the path."""

    BODY = b'{"paused": true}'

    def __init__(self):
        self.paths = []
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                daemon.paths.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(FakeDaemon.BODY)))
                self.end_headers()
                self.wfile.write(FakeDaemon.BODY)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        # A short poll keeps shutdown() from costing half a second per test.
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def daemon():
    d = FakeDaemon()
    yield d
    d.close()


class Env:
    def __init__(self, tmp_path, daemon, shell):
        self.tmp = tmp_path
        self.shell = shell
        self.daemon = daemon
        self.root = tmp_path / "us"
        self.root.mkdir()
        self.session = tmp_path / "kindle_media_session"
        self.log = tmp_path / "handler_calls"

    def handler(self, exit_code=0, name="handler.sh", executable=True, where=None):
        """A handler that logs "<name> <verb>" and exits with exit_code."""
        path = (where or self.root) / name
        path.write_text(f'#!/bin/sh\necho "{name} $1" >> "{self.log}"\nexit {exit_code}\n')
        path.chmod(0o755 if executable else 0o644)
        return path

    def register(self, line, newline=True):
        self.session.write_text(f"{line}\n" if newline else str(line))

    def calls(self):
        """(handler name, verb) for every handler run, in order."""
        if not self.log.exists():
            return []
        return [tuple(line.split()) for line in self.log.read_text().splitlines()]

    def run(self, *args):
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "KINDLE_HID_API": self.daemon.url,
            "KINDLE_MEDIA_SESSION": str(self.session),
            "KINDLE_MEDIA_HANDLER_DIR": str(self.root),
        }
        return subprocess.run([*self.shell, str(MEDIA_SH), *args], env=env,
                              capture_output=True, text=True, timeout=20, check=False)


@pytest.fixture(params=SHELLS, ids=lambda shell: " ".join(shell))
def media(request, tmp_path, daemon):
    return Env(tmp_path, daemon, request.param)


# --- next / previous --------------------------------------------------------

@pytest.mark.parametrize("verb", ["next", "previous"])
def test_skip_goes_to_the_session_handler(media, verb):
    media.register(media.handler())
    result = media.run(verb)
    assert result.returncode == 0, result.stderr
    assert media.calls() == [("handler.sh", verb)]
    assert media.daemon.paths == []


@pytest.mark.parametrize("verb", ["next", "previous"])
def test_skip_without_a_session_fails_and_does_nothing(media, verb):
    result = media.run(verb)
    assert result.returncode != 0
    assert "no media session" in result.stderr
    assert result.stdout == ""
    assert media.daemon.paths == []


def test_skip_declined_by_the_handler_has_no_fallback(media):
    media.register(media.handler(exit_code=3))
    result = media.run("next")
    assert result.returncode == 3
    assert "did not handle next" in result.stderr
    assert media.calls() == [("handler.sh", "next")]
    assert media.daemon.paths == []


def test_session_line_without_trailing_newline(media):
    media.register(media.handler(), newline=False)
    assert media.run("next").returncode == 0
    assert media.calls() == [("handler.sh", "next")]


def test_last_writer_owns_the_session(media):
    media.register(media.handler(name="first.sh"))
    media.register(media.handler(name="second.sh"))
    assert media.run("previous").returncode == 0
    assert media.calls() == [("second.sh", "previous")]


# Each builds a session that must not run anything. The ones that point at a
# real executable outside the handler directory would show up in calls().
INVALID = {
    "relative path": lambda m: (m.handler(), "us/handler.sh")[1],
    "outside the handler directory": lambda m: m.handler(where=m.tmp),
    "dot dot segment out of it": lambda m: (m.handler(where=m.tmp), f"{m.root}/../handler.sh")[1],
    "missing file": lambda m: m.root / "gone.sh",
    "not executable": lambda m: m.handler(executable=False),
    "a directory": lambda m: m.root,
    "empty session": lambda m: "",
}


@pytest.fixture(params=sorted(INVALID))
def invalid_session(request, media):
    media.register(INVALID[request.param](media))
    return media


def test_skip_ignores_an_invalid_session(invalid_session):
    result = invalid_session.run("next")
    assert result.returncode != 0
    assert "ignoring media session" in result.stderr
    assert invalid_session.calls() == []
    assert invalid_session.daemon.paths == []


def test_pause_falls_back_on_an_invalid_session(invalid_session):
    result = invalid_session.run("toggle")
    assert result.returncode == 0
    assert "ignoring media session" in result.stderr
    assert invalid_session.calls() == []
    assert invalid_session.daemon.paths == ["/media/toggle"]


# --- toggle / play / pause ----------------------------------------------------

@pytest.mark.parametrize("verb", ["toggle", "play", "pause"])
def test_pause_handled_by_the_player_skips_the_daemon(media, verb):
    media.register(media.handler(exit_code=0))
    result = media.run(verb)
    assert result.returncode == 0, result.stderr
    assert media.calls() == [("handler.sh", verb)]
    assert media.daemon.paths == []


@pytest.mark.parametrize("verb", ["toggle", "play", "pause"])
def test_pause_declined_by_the_player_uses_the_universal_pause(media, verb):
    media.register(media.handler(exit_code=1))
    result = media.run(verb)
    assert result.returncode == 0, result.stderr
    assert media.calls() == [("handler.sh", verb)]
    assert media.daemon.paths == [f"/media/{verb}"]
    assert result.stdout == FakeDaemon.BODY.decode()


@pytest.mark.parametrize("verb", ["toggle", "play", "pause"])
def test_pause_without_a_session_uses_the_universal_pause(media, verb):
    result = media.run(verb)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert media.daemon.paths == [f"/media/{verb}"]
    assert result.stdout == FakeDaemon.BODY.decode()


# --- status and usage -----------------------------------------------------------

def test_status_only_asks_the_daemon(media):
    media.register(media.handler())
    result = media.run("status")
    assert result.returncode == 0
    assert media.daemon.paths == ["/status"]
    assert media.calls() == []


@pytest.mark.parametrize("args", [[], ["skip"], ["next", "extra"]])
def test_usage(media, args):
    result = media.run(*args)
    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert media.daemon.paths == []
    assert media.calls() == []


# --- the registration the header documents ------------------------------------

def documented_recipe():
    """The indented shell lines that follow the mktemp line in the header."""
    lines = MEDIA_SH.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if "tmp=$(mktemp" in line)
    recipe = []
    for line in lines[start:]:
        if not line.startswith("#   "):
            break
        recipe.append(line[1:].strip())
    return "\n".join(recipe)


def test_documented_registration_is_atomic_and_works(media):
    handler = media.handler()
    recipe = (documented_recipe()
              .replace("/tmp/kindle_media_session", str(media.session))
              .replace("/mnt/us/myplayer/media-handler.sh", str(handler)))
    subprocess.run([*media.shell, "-c", recipe], check=True, timeout=20)
    assert media.session.read_text() == f"{handler}\n"
    assert list(media.tmp.glob("kindle_media_session.*")) == []
    assert media.run("next").returncode == 0
    assert media.calls() == [("handler.sh", "next")]
