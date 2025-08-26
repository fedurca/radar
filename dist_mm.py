# ===========================================================================
# Copyright (C) 2021-2022 Infineon Technologies AG
# ... (copyright header remains the same) ...
# ===========================================================================

import argparse
import sys

from ifxradarsdk import get_version_full
from ifxradarsdk.fmcw import DeviceFmcw
from ifxradarsdk.fmcw.types import FmcwSimpleSequenceConfig, FmcwMetrics
from helpers.DistanceAlgo import *


# -------------------------------------------------
# Helpers
# -------------------------------------------------
def parse_program_arguments(description, def_frate):
    # Parse all program attributes
    # description:   describes program
    # def_frate:     default frame rate in Hz

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-f', '--frate', type=int, default=def_frate,
                        help="frame rate in Hz, default " + str(def_frate))
    return parser.parse_args()


# -------------------------------------------------
# Main logic
# -------------------------------------------------
if __name__ == '__main__':

    args = parse_program_arguments(
        '''Displays distance plot from Radar Data''',
        def_frate=5)

    with DeviceFmcw() as device:
        print(f"Radar SDK Version: {get_version_full()}")
        print("Sensor: " + str(device.get_sensor_type()))

        i_ant = 0  # use only 1st RX antenna
        num_rx_antennas = 1

        metrics = FmcwMetrics(
            range_resolution_m=0.05,
            max_range_m=1.6,
            max_speed_m_s=3,
            speed_resolution_m_s=0.2,
            center_frequency_Hz=60_750_000_000,
        )

        # create acquisition sequence based on metrics parameters
        sequence = device.create_simple_sequence(FmcwSimpleSequenceConfig())
        sequence.loop.repetition_time_s = 1 / args.frate  # set frame repetition time

        # convert metrics into chirp loop parameters
        chirp_loop = sequence.loop.sub_sequence.contents
        device.sequence_from_metrics(metrics, chirp_loop)

        # set remaining chirp parameters which are not derived from metrics
        chirp = chirp_loop.loop.sub_sequence.contents.chirp
        chirp.sample_rate_Hz = 1_000_000
        chirp.rx_mask = (1 << num_rx_antennas) - 1
        chirp.tx_mask = 1
        chirp.tx_power_level = 31
        chirp.if_gain_dB = 33
        chirp.lp_cutoff_Hz = 500000
        chirp.hp_cutoff_Hz = 80000

        device.set_acquisition_sequence(sequence)

        algo = DistanceAlgo(chirp, chirp_loop.loop.num_repetitions)
        
        print("Starting measurements... Press Ctrl+C to stop.")

        try:
            while True:
                frame_contents = device.get_next_frame()
                frame_data = frame_contents[0]
                antenna_samples = frame_data[i_ant, :, :]
                distance_peak_m, distance_data = algo.compute_distance(antenna_samples)
                
                # === THIS IS THE MODIFIED PART ===
                # Convert meters to millimeters
                distance_peak_mm = distance_peak_m * 1000
                # Print the value as a whole number with "mm" units
                print(f"Distance: {distance_peak_mm:.0f} mm")
                # ===============================

        except KeyboardInterrupt:
            print("\nProgram stopped by user.")
            sys.exit(0)