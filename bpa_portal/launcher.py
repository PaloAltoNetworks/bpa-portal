"""Launcher — starts the embedded waitress server and opens the browser.

By default the portal listens on ALL network interfaces (IPv4 and IPv6) so it
can be reached from other machines. The app has no authentication, so anyone
who can reach the port has full access; bind to loopback with
`--host 127.0.0.1` (or BPA_HOST=127.0.0.1) if you want single-machine use.
"""
import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

from waitress import serve

from .app import app, DATA_DIR

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5057

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
ALL_INTERFACES = {"0.0.0.0", "*", "::", ""}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="bpa-portal",
        description="Palo Alto Networks BPA Portal — local web UI for Best Practice Assessments.",
    )
    p.add_argument(
        "--host",
        default=os.environ.get("BPA_HOST", DEFAULT_HOST),
        help=f"Interface to bind. Default {DEFAULT_HOST} (all interfaces). "
             f"Use 127.0.0.1 to restrict to this machine. Env: BPA_HOST",
    )
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("BPA_PORT", DEFAULT_PORT)),
        help=f"Port to listen on. Default {DEFAULT_PORT}. Env: BPA_PORT",
    )
    p.add_argument(
        "--no-browser",
        action="store_true",
        default=bool(os.environ.get("BPA_NO_BROWSER")),
        help="Do not open a browser on startup. Env: BPA_NO_BROWSER",
    )
    p.add_argument(
        "--threads",
        type=int,
        default=int(os.environ.get("BPA_THREADS", "8")),
        help="Worker threads. Default 8. Env: BPA_THREADS",
    )
    return p.parse_args(argv)


def port_is_free(host: str, port: int) -> bool:
    """Check the port is bindable before waitress tries, so we can fail clearly."""
    family = socket.AF_INET6 if ":" in host and host not in ALL_INTERFACES else socket.AF_INET
    probe_host = "0.0.0.0" if host in ALL_INTERFACES else host
    if family == socket.AF_INET6:
        probe_host = host
    s = socket.socket(family, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((probe_host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def local_addresses():
    """Every address on this box a user could actually browse to."""
    addrs = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass
    # getaddrinfo often misses secondary interfaces; ask the routing table too.
    for probe in (("8.8.8.8", 80), ("2001:4860:4860::8888", 80)):
        fam = socket.AF_INET6 if ":" in probe[0] else socket.AF_INET
        s = socket.socket(fam, socket.SOCK_DGRAM)
        try:
            s.connect(probe)
            ip = s.getsockname()[0]
            if ip not in addrs:
                addrs.append(ip)
        except OSError:
            pass
        finally:
            s.close()
    return [a for a in addrs if not a.startswith("127.") and a != "::1"]


def publish_url(url: str) -> None:
    """Record the live URL where a user can find it without a console.

    macOS .app bundles and Windows GUI builds have no visible stdout, so if
    webbrowser.open() fails the user would otherwise have a running server and
    no way to learn its address.
    """
    try:
        (DATA_DIR / "portal-url.txt").write_text(url + "\n", encoding="utf-8")
    except OSError:
        pass


def announce_without_console(url: str) -> None:
    """Last-resort visible notice when the browser did not open and there is no console."""
    if sys.platform == "darwin":
        script = (
            f'display dialog "BPA Portal is running at:\n\n{url}\n\n'
            f'Copy this address into your browser." '
            f'with title "BPA Portal" buttons {{"OK"}} default button "OK"'
        )
        os.system(f"osascript -e {script!r} >/dev/null 2>&1 &")


def open_browser(url: str) -> None:
    time.sleep(0.8)
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        announce_without_console(url)


def banner(host: str, port: int, local_url: str) -> None:
    listening_everywhere = host in ALL_INTERFACES
    print("=" * 68)
    print(" BPA Portal")
    print("=" * 68)
    print(f" Local:     {local_url}")
    if listening_everywhere:
        for ip in local_addresses():
            disp = f"[{ip}]" if ":" in ip else ip
            print(f" Network:   http://{disp}:{port}/")
    print(f" Data dir:  {DATA_DIR}")
    print(f" URL saved: {DATA_DIR / 'portal-url.txt'}")
    if listening_everywhere:
        print("-" * 68)
        print(" WARNING: listening on ALL interfaces with NO authentication.")
        print(" Anyone who can reach this port can upload configs, read every")
        print(" stored report, and delete them. Restrict with --host 127.0.0.1")
        print(" or keep the port closed at the firewall.")
    print("-" * 68)
    print(" Press Ctrl+C to stop the server.")
    print("=" * 68, flush=True)


def main(argv=None):
    args = parse_args(argv)
    host, port = args.host, args.port

    if not port_is_free(host, port):
        print(
            f"ERROR: port {port} on {host} is already in use.\n"
            f"       Stop whatever is using it, or choose another port:\n"
            f"         bpa-portal --port 5058       (or BPA_PORT=5058)",
            file=sys.stderr,
        )
        sys.exit(1)

    local_url = f"http://127.0.0.1:{port}/"
    publish_url(local_url)
    banner(host, port, local_url)

    if not args.no_browser:
        threading.Thread(target=open_browser, args=(local_url,), daemon=True).start()

    try:
        if host in ALL_INTERFACES:
            # "*" makes waitress bind both IPv4 and IPv6 on every interface.
            serve(app, listen=f"*:{port}", threads=args.threads, ident="BPA Portal")
        else:
            serve(app, host=host, port=port, threads=args.threads, ident="BPA Portal")
    except KeyboardInterrupt:
        print("\nShutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()
