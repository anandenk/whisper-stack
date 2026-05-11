"""Capture serial output for a fixed duration. Used for autonomous flash-and-test.

Usage:
    python tools/serial_capture.py COM3 100 > capture.log
"""
import serial
import sys
import time


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else 'COM3'
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0

    # Wait for the port to be available (idf.py flash holds it briefly).
    deadline = time.monotonic() + 5.0
    ser = None
    last_err = None
    while time.monotonic() < deadline:
        try:
            ser = serial.Serial(port, 115200, timeout=0.5)
            break
        except Exception as e:
            last_err = e
            time.sleep(0.1)
    if ser is None:
        print(f"FAILED to open {port}: {last_err}", file=sys.stderr)
        sys.exit(1)

    end = time.monotonic() + duration
    while time.monotonic() < end:
        try:
            line = ser.readline()
        except Exception as e:
            print(f"\n[serial_capture] read error: {e}", file=sys.stderr)
            break
        if line:
            try:
                sys.stdout.write(line.decode('utf-8', errors='replace'))
            except Exception:
                sys.stdout.write(repr(line) + '\n')
            sys.stdout.flush()

    ser.close()
    print(f"\n[serial_capture] done after {duration}s", file=sys.stderr)


if __name__ == '__main__':
    main()
