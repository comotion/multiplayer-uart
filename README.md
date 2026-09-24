# mpuart

One serial port, many people. `pyserial-miniterm` holds a UART open for one
user, so a colleague or an agent that wants to look must wait for you to quit.
`mpuart` puts a daemon on the port instead. Everyone else attaches to a socket,
sees the same stream, and sees what the others type. Everything is logged.

Python 3 and `pyserial`. One file, no install.

## Start it

Once per port. It keeps the port until you stop it:

    mpuart serve                  # the only serial port on the machine
    mpuart serve ttyUSB0 115200   # or name one. 115200 is the default

With no device it takes the only serial port there is, by its
`/dev/serial/by-id` name, which survives a replug. With several it prints them
and waits for you to pick:

    mpuart: 2 serial ports, name one:

      ttyUSB0      USB Blaster III
          mpuart serve /dev/serial/by-id/usb-Altera_USB_Blaster_III_FTAL4DUL-if01-port0

      ttyUSB1      FT232R USB UART
          mpuart serve /dev/serial/by-id/usb-FTDI_FT232R_A50285BI-if00-port0

The socket is named after the tty, `ttyUSB0`, however long the path is.
`--as diamond` names it yourself, which is worth doing with more than one
device on the bench.

## Use it

    mpuart attach                 # interactive. Ctrl-] quits
    mpuart attach -r              # watch only, cannot type
    mpuart send 'info' -w 2       # one shot, prints what came back
    mpuart list                   # which ports are served, and what they are

Leave the port out and the only one being served is used. Name it when there
are several: `mpuart attach ttyUSB0`.

What the others type arrives tagged. Your own typing comes back the way the
device echoes it:

    [anna] reset
    Booting...

On attach you get the last 2000 lines, then a line naming the port, the baud
rate and who else is there.

`mpuart list` asks each daemon what it is holding:

    ttyUSB0    USB Blaster III
               115200 baud, connected, here: kacper, claude
               /dev/serial/by-id/usb-Altera_USB_Blaster_III_FTAL4DUL-if01-port0
               /tmp/mpuart/ttyUSB0.log

Asking is not attaching. It leaves nothing in the log and the others see nothing.

## The log

Always on, at `/tmp/mpuart/ttyUSB0.log`. Device output, who typed what, and who
came and went:

    2026-09-15 11:30:05.482 > kacper: info
    2026-09-15 11:30:06.221 < CPU freq: 125000000 Hz
    2026-09-15 11:30:08.710 * anna attached

## For agents

- Run `mpuart list` first. If the port is already served, attach to it. Never
  start a second `serve` on the same port, and never kill someone else's daemon.
- Use `send`, not `attach`. `mpuart send 'reset soft' -e 'Boot done' -w 60`
  returns as soon as the pattern appears, and exits 1 if it never does.
- Pass `-n claude` or set `MPUART_NAME`, so the log says which agent did it.
- To read what happened before you arrived, read the log file. Do not attach
  just to collect output.
- A person may be typing at the same time. Their input is in your stream too,
  tagged. Do not treat it as device output.

## Options

`serve`

    --as NAME          what to call it in the socket, the log and mpuart list
    --history N        replay N lines, or bytes with k or M. Default 2000 lines
    --log FILE         default /tmp/mpuart/<device>.log
    --no-log           do not log
    --raw-log FILE     exact device bytes, no timestamps
    --tcp [HOST:]PORT  also listen on tcp
    --mode 660         socket and log permissions. Default 666
    --rtscts --xonxoff flow control
    --no-exclusive     do not claim the port with TIOCEXCL

`SIGHUP` reopens the logs, for logrotate.

`attach`

    -n NAME            default $MPUART_NAME, else your login name
    -r                 read only
    --history N        replay at most N lines
    --no-history       start on a clean screen
    --device-only      device bytes only, no notices and no [name] tags
    --echo             tag your own typing back to you
    --eol crlf|cr|lf   what Enter sends. Default crlf, as miniterm
    --raw-keys         send every key untouched
    --no-color

`send`

    -w SECONDS         how long to read. Default 1
    -e REGEX           stop as soon as it appears, exit 1 if it does not
    -N                 send no line ending
    --eol crlf|cr|lf

## Line endings

Enter sends CR LF, which is what miniterm sends. Consoles that run a command on
the LF ignore a lone CR: the line echoes and then sits there, and your next
command is appended to it. Use `--eol cr` or `--eol lf` if your device wants one
of them. The DEL key is sent as backspace, again as miniterm does.

Device output is passed through untouched. Your terminal keeps its own LF to
CRLF step, so a device that ends lines with a bare LF still starts at the left.

## A device that does not speak text

`mpuart` passes the device bytes through, and adds two kinds of text of its own:
a notice about who comes and goes, and `[name] text` for what somebody types. On
a text console they are what makes the port shareable. In a binary stream they
land inside a frame and cost the decoder that frame.

`--device-only` turns both off, so what you get is the device bytes and nothing
else. A Rust image that logs with `defmt` reads like this:

    mpuart attach -r --device-only | defmt-print -e firmware.elf stdin

Ask the daemon what the port is doing instead. `mpuart list` says whether the
device is open and who is attached, and asking is not attaching.

Serve a binary device with `--history 64k`, because the default history is
counted in lines and a binary stream has none. A binary stream is unreadable in
the text log, so add `--raw-log FILE` and decode that file afterwards.

## Sharing

The socket is `/tmp/mpuart/<device>.sock`, mode 0666, so anyone on the machine
can attach. Only the daemon needs to be in `dialout`. `--mode 660` holds it to
your group. `MPUART_DIR` moves the socket and log directory.

Over TCP, `mpuart attach 127.0.0.1:5600`, or `nc 127.0.0.1 5600`. There is no
authentication, so bind to `0.0.0.0` only behind something that controls access.

The daemon claims the port with `TIOCEXCL`, so a stray `miniterm` is refused
instead of stealing it. It claims the tty only, so JTAG on the same adapter is
untouched: on a USB Blaster III the JTAG interface is on usbfs and the UART is
the one `ftdi_sio` turns into `/dev/ttyUSB0`.

## If the adapter disconnects

Nobody is thrown off. Clients stay attached and are told:

    -- /dev/ttyUSB0 lost (eof), retrying --

While the port is away, anything you type is refused rather than queued, so a
command cannot arrive at the device minutes later, out of context:

    -- device not open, dropped --

The daemon retries once a second and sits idle between tries. When the adapter
comes back it says so, and the backscroll from before the outage is still there:

    -- /dev/ttyUSB0 open --

Both events are in the log with timestamps, so you can tell a dead device from a
quiet one afterwards.

A replugged adapter can come back as `ttyUSB1` if something else took `ttyUSB0`
first. `mpuart serve` with no device already guards against this, because it
serves the `/dev/serial/by-id` path, and the socket keeps its original name, so
`mpuart attach ttyUSB0` still works afterwards. Naming `/dev/ttyUSB0` yourself
gives that up: the daemon then waits for a name the adapter may not come back
with.

## Test

    ./test_mpuart.py

Needs `socat`. It makes a pty pair, so no hardware is involved.
