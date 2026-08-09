import subprocess
import time
import os
import numpy as np
import re

# Constants for Steinhart-Hart coefficients
A = -5.1166039831e-02
B = 1.0255677487e-02
C = -5.2472281758e-05


def parse_composite_bursts(filename, symbol_w=510):
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
    burst_starts = [active_indices[0]]
    for idx in gap_indices:
        burst_starts.append(active_indices[idx + 1])
    
    burst_starts = burst_starts[:5]

    for i, burst_start in enumerate(burst_starts):
        print(f"==============================")
        print(f"Processing Burst {i+1} at index {burst_start}")
        print(f"==============================")
        
        window_start = max(0, burst_start - 1000)
        burst_sig = complex_sig[window_start : window_start + 120000]
        
        phase = np.unwrap(np.angle(burst_sig))
        demod = np.diff(phase)
        demod = demod - np.median(demod)
        
        bits = []
        curr = 0
        while curr + symbol_w < len(demod):
            val = np.mean(demod[int(curr + symbol_w*0.25) : int(curr + symbol_w*0.75)])
            bits.append('1' if val > 0 else '0')
            curr += symbol_w
            
        bit_str = "".join(bits)
        
        # Composite regex: Look for preamble followed closely by the sync word (2dd40b)
        # Sync word binary: 001011011101010000001011
        sync_bin = "001011011101010000001011"
        pattern = re.compile(r'((?:10){4,}|(?:01){4,})(' + sync_bin + ')')
        match = pattern.search(bit_str)
        
        if match:
            preamble_end = match.end(1)
            sync_start = match.start(2)
            print(f"Composite match found! Preamble ends at {preamble_end}, Sync starts at {sync_start}")
            
            # Align perfectly starting slightly before the sync word to keep header context
            payload_bits = bit_str[sync_start - 16 : sync_start + 240]
            
            pad_len = (8 - (len(payload_bits) % 8)) % 8
            padded_bits = payload_bits + ('0' * pad_len)
            
            byte_array = int(padded_bits, 2).to_bytes(len(padded_bits) // 8, byteorder='big')
            print(f"Aligned Frame Hex: {byte_array.hex()}\n")
            
            msb = (byte_array[11] >> 4) & 0x0F
            lsb = byte_array[13]
            raw_adc = (msb << 8) | lsb
            ln_adc = np.log(np.maximum(raw_adc, 1e-6))

            # Steinhard Hart Calculation           
            inv_kelvin = A + (B * ln_adc) + (C * (ln_adc ** 3))
            temp_kelvin = 1.0 / inv_kelvin
            temp_celsius = temp_kelvin - 273.15
            print(f"\nCalculated Temperature: {temp_celsius}°C\n\n")
        else:
            print("Could not locate composite preamble+sync pattern.\n")


def capture_and_decode():
    capture_file = "/tmp/climote_capture.cu8"
    
    while True:
        print("Capturing RF data...")
        
        # Adjust -f (frequency) and -s (sample rate) to match your previous manual commands
        try:
            subprocess.run([
                "rtl_sdr",
                "-f", "868500000",      # Update to your exact frequency
                "-s", "1024000",      # Update to your sample rate
                "-n", "2048000",     # Number of samples (e.g., 10 seconds at 250k)
                "-g", "40",          # Gain
                capture_file
            ], check=True)
            
            print("Capture complete. Decoding...")
            parse_composite_bursts(capture_file)
            
        except subprocess.CalledProcessError as e:
            print(f"Hardware capture failed: {e}")
            
        finally:
            # Delete the raw I/Q file to prevent container bloat
            if os.path.exists(capture_file):
                os.remove(capture_file)
                
        # Wait before the next poll (e.g., 60 seconds)
        time.sleep(30)


if __name__ == "__main__":
    capture_and_decode()
