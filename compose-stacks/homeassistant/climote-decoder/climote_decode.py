import subprocess
import sys
import time
import os
import numpy as np
import re
import json
import paho.mqtt.publish as publish
import traceback

# Steinhart-Hart Constants (Climote)
A = -5.1166039831e-02
B = 1.0255677487e-02
C = -5.2472281758e-05

# MQTT Configuration
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_TOPIC_CLIMOTE = os.environ.get("MQTT_TOPIC_CLIMOTE", "climote/sensor/state")
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

# Capture timing. The Climote sends a ~20s train of repeated bursts every 10-15 minutes,
# so after a good decode we sleep just under the shortest gap, then listen back-to-back
# until the next train arrives.
SAMPLE_RATE = 1_024_000
CAPTURE_SECS = int(os.environ.get("CAPTURE_SECS", 10))
SLEEP_AFTER_DECODE_SECS = int(os.environ.get("SLEEP_AFTER_DECODE_SECS", 540))
MAX_HW_FAILURES = 3   # consecutive dongle failures before exiting so Docker restarts us
HW_RETRY_SECS = 10


def publish_mqtt(topic: str, payload: dict):
    """Unified MQTT publish function."""
    print(f"--- MQTT Publish Attempt [{topic}] ---", flush=True)
    print(f"Target: {MQTT_HOST}:{MQTT_PORT} | Payload: {payload}", flush=True)
    
    auth = None
    if MQTT_USER and MQTT_PASS:
        auth = {'username': MQTT_USER, 'password': MQTT_PASS}
        
    try:
        publish.single(
            topic=topic,
            payload=json.dumps(payload),
            hostname=MQTT_HOST,
            port=MQTT_PORT,
            auth=auth,
            client_id="climote_decoder",
            retain=False
        )
        print("MQTT Publish: SUCCESS", flush=True)
    except Exception as e:
        print(f"MQTT Publish: FAILED - {e}", flush=True)
        traceback.print_exc()


# ==========================================
# CLIMOTE DECODER LOGIC (868.5 MHz)
# ==========================================

def calculate_temperature(raw_adc: int):
    """Applies Steinhart-Hart equation to raw ADC value."""
    ln_adc = np.log(np.maximum(raw_adc, 1e-6))
    inv_kelvin = A + (B * ln_adc) + (C * (ln_adc ** 3))
    temp_kelvin = 1.0 / inv_kelvin
    return temp_kelvin - 273.15


def decode_burst_bits(burst_sig: complex, symbol_w: int):
    """Demodulates FM signal into a binary string."""
    phase = np.unwrap(np.angle(burst_sig))
    demod = np.diff(phase)
    demod = demod - np.median(demod)
    
    bits = []
    curr = 0
    while curr + symbol_w < len(demod):
        val = np.mean(demod[int(curr + symbol_w * 0.25) : int(curr + symbol_w * 0.75)])
        bits.append('1' if val > 0 else '0')
        curr += symbol_w
        
    return "".join(bits)


def extract_telemetry(bit_str: str):
    """Finds preamble/sync, aligns frame, and extracts ADC value."""
    sync_bin = "001011011101010000001011"
    pattern = re.compile(r'((?:10){4,}|(?:01){4,})(' + sync_bin + ')')
    match = pattern.search(bit_str)
    
    if not match:
        return None
        
    sync_start = match.start(2)
    payload_bits = bit_str[sync_start - 16 : sync_start + 240]
    
    pad_len = (8 - (len(payload_bits) % 8)) % 8
    padded_bits = payload_bits + ('0' * pad_len)
    
    byte_array = int(padded_bits, 2).to_bytes(len(padded_bits) // 8, byteorder='big')
    
    if len(byte_array) >= 14:
        msb = (byte_array[11] >> 4) & 0x0F
        lsb = byte_array[13]
        raw_adc = (msb << 8) | lsb
    else:
        print("Error: Extracted byte array is too short for expected telemetry data.")
        raw_adc = None
    
    return raw_adc


def process_capture_file(filename: str, symbol_w: int = 510) -> bool:
    """Reads raw I/Q data and iterates over detected RF bursts.

    Returns True if a reading was decoded and published.
    """
    print(f"Reading {filename}...", flush=True)
    with open(filename, "rb") as f:
        raw = np.fromfile(f, dtype=np.uint8)
    
    iq = raw.astype(np.float32) - 127.5
    complex_sig = (iq[0::2]) + 1j * (iq[1::2])
    power = iq[0::2]**2 + iq[1::2]**2
    
    threshold = 100.0
    active_indices = np.where(power > threshold)[0]
    if len(active_indices) == 0:
        print("No active bursts found.", flush=True)
        return False

    gap_indices = np.where(np.diff(active_indices) > 10000)[0]
    burst_starts = [active_indices[0]] + [active_indices[idx + 1] for idx in gap_indices]
    
    for i, burst_start in enumerate(burst_starts[:5]):
        print(f"--- Processing Burst {i+1} at index {burst_start} ---", flush=True)
        window_start = max(0, burst_start - 1000)
        burst_sig = complex_sig[window_start : window_start + 120000]
        
        bit_str = decode_burst_bits(burst_sig, symbol_w)
        raw_adc = extract_telemetry(bit_str)
        
        if raw_adc is not None:
            temp_celsius = calculate_temperature(raw_adc)
            print(f"Raw ADC: {raw_adc} | Calculated Temperature: {temp_celsius:.2f}°C", flush=True)
            payload = {
                "temperature_c": round(temp_celsius, 2),
                "raw_adc": raw_adc
            }
            publish_mqtt(MQTT_TOPIC_CLIMOTE, payload)
            return True  # Exit after first successful decode per capture
        else:
            print("Could not locate composite preamble+sync pattern.", flush=True)

    return False


def capture_climote(capture_file: str):
    """Executes rtl_sdr to record CAPTURE_SECS of 868.5MHz RF data."""
    print(f"\n[Climote] Capturing {CAPTURE_SECS}s at 868.5MHz...", flush=True)
    cmd = [
        "rtl_sdr",
        "-f", "868500000",
        "-s", str(SAMPLE_RATE),
        "-n", str(SAMPLE_RATE * CAPTURE_SECS),
        "-g", "40",
        capture_file,
    ]
    proc = subprocess.Popen(cmd)
    try:
        proc.wait(timeout=CAPTURE_SECS + 15)
    except subprocess.TimeoutExpired:
        # Ask rtl_sdr to shut down cleanly so it releases the USB interface; SIGKILL only as a last resort.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


# ==========================================
# MAIN LOOP
# ==========================================

def main():
    capture_file = "/tmp/climote_capture.cu8"
    hw_failures = 0

    while True:
        decoded = False
        try:
            capture_climote(capture_file)
            hw_failures = 0
            decoded = process_capture_file(capture_file)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            hw_failures += 1
            print(f"[Climote] Hardware capture failed ({hw_failures}/{MAX_HW_FAILURES}): {e}", flush=True)
            if hw_failures >= MAX_HW_FAILURES:
                # Usually the dongle re-enumerated on the USB bus. Exit so `restart: unless-stopped`
                # starts a fresh container with a fresh view of the device.
                print("[Climote] Giving up; exiting so Docker restarts the container.", flush=True)
                sys.exit(1)
            time.sleep(HW_RETRY_SECS)
        except Exception as e:
            print(f"[Climote] Processing error: {e}", flush=True)
            traceback.print_exc()
        finally:
            if os.path.exists(capture_file):
                os.remove(capture_file)

        if decoded:
            print(f"[Climote] Sleeping {SLEEP_AFTER_DECODE_SECS}s until the next expected transmission.", flush=True)
            time.sleep(SLEEP_AFTER_DECODE_SECS)


if __name__ == "__main__":
    main()
