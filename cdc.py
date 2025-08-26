#!/usr/bin/env python3
# cdc.py
# Podporuje dva režimy:
#  1) SDK (Infineon ifxradarsdk) – když RDK+FW na MCU7(+) je správně a zařízení se hlásí přes Radar SDK
#  2) Sériový port (CDC/USB modem) – když deska streamuje přes /dev/tty.usbmodem*
#
# Příklady:
#   python3 cdc.py --list
#   python3 cdc.py --sdk-only
#   python3 cdc.py --port /dev/tty.usbmodem2101 --baud 115200
#   python3 cdc.py --serial-only --hex --read-bytes 1024
#
# Pozn.: pro sériový režim nainstaluj pyserial: pip install pyserial

import argparse
import sys
import time
import json
import glob
import signal
from typing import Optional

# ----------------------------- Pomocné -----------------------------

def graceful_exit(signum, frame):
    raise KeyboardInterrupt()

signal.signal(signal.SIGINT, graceful_exit)

def eprint(*a, **kw):
    print(*a, file=sys.stderr, **kw)

def is_json_line(s: str) -> bool:
    s = s.strip()
    if not s or s[0] not in ('{', '['):
        return False
    try:
        json.loads(s)
        return True
    except Exception:
        return False

# ----------------------------- Sériový režim -----------------------------

def run_serial(port: str, baud: int, read_bytes: Optional[int], hex_dump: bool,
               newline_mode: bool, save_path: Optional[str], timeout: float) -> None:
    try:
        import serial  # pyserial
    except Exception as e:
        eprint(f"[SERIAL] Chybí pyserial: {e}\nNainstaluj: pip install pyserial")
        sys.exit(2)

    eprint(f"[SERIAL] Otevírám {port} @ {baud}, timeout={timeout}s")
    ser = serial.Serial(port, baudrate=baud, timeout=timeout)

    # malá pauza po otevření
    time.sleep(0.2)

    fout = None
    if save_path:
        fout = open(save_path, "ab")
        eprint(f"[SERIAL] Loguji do: {save_path}")

    eprint("[SERIAL] Čtu data… (Ctrl+C pro ukončení)")

    try:
        while True:
            if read_bytes and read_bytes > 0:
                data = ser.read(read_bytes)
                if not data:
                    continue
                if hex_dump:
                    line = data.hex()
                    print(line)
                    if fout:
                        fout.write((line + "\n").encode("utf-8", "replace"))
                else:
                    # binár necháme jako raw do souboru + hezký výpis do stdout (hex)
                    if fout:
                        fout.write(data)
                    print(data.hex())
            else:
                # řádkový režim
                line = ser.readline()
                if not line:
                    continue
                try:
                    txt = line.decode("utf-8", errors="replace")
                except Exception:
                    txt = line.hex()

                if is_json_line(txt):
                    # hezké formátování JSONu
                    try:
                        obj = json.loads(txt)
                        pretty = json.dumps(obj, ensure_ascii=False)
                        print(pretty)
                        if fout:
                            fout.write((pretty + ("\n" if newline_mode else "")).encode("utf-8", "replace"))
                        continue
                    except Exception:
                        pass

                # obyčejný text
                if newline_mode and not txt.endswith("\n"):
                    txt += "\n"
                print(txt, end="" if txt.endswith("\n") else ("\n" if newline_mode else ""))
                if fout:
                    fout.write(txt.encode("utf-8", "replace"))
    except KeyboardInterrupt:
        eprint("\n[SERIAL] Přerušeno uživatelem.")
    finally:
        try:
            ser.close()
        except Exception:
            pass
        if fout:
            fout.close()
        eprint("[SERIAL] Zavřeno.")

# ----------------------------- SDK režim -----------------------------

def make_sequence_for_15m(dev, fps: float = 50.0):
    """
    Sestaví simple-sekvenci pro ~15 m dosah.
    Úmyslně konzervativní nastavení (1 TX / 1 RX, rozumné filtry).
    """
    from ifxradarsdk.fmcw.types import FmcwSimpleSequenceConfig, FmcwMetrics

    metrics = FmcwMetrics(
        range_resolution_m=0.05,
        max_range_m=15.0,
        max_speed_m_s=0.0,
        speed_resolution_m_s=0.0,
        center_frequency_Hz=60_750_000_000
    )
    seq = dev.create_simple_sequence(FmcwSimpleSequenceConfig())
    seq.loop.repetition_time_s = 1.0 / fps

    chirp_loop = seq.loop.sub_sequence.contents
    dev.sequence_from_metrics(metrics, chirp_loop)

    chirp = chirp_loop.loop.sub_sequence.contents.chirp
    chirp.sample_rate_Hz = 1_000_000
    chirp.rx_mask = 0b0001
    chirp.tx_mask = 0b0001
    chirp.tx_power_level = 31
    chirp.if_gain_dB = 33
    chirp.lp_cutoff_Hz = 500_000
    chirp.hp_cutoff_Hz = 80_000
    return seq

def _uuid_from_entry(entry) -> Optional[str]:
    # entry může být objekt s .uuid, string, bytes, dict…
    try:
        if hasattr(entry, "uuid"):
            return str(entry.uuid)
        if isinstance(entry, bytes):
            return entry.decode()
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict) and "uuid" in entry:
            return str(entry["uuid"])
    except Exception:
        pass
    return None

def run_sdk(n_frames: int, fps: float) -> bool:
    try:
        from ifxradarsdk.fmcw import DeviceFmcw
    except Exception as e:
        eprint(f"[SDK] Nelze importovat ifxradarsdk: {e}")
        return False

    try:
        devs = DeviceFmcw.get_list()
    except Exception as e:
        eprint(f"[SDK] get_list() selhalo: {e}")
        return False

    if not devs:
        eprint("[SDK] Nenalezeno žádné FMCW zařízení.")
        return False

    print("[SDK] Nalezeno zařízení:", devs)

    uuid = _uuid_from_entry(devs[0])
    if not uuid:
        eprint("[SDK] Neumím získat UUID z devs[0] → končím SDK.")
        return False

    try:
        with DeviceFmcw(uuid=uuid) as dev:
            info = dev.get_device_information()
            print("[SDK] Otevřeno:", info)

            seq = make_sequence_for_15m(dev, fps=fps)
            dev.set_acquisition_sequence(seq)

            print(f"[SDK] Čtu {n_frames} snímků…")
            import numpy as np
            for i in range(n_frames):
                frame = dev.get_next_frame()
                if i == 0:
                    f0 = frame[0]
                    arr = np.array(f0)
                    print("[SDK] frame[0].shape:", arr.shape, "dtype:", arr.dtype)
            print("[SDK] Hotovo.")
            return True
    except KeyboardInterrupt:
        eprint("\n[SDK] Přerušeno uživatelem.")
        return True
    except Exception as e:
        eprint(f"[SDK] Chyba při práci se zařízením: {e}")
        return False

# ----------------------------- Hlavní -----------------------------

def autodetect_serial_port(preferred_suffix: Optional[str] = None) -> Optional[str]:
    cands = sorted(glob.glob("/dev/tty.usbmodem*") + glob.glob("/dev/tty.usbserial*"))
    if not cands:
        return None
    if preferred_suffix:
        for c in cands:
            if c.endswith(preferred_suffix):
                return c
    return cands[0]

def main():
    ap = argparse.ArgumentParser(description="Infineon radar DEMO BGT60UTR11AIP – SDK/CDC čtečka")
    ap.add_argument("--list", action="store_true", help="Vypsat zařízení (SDK i CDC) a skončit")
    ap.add_argument("--sdk-only", action="store_true", help="Použít jen SDK režim (nespouštět CDC)")
    ap.add_argument("--serial-only", action="store_true", help="Použít jen sériový režim (nespouštět SDK)")
    ap.add_argument("--port", help="Sériový port, např. /dev/tty.usbmodem2101")
    ap.add_argument("--baud", type=int, default=115200, help="Baudrate pro sériový režim (výchozí 115200)")
    ap.add_argument("--timeout", type=float, default=1.0, help="Timeout sériového čtení v sekundách")
    ap.add_argument("--read-bytes", type=int, default=0, help="Pokud >0, čte binárně N bajtů místo řádků")
    ap.add_argument("--hex", action="store_true", help="Při binárním čtení tisknout hex dump")
    ap.add_argument("--newline", action="store_true", help="Do výstupu přidávat \\n i když zařízení neposílá")
    ap.add_argument("--save", help="Cesta k log souboru (append)")
    ap.add_argument("--frames", type=int, default=50, help="Počet snímků v SDK režimu")
    ap.add_argument("--fps", type=float, default=50.0, help="FPS v SDK režimu (repetition_time)")

    args = ap.parse_args()

    if args.list:
        # SDK seznam
        try:
            from ifxradarsdk.fmcw import DeviceFmcw
            devs = []
            try:
                devs = DeviceFmcw.get_list()
            except Exception as e:
                eprint(f"[LIST][SDK] get_list() selhalo: {e}")
            print("[LIST][SDK]:", devs if devs else "(nic)")
        except Exception as e:
            print(f"[LIST][SDK] ifxradarsdk není k dispozici: {e}")

        # CDC seznam
        cands = sorted(glob.glob("/dev/tty.usbmodem*") + glob.glob("/dev/tty.usbserial*"))
        print("[LIST][CDC]:", cands if cands else "(nic)")
        return

    # 1) Pokud --serial-only → přímo sériák
    if args.serial_only:
        port = args.port or autodetect_serial_port(preferred_suffix="2101")
        if not port:
            eprint("[CDC] Nenašel jsem žádný /dev/tty.usbmodem* ani /dev/tty.usbserial*")
            sys.exit(1)
        run_serial(port, args.baud, args.read_bytes, args.hex, args.newline, args.save, args.timeout)
        return

    # 2) Pokud --sdk-only → zkus SDK a skonči
    if args.sdk_only:
        ok = run_sdk(args.frames, args.fps)
        sys.exit(0 if ok else 1)

    # 3) Default: zkus SDK, při selhání spadni na CDC
    ok = run_sdk(args.frames, args.fps)
    if ok:
        return

    eprint("[AUTO] SDK nedostupné → přepínám na sériový režim.")
    port = args.port or autodetect_serial_port(preferred_suffix="2101")
    if not port:
        eprint("[CDC] Nenašel jsem žádný /dev/tty.usbmodem* ani /dev/tty.usbserial*")
        sys.exit(1)
    run_serial(port, args.baud, args.read_bytes, args.hex, args.newline, args.save, args.timeout)

if __name__ == "__main__":
    main()
