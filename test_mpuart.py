#!/usr/bin/env python3
"""End to end test of mpuart against a socat pty pair. No hardware needed.

    ./test_mpuart.py

AF_UNIX paths stop at 108 bytes, so the run directory is short on purpose.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MPUART = os.path.join(HERE, "mpuart")
HELLO = b"\x01mpuart1 "
fails = []
ran = 0


def check(name, ok, got=""):
    global ran
    ran += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)
        if got:
            print("     got: %r" % got)


class Rig:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="/tmp/mpu")   # short, see module docstring
        self.env = dict(os.environ, MPUART_DIR=self.dir)
        self.a = os.path.join(self.dir, "p")
        self.b = os.path.join(self.dir, "q")
        self.socat = self.serve = self.dev = None
        self.got = bytearray()          # every byte the device end was sent

    def start_pty(self):
        self.socat = subprocess.Popen(
            ["socat", "pty,raw,echo=0,link=" + self.a, "pty,raw,echo=0,link=" + self.b],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if os.path.exists(self.a) and os.path.exists(self.b):
                return
            time.sleep(0.1)
        raise SystemExit("socat did not make the pty pair")

    def start_device(self):
        """Other end of the pair: echoes back what it is sent, prefixed."""
        self.dev = os.open(os.path.realpath(self.b), os.O_RDWR | os.O_NOCTTY)
        def loop(fd):
            while True:
                try:
                    d = os.read(fd, 4096)
                except OSError:
                    return
                if not d:
                    return
                self.got += d
                os.write(fd, b"got " + d.replace(b"\r", b"\n"))
        threading.Thread(target=loop, args=(self.dev,), daemon=True).start()

    def start_server(self, *extra):
        self.serve = subprocess.Popen(
            [MPUART, "serve", self.a, "115200"] + list(extra),
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sock = os.path.join(self.dir, "p.sock")
        for _ in range(50):
            if os.path.exists(sock):
                time.sleep(0.2)
                return
            time.sleep(0.1)
        raise SystemExit("server did not bind")

    def client(self, name, **opts):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(os.path.join(self.dir, "p.sock"))
        parts = ["name=" + name] + ["%s=%d" % (k, int(v)) for k, v in opts.items()]
        s.sendall(HELLO + " ".join(parts).encode() + b"\n")
        time.sleep(0.3)
        return s

    def stop(self):
        for p in (self.serve, self.socat):
            if p:
                p.terminate()
                p.wait(5)
        shutil.rmtree(self.dir, ignore_errors=True)


def load_mpuart():
    """The script as a module, to check its helpers directly."""
    import importlib.util
    spec = importlib.util.spec_from_loader("mp", None)
    m = importlib.util.module_from_spec(spec)
    src = open(MPUART).read().replace('if __name__ == "__main__":\n    main()', "")
    exec(src, m.__dict__)
    return m


def check_discovery():
    m = load_mpuart()
    ports = m.system_ports()
    check("the phantom ttyS lines are left out of discovery",
          not any("/dev/ttyS" in p[1] for p in ports), [p[1] for p in ports])
    check("a discovered port comes with a stable path",
          all(p[0].startswith("/dev/serial/by-id/") or p[0] == p[1] for p in ports), ports)

    menu = m.port_menu([("/dev/serial/by-id/usb-A-if01-port0", "/dev/ttyUSB0", "Blaster"),
                        ("/dev/serial/by-id/usb-B-if00-port0", "/dev/ttyUSB1", "FT232R")])
    check("several ports print a menu with a command to copy",
          "2 serial ports" in menu and "mpuart serve /dev/serial/by-id/usb-B-if00-port0" in menu
          and "ttyUSB1" in menu, menu)


def check_list(r):
    """list reports what the daemon is, not just that it exists."""
    watcher = r.client("nosy")
    time.sleep(0.4)
    out = subprocess.run([MPUART, "list"], env=r.env, capture_output=True, text=True).stdout
    check("list names the baud rate", "115200 baud" in out, out)
    check("list names the device behind the socket", r.a in out, out)
    check("list names the log file", "p.log" in out, out)
    check("list says who is attached", "nosy" in out, out)
    check("a pty has no hardware name, and list says so", "unknown hardware" in out, out)

    log = open(os.path.join(r.dir, "p.log")).read()
    check("asking is not an attach, and leaves nothing in the log",
          "list attached" not in log and "list detached" not in log, log[-200:])
    watcher.close()

    m = load_mpuart()
    m.system_ports = lambda: [("/dev/x", "/dev/null", "Blaster III - Blaster III")]
    check("a doubled hardware name is said once", m.describe("/dev/null") == "Blaster III")
    m.system_ports = lambda: [("/dev/x", "/dev/null", "FTDI - FT232R USB UART")]
    check("a two part name is kept whole",
          m.describe("/dev/null") == "FTDI - FT232R USB UART")


def check_naming(r):
    """--as renames the socket, the log and what list shows."""
    second = Rig()
    second.dir, second.env = r.dir, r.env
    second.a = os.path.join(r.dir, "u")
    second.b = os.path.join(r.dir, "v")
    try:
        second.start_pty()
        second.serve = subprocess.Popen(
            [MPUART, "serve", second.a, "115200", "--as", "bench"],
            env=r.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if os.path.exists(os.path.join(r.dir, "bench.sock")):
                break
            time.sleep(0.1)
        check("--as names the socket", os.path.exists(os.path.join(r.dir, "bench.sock")))
        check("--as names the log too", os.path.exists(os.path.join(r.dir, "bench.log")))
        out = subprocess.run([MPUART, "list"], env=r.env, capture_output=True, text=True)
        check("and list shows that name", "bench" in out.stdout, out.stdout)
    finally:
        for p in (second.serve, second.socat):
            if p:
                p.terminate()
                p.wait(5)
        for f in ("bench.sock", "bench.log"):
            try:
                os.unlink(os.path.join(r.dir, f))
            except OSError:
                pass


def cpu_ticks(pid):
    """User plus system time, in clock ticks. 100 ticks is one core second."""
    f = open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()
    return int(f[11]) + int(f[12])


def drain(s, secs=0.5):
    s.setblocking(False)
    out, end = b"", time.time() + secs
    while time.time() < end:
        try:
            out += s.recv(65536)
        except BlockingIOError:
            time.sleep(0.02)
        except OSError:
            break
    s.setblocking(True)
    return out


def check_picking(r):
    """The port may be left out when only one is served."""
    out = subprocess.run([MPUART, "send", "version", "-w", "1.5", "-e", "got version"],
                         env=r.env, capture_output=True, timeout=20)
    check("one served port needs no name", out.returncode == 0, out.stdout + out.stderr)

    empty = dict(r.env, MPUART_DIR=tempfile.mkdtemp(prefix="/tmp/mpu"))
    out = subprocess.run([MPUART, "attach"], env=empty, capture_output=True,
                         text=True, timeout=20)
    check("no served port says how to start one",
          out.returncode != 0 and "mpuart serve" in out.stderr, out.stderr)
    shutil.rmtree(empty["MPUART_DIR"], ignore_errors=True)

    second = Rig()
    second.dir = r.dir                       # same run directory, second port
    second.env = r.env
    second.a = os.path.join(r.dir, "r")
    second.b = os.path.join(r.dir, "s")
    try:
        second.start_pty()
        second.start_server()
        out = subprocess.run([MPUART, "attach"], env=r.env, capture_output=True,
                             text=True, timeout=20)
        check("two served ports ask which",
              out.returncode != 0 and "p" in out.stderr and "r" in out.stderr, out.stderr)
    finally:
        for p in (second.serve, second.socat):
            if p:
                p.terminate()
                p.wait(5)


def check_history(r):
    """Replay starts at a line boundary and honours both limits."""
    for i in range(40):
        os.write(r.dev, b"line %02d\n" % i)
        time.sleep(0.02)
    time.sleep(0.5)

    seen = drain(r.client("all"))
    body = seen.split(b"--")[0]
    check("replay never starts mid line",
          b"line 00\n" in body and not body.lstrip().startswith(b"ine"), body[:80])
    check("replay holds what the daemon was told to keep",
          body.count(b"\nline ") + body.startswith(b"line ") >= 30, body[:80])

    seen = drain(r.client("few", hist=5))
    body = seen.split(b"-- /")[0]
    got = [l for l in body.split(b"\n") if l.startswith(b"line ")]
    check("a client may ask for fewer lines", got == [b"line %02d" % i for i in range(35, 40)], got)

    seen = drain(r.client("none", hist=0))
    check("and for none at all", b"line 3" not in seen, seen)


def check_history_limit():
    """The daemon keeps only the lines it was told to."""
    r = Rig()
    try:
        r.start_pty()
        r.start_device()
        r.start_server("--history", "10")
        for i in range(30):
            os.write(r.dev, b"row %02d\n" % i)
            time.sleep(0.02)
        time.sleep(0.5)
        body = drain(r.client("late")).split(b"-- /")[0]
        got = [l for l in body.split(b"\n") if l.startswith(b"row ")]
        check("--history 10 keeps ten lines", got == [b"row %02d" % i for i in range(20, 30)], got)
    finally:
        r.stop()


def check_device_only(r):
    """A binary stream needs the device bytes and nothing mpuart adds to them."""
    quiet = r.client("quiet", notes=0, tags=0)
    loud = r.client("loud")
    drain(quiet); drain(loud)
    typer = r.client("typer2")
    typer.sendall(b"hello\r\n")
    time.sleep(0.6)
    quiet_saw, loud_saw = drain(quiet), drain(loud)
    check("a device-only client is not told what the others type",
          b"[typer2]" not in quiet_saw, quiet_saw)
    check("and is not told who came or went", b"--" not in quiet_saw, quiet_saw)
    check("but it still gets what the device said", b"got hello" in quiet_saw, quiet_saw)
    check("while everyone else is told both",
          b"[typer2] hello" in loud_saw and b"typer2 attached" in loud_saw, loud_saw)

    p = subprocess.Popen([MPUART, "attach", r.a, "-r", "-n", "cli", "--device-only"],
                         env=r.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    time.sleep(0.8)
    typer.sendall(b"again\r\n")
    time.sleep(0.8)
    p.terminate()
    out = p.communicate(timeout=5)[0]
    check("--device-only does the same from the command line",
          b"got again" in out and b"[typer2]" not in out and b"-- " not in out, out)
    quiet.close(); loud.close(); typer.close()


def check_eol(r):
    """Enter is one line ending on the wire and one tag on the screen."""
    watcher = r.client("watcher")
    drain(watcher)
    del r.got[:]
    typer = r.client("typer")
    typer.sendall(b"ping\r\n")
    time.sleep(0.6)
    seen = drain(watcher)
    check("CRLF is tagged once, not as a second empty line",
          seen.count(b"[typer]") == 1 and b"[typer] ping" in seen, seen)

    out = subprocess.run([MPUART, "send", "--eol", "cr", "bare"], env=r.env,
                         capture_output=True, timeout=20)
    check("--eol cr sends CR alone", b"bare\r" in bytes(r.got)
          and b"bare\r\n" not in bytes(r.got), bytes(r.got))
    del r.got[:]
    subprocess.run([MPUART, "send", "crlf"], env=r.env, capture_output=True, timeout=20)
    check("send ends a line with CRLF by default", b"crlf\r\n" in bytes(r.got), bytes(r.got))
    watcher.close(); typer.close()


def check_terminal(r):
    """Attach on a real terminal. Bare LF from the device must reach the
    screen as CRLF, and Enter must still leave as CR."""
    import pty
    master, slave = pty.openpty()
    p = subprocess.Popen([MPUART, "attach", r.a, "-n", "term", "--no-color"],
                         env=r.env, stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    time.sleep(1.0)
    os.read(master, 65536)                       # greeting and backscroll
    os.write(r.dev, b"one\ntwo\n")               # device sends bare LF
    time.sleep(0.6)
    seen = os.read(master, 65536)
    check("bare LF from the device reaches the screen as CRLF",
          b"one\r\ntwo\r\n" in seen, seen)

    del r.got[:]
    os.write(master, b"x\r")                     # a keypress and Enter
    time.sleep(0.6)
    check("Enter reaches the device as CRLF, as miniterm sends it",
          b"x\r\n" in bytes(r.got), bytes(r.got))

    del r.got[:]
    os.write(master, b"\x7f")                    # the DEL key
    time.sleep(0.6)
    check("DEL reaches the device as backspace",
          b"\x08" in bytes(r.got) and b"\x7f" not in bytes(r.got), bytes(r.got))

    os.write(master, b"\x1d")                    # Ctrl-]
    p.wait(5)
    check("Ctrl-] quits", p.returncode == 0)
    os.close(master)


def main():
    r = Rig()
    try:
        r.start_pty()
        r.start_device()
        r.start_server("--tcp", "127.0.0.1:5698")
        os.write(r.dev, b"boot banner\r\n")
        time.sleep(0.3)

        alice, bob, eve = r.client("alice"), r.client("bob"), r.client("eve", ro=1)
        drain(alice); drain(bob); drain(eve)

        alice.sendall(b"reset\r")
        time.sleep(0.5)
        seen = drain(bob)
        check("others see who typed", b"[alice] reset" in seen, seen)
        check("device answer reaches them", b"got reset" in seen, seen)
        mine = drain(alice)
        check("you are not tagged to yourself", b"[alice]" not in mine, mine)

        bob.sendall(b"half")
        time.sleep(1.4)
        seen = drain(alice)
        check("a half typed line is tagged when idle", b"[bob] half" in seen, seen)

        eve.sendall(b"nope\r")
        time.sleep(0.4)
        seen = drain(eve)
        check("read only clients cannot type", b"read only" in seen, seen)
        check("and nothing reached the device", b"got nope" not in drain(alice))

        late = r.client("late")
        seen = drain(late)
        check("a late client gets backscroll", b"boot banner" in seen, seen)
        check("and is told who is here", b"alice" in seen and b"bob" in seen, seen)

        out = subprocess.run([MPUART, "send", r.a, "version", "-w", "1.5", "-e", "got version"],
                             env=r.env, capture_output=True, timeout=20)
        check("send prints the answer", b"got version" in out.stdout, out.stdout + out.stderr)
        check("send exits clean when it sees what it expects", out.returncode == 0, out.stderr)

        out = subprocess.run([MPUART, "send", r.a, "nothing", "-w", "1", "-e", "never appears"],
                             env=r.env, capture_output=True, timeout=20)
        check("send fails when it does not", out.returncode != 0, out.stderr)

        t = socket.create_connection(("127.0.0.1", 5698))
        t.sendall(HELLO + b"name=remote\n")
        time.sleep(0.4); drain(t)
        t.sendall(b"over tcp\r")
        time.sleep(0.5)
        seen = drain(alice)
        check("tcp and unix clients share one port", b"[remote] over tcp" in seen, seen)

        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.connect(os.path.join(r.dir, "p.sock"))
        raw.sendall(b"plain\r\n")
        time.sleep(0.5)
        seen = drain(alice)
        check("a client with no hello still works", b"got plain" in seen, seen)

        r.socat.terminate(); r.socat.wait(5)
        check("the device node is gone, as after an unplug",
              not os.path.exists(r.a), r.a)
        busy = cpu_ticks(r.serve.pid)
        time.sleep(2.5)
        seen = drain(alice, 1.0)
        check("clients are told the device went away", b"lost" in seen, seen)
        alice.sendall(b"into the void\r")
        time.sleep(0.4)
        seen = drain(alice)
        check("writes are dropped, not queued", b"dropped" in seen, seen)
        spent = cpu_ticks(r.serve.pid) - busy
        check("reopening does not spin the cpu", spent < 20, "%d ticks in ~3s" % spent)
        r.start_pty(); r.start_device()
        time.sleep(3.0)
        seen = drain(alice, 1.0)
        check("and it reconnects on its own", b"open" in seen, seen)
        del r.got[:]
        alice.sendall(b"after the outage\r\n")
        time.sleep(0.6)
        check("a client that sat through it can type again",
              b"after the outage" in bytes(r.got), bytes(r.got))

        check_terminal(r)
        check_history(r)
        check_eol(r)
        check_device_only(r)
        check_picking(r)
        check_naming(r)
        check_list(r)

        log = open(os.path.join(r.dir, "p.log")).read()
        check("the log names who typed", "> alice: reset" in log)
        check("the log has device output", "< boot banner" in log)
        check("the log has attach and detach", "* alice attached" in log)

        out = subprocess.run([MPUART, "list"], env=r.env, capture_output=True, text=True)
        check("list finds the port and the dead one",
              out.stdout.startswith("p ") and "stale socket" in out.stdout, out.stdout)

        for s in (alice, bob, eve, late, t, raw):
            s.close()
    finally:
        r.stop()

    check_history_limit()
    check_discovery()

    print("\n%d checks, %d failed" % (ran, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
