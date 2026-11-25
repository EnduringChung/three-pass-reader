"""The desktop launcher and its icon.

Nothing here starts a server: the parts worth testing are the ones that make the icon trustworthy —
finding the process on the port, and shipping a valid multi-size .ico that Explorer can draw.
"""
import struct
import subprocess

import launcher

FAKE_NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:8765           0.0.0.0:0              LISTENING       4242
  TCP    127.0.0.1:8765         127.0.0.1:60123        ESTABLISHED     4242
  TCP    0.0.0.0:445            0.0.0.0:0              LISTENING       4
  TCP    [::]:8765              [::]:0                 LISTENING       4242
"""


def test_pids_on_port_finds_only_the_listener(monkeypatch):
    class Done:
        stdout = FAKE_NETSTAT

    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: Done())
    assert launcher.pids_on_port() == ["4242"], "established client sockets must not count"


def test_pids_on_port_empty_when_nothing_listens(monkeypatch):
    class Done:
        stdout = "  TCP    10.0.0.5:8765   1.1.1.1:443   ESTABLISHED  99\n"

    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: Done())
    assert launcher.pids_on_port() == []


def test_icon_is_a_valid_multi_size_ico():
    path = launcher.ROOT / "app.ico"
    assert path.exists(), "the shortcut points at this file; it must be committed"
    blob = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", blob, 0)
    assert (reserved, kind) == (0, 1)
    sizes = []
    for i in range(count):
        w, h, colours, res, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", blob, 6 + 16 * i)
        sizes.append(256 if w == 0 else w)
        assert bits == 32 and planes == 1
        assert blob[offset:offset + 8] == b"\x89PNG\r\n\x1a\n", "entry must be a PNG payload"
        assert offset + size <= len(blob)
    assert sizes == [16, 24, 32, 48, 64, 128, 256], "Explorer picks a size; all seven must exist"


def test_icon_generator_is_reproducible(tmp_path):
    import make_icon

    out, size = make_icon.build(tmp_path / "generated.ico")
    assert size > 1000
    again = tmp_path / "again.ico"
    make_icon.build(again)
    assert again.read_bytes() == out.read_bytes(), "same source must produce the same icon"


def test_launcher_status_reports_stopped_on_a_dead_port(monkeypatch):
    monkeypatch.setattr(launcher, "port_open", lambda *a, **k: False)
    assert launcher.main(["--status"]) == 1
    assert launcher.main(["--status"]) == 1


def test_second_click_opens_the_browser_without_starting_anything(monkeypatch):
    """A double click must not spawn a second server: it just shows the running one."""
    monkeypatch.setattr(launcher, "port_open", lambda *a, **k: True)
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))

    def boom(*a, **k):
        raise AssertionError("start() must not run when the port is already open")

    monkeypatch.setattr(launcher, "start", boom)
    assert launcher.main([]) == 0
    assert opened == [launcher.URL]


def test_stop_returns_false_when_nothing_runs(monkeypatch):
    monkeypatch.setattr(launcher, "pids_on_port", lambda: [])
    assert launcher.stop() is False
