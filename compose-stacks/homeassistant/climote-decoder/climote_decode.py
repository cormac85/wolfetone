import subprocess
import time
import os
import numpy as np
import re
import json
import paho.mqtt.publish as publish

# Steinhart-Hart Constants
A = -5.1166039831e-02
B = 1.0255677487e-02
C = -5.2472281758e-05

# MQTT Configuration
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_TOPIC = "climote/sensor/state"
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

def calculate_temperature(raw_adc):
    """Applies Steinhart-Hart equation to raw ADC value."""
    ln_adc = np.log(np.maximum(raw_adc, 1e-6))
    inv_kelvin = A + (B * ln_adc) + (C * (ln_adc ** 3))
    temp_kelvin = 1.0 / inv_kelvin
    return temp_kelvin - 273.15


def publish_data(raw_adc, temp_celsius):
    """Publishes decoded data to the MQTT broker."""
    payload = {
        "temperature_c": round(temp_celsius, 2),
        "raw_adc": raw_adc
    }
    
    print(f"Publishing to MQTT ({MQTT_HOST}:{MQTT_PORT}) -> {payload}")
    
    auth = None
    if MQTT_USER and MQTT_PASS:
        auth = {'username': MQTT_USER, 'password': MQTT_PASS}

    try:
        publish.single(
            topic=MQTT_TOPIC,
            payload=json.dumps(payload),
            hostname=MQTT_HOST,
            port=MQTT_PORT,
            auth=auth
        )
    except Exception as e:
        print(f"MQTT Publish failed: {e}")


def decode_burst_bits(burst_sig, symbol_w):
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


def extract_telemetry(bit_str):
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
    
    if len(byte_array) >= 12:
        msb = (byte_array[11] >> 4) & 0x0F
        lsb = byte_array[13]
        return_val = (msb << 8) | lsb
    else:
        print("Error: Extracted byte array is too short for expected telemetry data.")
        return_val = None
    
    return return_val


def process_capture_file(filename, symbol_w=510):
    """Reads raw I/Q data and iterates over detected RF bursts."""
    print(f"Reading {filename}...")
    with open(filename, "rb") as f:
        raw = np.fromfile(f, dtype=np.uint8)
    
    iq = raw.astype(np.float32) - 127.5
    complex_sig = (iq[0::2]) + 1j * (iq[1::2])
    power = iq[0::2]**2 + iq[1::2]**2
    
    threshold = 100.0
    active_indices = np.where(power > threshold)[0]
    if len(active_indices) == 0:
        print("No active bursts found.")
        return

    gap_indices = np.where(np.diff(active_indices) > 10000)[0]
    burst_starts = [active_indices[0]] + [active_indices[idx + 1] for idx in gap_indices]
    
    for i, burst_start in enumerate(burst_starts[:5]):
        print(f"--- Processing Burst {i+1} at index {burst_start} ---")
        window_start = max(0, burst_start - 1000)
        burst_sig = complex_sig[window_start : window_start + 120000]
        
        bit_str = decode_burst_bits(burst_sig, symbol_w)
        raw_adc = extract_telemetry(bit_str)
        
        if raw_adc is not None:
            temp_celsius = calculate_temperature(raw_adc)
            print(f"Raw ADC: {raw_adc} | Calculated Temperature: {temp_celsius:.2f}°C")
            publish_data(raw_adc, temp_celsius)
            return # Exit after first successful decode per capture to avoid duplicate MQTT spam
        else:
            print("Could not locate composite preamble+sync pattern.")


def capture_rf(capture_file):
    """Executes rtl_sdr to record RF data."""
    print("\nCapturing RF data...")
    subprocess.run([
        "rtl_sdr",
        "-f", "868500000",
        "-s", "1024000",
        "-n", "2048000",
        "-g", "40",
        capture_file
    ], check=True)


def main():
    capture_file = "/tmp/climote_capture.cu8"
    
    while True:
        try:
            capture_rf(capture_file)
            process_capture_file(capture_file)
        except subprocess.CalledProcessError as e:
            print(f"Hardware capture failed: {e}")
        except Exception as e:
            print(f"Processing error: {e}")
        finally:
            if os.path.exists(capture_file):
                os.remove(capture_file)
                
        time.sleep(17)  # Set odd wait time to avoid syncing on a fixed schedule with the Climote device


if __name__ == "__main__":
    main()