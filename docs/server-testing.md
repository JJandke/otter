# Safe server testing on franz

These are operator instructions; **none of these server changes has been run**.
They create an isolated M1 experiment, not a production deployment. No edits to
Nginx, Certbot, systemd, the HTTPS hostname, or `/home/ubuntu/provadis_timetable`
are required. There is no public M1 HTTP endpoint or ICS file.

## Prepare the isolated experiment

Connect using the existing SSH alias:

```sh
ssh franz
```

Verify the host and account, and work only under a new directory such as
`/home/ubuntu/provadis-m1`. If this path already contains unrelated work, choose a
different fresh directory rather than overwriting it. Obtain the reviewed M1
source from its repository or source archive. Do not copy the old application.

The server needs uv, Python 3.12.14, and Playwright's Chromium dependencies.
Python 3.12 can be managed by uv; do not replace Ubuntu's system Python. Review
and run dependency installation deliberately because it may use sudo:

```sh
cd /home/ubuntu/provadis-m1
uv venv --python 3.12.14
uv pip sync --python .venv/bin/python requirements.lock
# Installs OS libraries required by the pinned Playwright browser; review sudo output.
.venv/bin/python -m playwright install-deps chromium
.venv/bin/python -m playwright install chromium
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Ubuntu 22.04 is in Playwright's documented supported Linux family. Browser/kernel
sandbox availability and actual `franz` packages still need testing. Do not solve
a launch failure by running Chromium as root or adding `--no-sandbox`.

## Temporary graphical access via an SSH tunnel

Use a temporary Xvfb display and password-protected VNC bound to server loopback.
The browser profile must be created on the server as the same `ubuntu` account
used for later headless runs. This procedure requires `xvfb`, `x11vnc`, `xauth`,
and `openssl`. Installing missing OS packages is an explicit operator step:

```sh
sudo apt-get install xvfb x11vnc xauth openssl
```

Choose an unused display number and port before starting. The example uses X
display `:99` and VNC port 5901. In a server SSH terminal, from the M1 directory:

```sh
umask 077
mkdir -p .m1-gui
chmod 700 .m1-gui
touch .m1-gui/Xauthority
chmod 600 .m1-gui/Xauthority
export XAUTHORITY="$PWD/.m1-gui/Xauthority"
xauth -f "$XAUTHORITY" add :99 . "$(openssl rand -hex 16)"
# Prompts locally: never put the VNC password in command arguments or chat.
x11vnc -storepasswd .m1-gui/vnc.passwd
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp -auth "$XAUTHORITY" > .m1-gui/xvfb.log 2>&1 &
xvfb_pid=$!
x11vnc -display :99 -auth "$XAUTHORITY" -localhost -rfbport 5901 \
  -rfbauth .m1-gui/vnc.passwd -forever -shared -o .m1-gui/vnc.log &
vnc_pid=$!
```

Confirm both newly started processes remain running and that the VNC listener is
loopback-only (`ss -ltn 'sport = :5901'`). If either startup failed, stop here;
do not attach to an unrelated pre-existing display. Keep this terminal open to
retain the two process IDs for cleanup. Xvfb has TCP disabled and an Xauthority
cookie; do not use `-ac` or `xhost +`.

In a separate terminal **on the Mac**:

```sh
ssh -N -T -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:5901:127.0.0.1:5901 franz
```

Connect a VNC viewer (macOS Screen Sharing works) to `vnc://127.0.0.1:5901`.
In the server terminal run:

```sh
DISPLAY=:99 ./scripts/m1 login
```

Sign in and open the timetable in the dedicated Chromium window. Credentials/MFA
stay in the browser. Do not expose VNC/CDP through a public interface, firewall
rule, reverse proxy, or the existing HTTPS hostname.

After successful login, in that same server terminal:

```sh
./scripts/m1 verify
# Only terminate the temporary processes started above, using their saved IDs.
kill "$vnc_pid" "$xvfb_pid"
```

Close the Mac's tunnel with Ctrl-C. Retire the temporary VNC password and
Xauthority file locally when graphical testing ends. Keep any troubleshooting
logs private; do not publish them. The entire `.m1-gui` directory is ignored by
Git. No VNC service or persistent startup entry should be enabled for M1.

## What to record

Record server OS/architecture, Python and Playwright versions, commands/options,
exit codes, and the sanitized JSON summaries. Do not publish account identity,
lecture details, raw API responses, browser storage, or GUI screenshots.

Run the restart and longevity matrix in [feasibility.md](feasibility.md) before
approving M2. Reauthenticate through this same temporary tunnel when needed.
No production scheduling or deployment is part of this milestone.
