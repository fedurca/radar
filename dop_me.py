import argparse
import sys
import numpy as np
from scipy import signal

# --- THIS IS THE FIXED LINE ---
from ifxradarsdk import get_version_full
# ------------------------------

from ifxradarsdk.fmcw import DeviceFmcw
from ifxradarsdk.fmcw.types import FmcwSimpleSequenceConfig, FmcwMetrics

# ===========================================================================
# Algorithm Class
# ===========================================================================
class DopplerAlgo:
    """
    Algorithm for processing FMCW radar data to get a Range-Doppler Map
    and extract the strongest target's distance and speed.
    """
    def __init__(self, num_samples_per_chirp, num_chirps_per_frame, metrics):
        self.num_samples_per_chirp = int(num_samples_per_chirp)
        self.num_chirps_per_frame = int(num_chirps_per_frame)

        self.range_window = signal.windows.blackmanharris(self.num_samples_per_chirp)
        self.doppler_window = signal.windows.blackmanharris(self.num_chirps_per_frame)

        self.range_axis = self._calculate_range_axis(metrics.range_resolution_m)
        self.speed_axis = self._calculate_speed_axis(metrics.max_speed_m_s, metrics.speed_resolution_m_s)

    def _calculate_range_axis(self, range_resolution_m):
        return np.arange(self.num_samples_per_chirp // 2) * range_resolution_m

    def _calculate_speed_axis(self, max_speed_m_s, speed_resolution_m_s):
        num_speed_bins = int(2 * max_speed_m_s / speed_resolution_m_s)
        return np.linspace(-max_speed_m_s, max_speed_m_s, num_speed_bins)

    def compute_doppler_map(self, frame_data, i_ant=0):
        # --- 1. Range FFT ---
        range_data = frame_data * self.range_window
        range_fft = np.fft.fft(range_data, axis=1)
        range_fft = range_fft[:, :self.num_samples_per_chirp // 2]

        # --- Add MTI filter to remove stationary DC offset ---
        range_fft_mti = range_fft - np.mean(range_fft, axis=0)
        
        # --- 2. Doppler FFT ---
        doppler_data = range_fft_mti * self.doppler_window[:, np.newaxis]
        range_doppler_map = np.fft.fftshift(np.fft.fft(doppler_data, axis=0), axes=0)
        
        range_doppler_map_abs = np.abs(range_doppler_map)
        
        # --- 3. Find the peak ---
        peak_idx = np.unravel_index(np.argmax(range_doppler_map_abs), range_doppler_map_abs.shape)
        doppler_idx, range_idx = peak_idx
        
        target_distance_m = self.range_axis[range_idx]
        target_speed_m_s = self.speed_axis[doppler_idx]

        return target_distance_m, target_speed_m_s, range_doppler_map_abs

# ===========================================================================
# Main Logic
# ===========================================================================
def parse_program_arguments(description, def_frate):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-f', '--frate', type=int, default=def_frate,
                        help="frame rate in Hz, default " + str(def_frate))
    return parser.parse_args()

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
        
        algo = DopplerAlgo(chirp.num_samples, chirp_loop.loop.num_repetitions, metrics)
        
        print("\nStarting measurements... Press Ctrl+C to stop.")

        try:
            while True:
                frame_contents = device.get_next_frame()
                frame_data = frame_contents[0]
                antenna_samples = frame_data[i_ant, :, :]
                
                distance_m, speed_m_s, range_doppler_map = algo.compute_doppler_map(antenna_samples)
                
                distance_mm = distance_m * 1000

                if abs(speed_m_s) < metrics.speed_resolution_m_s:
                    direction = "Static"
                elif speed_m_s > 0:
                    direction = "Approaching"
                else:
                    direction = "Receding"

                output = (f"Distance: {distance_mm:4.0f} mm | "
                          f"Speed: {speed_m_s:+.2f} m/s "
                          f"({direction})")
                print(output)

        except KeyboardInterrupt:
            print("\nProgram stopped by user.")
            sys.exit(0)
        except Exception as e:
            print(f"\nAn unexpected error occurred: {e}")
            sys.exit(1)