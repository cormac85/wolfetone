import numpy as np
import os
import tempfile
import unittest

# Import functions from your main script
# Replace 'climote_watchman' with the actual name of your python module file

from climote_decode import (
    calculate_temperature,
    extract_telemetry,
    decode_burst_bits,
    process_capture_file
)


class TestClimoteWatchman(unittest.TestCase):

    def test_calculate_temperature_known_adc(self):
        """Verify Steinhart-Hart calculation against known raw ADC input."""
        # ADC 1552 maps to approximately 23.28°C
        raw_adc = 1552
        calculated_temp = calculate_temperature(raw_adc)
        
        self.assertIsInstance(calculated_temp, float)
        self.assertAlmostEqual(calculated_temp, 23.28, places=1)

    def test_calculate_temperature_edge_cases(self):
        """Ensure ADC values near zero do not trigger division-by-zero errors."""
        zero_adc_temp = calculate_temperature(0)
        self.assertIsInstance(zero_adc_temp, float)
        self.assertFalse(np.isnan(zero_adc_temp))

    def test_extract_telemetry_valid_payload(self):
        """Test telemetry extraction with a synthetic binary frame."""
        # The telemetry function extracts a 256-bit (32-byte) frame starting
        # 16 bits BEFORE the sync pattern.
        expected_bytes = bytearray(32)
        
        # 1. Construct the 16-bit preamble (2 bytes of 0xAA) before the sync
        expected_bytes[0] = 0xAA  # 10101010
        expected_bytes[1] = 0xAA  # 10101010
        
        # 2. Construct the 24-bit sync pattern in bytes 2, 3, and 4
        # sync_bin = "001011011101010000001011"
        expected_bytes[2] = int("00101101", 2)
        expected_bytes[3] = int("11010100", 2)
        expected_bytes[4] = int("00001011", 2)
        
        # 3. Set the target ADC bytes (Bytes 11 and 13) to yield an ADC of 3856
        # msb = (byte_array[11] >> 4) & 0x0F -> requires 0xF0 to yield 0x0F
        # lsb = byte_array[13]               -> requires 0x10
        expected_bytes[11] = 0xF0
        expected_bytes[13] = 0x10
        
        # 4. Convert the bytearray into a continuous bit string
        bit_str = "".join(f"{b:08b}" for b in expected_bytes)
        
        # 5. Prepend some zeros to simulate a real capture and ensure the regex aligns
        bit_str = "00000000" + bit_str
        
        extracted_adc = extract_telemetry(bit_str)
        self.assertEqual(extracted_adc, 3856)

    def test_extract_telemetry_missing_sync(self):
        """Ensure bit strings lacking the sync pattern return None."""
        invalid_bit_str = "10101010101010101111000011110000"
        self.assertIsNone(extract_telemetry(invalid_bit_str))

    def test_extract_telemetry_truncated_payload(self):
        """Ensure sync pattern with truncated payload handles short arrays gracefully."""
        preamble = "10101010"
        sync_bin = "001011011101010000001011"
        truncated_bit_str = "00000000" + preamble + sync_bin + "10101010"
        
        extracted_adc = extract_telemetry(truncated_bit_str)
        self.assertIsNone(extracted_adc)

    def test_decode_burst_bits(self):
        """Test FSK/FM demodulation on synthetic complex IQ data."""
        # Generate 1000 samples of synthetic complex signal
        t = np.linspace(0, 1, 1000)
        synthetic_iq = np.exp(1j * 2 * np.pi * 5 * t)
        
        bit_str = decode_burst_bits(synthetic_iq, symbol_w=100)
        
        self.assertIsInstance(bit_str, str)
        self.assertTrue(all(c in '01' for c in bit_str))

    def test_process_capture_file_silent_iq(self):
        """Verify IQ processor handles empty/silent raw capture files without errors."""
        # Create a temporary raw IQ file representing silence (center value ~127 uint8)
        with tempfile.NamedTemporaryFile(suffix=".cu8", delete=False) as tmp_file:
            tmp_path = tmp_file.name
            silent_data = np.full(10000, 127, dtype=np.uint8)
            tmp_file.write(silent_data.tobytes())

        try:
            # Should complete without throwing exceptions
            process_capture_file(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()