import numpy as np
from scipy import signal

class DopplerAlgo:
    """
    Algorithm for processing FMCW radar data to get a Range-Doppler Map
    and extract the strongest target's distance and speed.
    """
    def __init__(self, num_samples_per_chirp, num_chirps_per_frame, metrics):
        self.num_samples_per_chirp = int(num_samples_per_chirp)
        self.num_chirps_per_frame = int(num_chirps_per_frame)

        # Create windowing functions for FFT
        self.range_window = signal.windows.blackmanharris(self.num_samples_per_chirp)
        self.doppler_window = signal.windows.blackmanharris(self.num_chirps_per_frame)

        # Calculate scaling factors for distance and speed from metrics
        self.range_axis = self._calculate_range_axis(metrics.range_resolution_m)
        self.speed_axis = self._calculate_speed_axis(metrics.max_speed_m_s, metrics.speed_resolution_m_s)

    def _calculate_range_axis(self, range_resolution_m):
        return np.arange(self.num_samples_per_chirp // 2) * range_resolution_m

    def _calculate_speed_axis(self, max_speed_m_s, speed_resolution_m_s):
        num_speed_bins = int(2 * max_speed_m_s / speed_resolution_m_s)
        return np.linspace(-max_speed_m_s, max_speed_m_s, num_speed_bins)

    def compute_doppler_map(self, frame_data, i_ant=0):
        """
        Computes the Range-Doppler Map and finds the peak.
        Note: The i_ant argument is kept for API compatibility but is unused
              since this implementation processes the already selected antenna data.
        """
        # --- 1. Range FFT ---
        # Apply window across the samples (rows)
        range_data = frame_data * self.range_window
        # Perform FFT across the samples (rows)
        range_fft = np.fft.fft(range_data, axis=1)
        # We only need the first half of the FFT result
        range_fft = range_fft[:, :self.num_samples_per_chirp // 2]

        # --- 2. Doppler FFT ---
        # Apply window across the chirps (columns)
        doppler_data = range_fft * self.doppler_window[:, np.newaxis]
        # Perform FFT across the chirps (columns) and shift the zero-frequency component to the center
        range_doppler_map = np.fft.fftshift(np.fft.fft(doppler_data, axis=0), axes=0)
        
        # Get the absolute values (magnitude)
        range_doppler_map_abs = np.abs(range_doppler_map)
        
        # --- 3. Find the peak ---
        # Find the indices of the maximum value in the 2D map
        peak_idx = np.unravel_index(np.argmax(range_doppler_map_abs), range_doppler_map_abs.shape)
        doppler_idx, range_idx = peak_idx
        
        # Map indices to physical values
        target_distance_m = self.range_axis[range_idx]
        target_speed_m_s = self.speed_axis[doppler_idx]

        return target_distance_m, target_speed_m_s, range_doppler_map_abs