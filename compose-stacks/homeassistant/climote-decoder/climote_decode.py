import subprocess
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
MQTT_TOPIC_WATCHMAN = os.environ.get("MQTT_TOPIC_WATCHMAN", "watchman/sensor/state")
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")


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
            client_id="climote_watchman_decoder",
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


def process_capture_file(filename: str, symbol_w: int = 510):
    """Reads raw I/Q data and iterates over detected RF bursts."""
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
        return

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
            return  # Exit after first successful decode per capture
        else:
            print("Could not locate composite preamble+sync pattern.", flush=True)


def capture_climote(capture_file: str):
    """Executes rtl_sdr to record 868.5MHz RF data."""
    print("\n[Climote] Capturing 868.5MHz RF data...", flush=True)
    subprocess.run([
        "rtl_sdr",
        "-f", "868500000",
        "-s", "1024000",
        "-n", "2048000",
        "-g", "40",
        capture_file
    ], check=True, timeout=10)


# ==========================================
# WATCHMAN LISTENER LOGIC (433.92 MHz)
# ==========================================

def listen_watchman(duration_secs: int = 720):
    """Listens for Watchman bursts via rtl_433 on 433.92MHz."""
    print(f"\n[Watchman] Listening on 433.92MHz for {duration_secs}s...", flush=True)
    cmd = [
        "rtl_433",
        "-f", "433.92M",
        "-R", "43",  # Watchman / Oil-Sonic Protocol
        "-F", "json",
        "-T", str(duration_secs)
    ]
    
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                print(f"[Watchman] Signal Captured: {data}", flush=True)
                publish_mqtt(MQTT_TOPIC_WATCHMAN, data)
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print(f"[Watchman] Processing error: {e}", flush=True)
                
        proc.wait(timeout=duration_secs + 10)
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
    except Exception as e:
        print(f"[Watchman] Subprocess error: {e}", flush=True)


# ==========================================
# MAIN MACRO LOOP
# ==========================================

def main():
    capture_file = "/tmp/climote_capture.cu8"
    
    while True:
        print("\n==================================================", flush=True)
        print("Starting 15-Minute Macro Cycle", flush=True)
        print("==================================================", flush=True)
        
        # 1. Climote Capture & Decode
        try:
            capture_climote(capture_file)
            process_capture_file(capture_file)
        except subprocess.TimeoutExpired:
            print("[Climote] Hardware capture timed out.", flush=True)
        except subprocess.CalledProcessError as e:
            print(f"[Climote] Hardware capture failed: {e}", flush=True)
        except Exception as e:
            print(f"[Climote] Processing error: {e}", flush=True)
        finally:
            if os.path.exists(capture_file):
                os.remove(capture_file)
                
        # Settle Tuner PLL
        time.sleep(2)
        
        # 2. Watchman Listening Window (12 Minutes)
        listen_watchman(duration_secs=720)
        
        # 3. Rest Window (2 Minutes) to avoid USB bus fatigue and lower duty cycle
        print("\n[Bus Rest] Cooling down USB interface for 118s...", flush=True)
        time.sleep(118)


if __name__ == "__main__":
    main()