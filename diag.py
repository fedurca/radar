# ===========================================================================
# Copyright (C) 2021-2022 Infineon Technologies AG
# ... (copyright header remains the same) ...
# ===========================================================================

import argparse
import sys
import numpy as np

from ifxradarsdk import get_version_full
from ifxradarsdk.fmcw import DeviceFmcw
from ifxradarsdk.fmcw.types import FmcwSimpleSequenceConfig, FmcwMetrics
from helpers.DopplerAlgo import *


# -------------------------------------------------
# Helpers
# -------------------------------------------------
def parse_program_arguments(description, def_frate):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-f', '--frate', type=int, default=def_frate,
                        help="frame rate in Hz, default " + str(def_frate))
    return parser.parse_args()


# -------------------------------------------------
# Main logic
# -------------------------------------------------
if __name__ == '__main__':

    args = parse_program_arguments(
        '''Displays distance and speed plot from Radar Data''',
        def_frate=5)

    with DeviceFmcw() as device:
        print(f"Radar SDK Version: {get_version_full()}")
        print(f"Sensor: {str(device.get_sensor_type())}")

        i_ant = 0
        num_rx_antennas = 1

        metrics = FmcwMetrics(
            range_resolution_m=0.05,
            max_range_m=1.6,
            max_speed_m_s=3,
            speed_resolution_m_s=0.2,
            center_frequency_Hz=60_750_000_000,
        )

        sequence = device.create_simple_sequence(FmcwSimpleSequenceConfig())
        sequence.loop.repetition_time_s = 1 / args.frate

        chirp_loop = sequence.loop.sub_sequence.contents
        device.sequence_from_metrics(metrics, chirp_loop)

        chirp = chirp_loop.loop.sub_sequence.contents.chirp
        chirp.sample_rate_Hz = 1_000_000
        chirp.rx_mask = (1 << num_rx_antennas) - 1
        chirp.tx_mask = 1
        chirp.tx_power_level = 31
        chirp.if_gain_dB = 33
        chirp.lp_cutoff_Hz = 500000
        chirp.hp_cutoff_Hz = 80000

        device.set_acquisition_sequence(sequence)
        
        num_chirps_per_frame = chirp_loop.loop.num_repetitions
        num_samples_per_chirp = chirp.num_samples
        
        # --- DIAGNOSTIC 1: Verify arguments before creating the algorithm object ---
        print("\n--- [DIAGNOSTIC] Initializing Algorithm ---")
        print(f" - num_samples: {num_samples_per_chirp} (Type: {type(num_samples_per_chirp)})")
        print(f" - num_chirps: {num_chirps_per_frame} (Type: {type(num_chirps_per_frame)})")
        print(f" - num_antennas: {num_rx_antennas} (Type: {type(num_rx_antennas)})")
        print(f" - metrics object: Type: {type(metrics)}")
        
        algo = DopplerAlgo(num_samples_per_chirp, num_chirps_per_frame, num_rx_antennas, metrics)
        
        # --- DIAGNOSTIC 2: Inspect the created algorithm object ---
        if hasattr(algo, 'mti_history'):
            print(f" - algo.mti_history.dtype: {algo.mti_history.dtype}")
            print(f" - algo.mti_history.shape: {algo.mti_history.shape}")
            # Check if the dtype is a simple numeric type
            if not np.issubdtype(algo.mti_history.dtype, np.number):
                print("   [WARNING] mti_history dtype is not a simple number! This may cause errors.")
        else:
            print("   [WARNING] Algorithm object does not have 'mti_history' attribute.")
        print("------------------------------------------")
        
        print("\nStarting measurements... Press Ctrl+C to stop.")

        try:
            while True:
                frame_contents = device.get_next_frame()
                frame_data = frame_contents[0]
                antenna_samples = frame_data[i_ant, :, :]
                
                try:
                    # --- This is the call to the external library ---
                    distance_m, speed_m_s, range_doppler_map = algo.compute_doppler_map(antenna_samples, i_ant)
                except Exception as e:
                    # --- DIAGNOSTIC 3: Catch errors from the algorithm and print detailed info ---
                    print("\n--- ERROR DURING ALGORITHM EXECUTION ---")
                    print(f"Error Type: {type(e).__name__}")
                    print(f"Error Message: {e}")
                    print(f"[DIAGNOSTIC] Input data shape to algorithm: {antenna_samples.shape}")
                    print(f"[DIAGNOSTIC] Input data type: {antenna_samples.dtype}")
                    print("----------------------------------------")
                    sys.exit(1) # Stop the script after an error

                distance_mm = distance_m * 1000

                if abs(speed_m_s) < 0.1:
                    direction = "Static"
                elif speed_m_s > 0:
                    direction = "Approaching"
                else:
                    direction = "Receding"

                output = (f"Distance: {distance_mm:4.0f} mm | "
                          f"Speed: {speed_m_s:+.1f} m/s "
                          f"({direction})")
                print(output)

        except KeyboardInterrupt:
            print("\nProgram stopped by user.")
            sys.exit(0)