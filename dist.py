#!/usr/bin/env python3
import numpy as np, time, sys
from ifxradarsdk.fmcw import DeviceFmcw
from ifxradarsdk.fmcw.types import FmcwSimpleSequenceConfig, FmcwMetrics

def make_seq(dev, fps=50.0):
    m = FmcwMetrics(
        range_resolution_m=0.05,
        max_range_m=15.0,
        max_speed_m_s=0.0,
        speed_resolution_m_s=0.0,
        center_frequency_Hz=60_750_000_000
    )
    seq = dev.create_simple_sequence(FmcwSimpleSequenceConfig())
    seq.loop.repetition_time_s = 1.0 / fps
    chirp_loop = seq.loop.sub_sequence.contents
    dev.sequence_from_metrics(m, chirp_loop)
    chirp = chirp_loop.loop.sub_sequence.contents.chirp
    chirp.sample_rate_Hz = 1_000_000
    chirp.rx_mask = 0b0001
    chirp.tx_mask = 0b0001
    chirp.tx_power_level = 31
    chirp.if_gain_dB = 33
    chirp.lp_cutoff_Hz = 500_000
    chirp.hp_cutoff_Hz = 80_000
    return seq

def main(n=100):
    devs = DeviceFmcw.get_list()
    if not devs:
        print("SDK nenašlo zařízení. Zkontroluj USB/FW.", file=sys.stderr); sys.exit(1)
    uuid = str(getattr(devs[0], "uuid", devs[0]))
    with DeviceFmcw(uuid=uuid) as dev:
        dev.set_acquisition_sequence(make_seq(dev))
        t0 = time.time()
        for i in range(n):
            frame = dev.get_next_frame()
            if i == 0:
                a = np.array(frame[0])
                print("Frame0 shape:", a.shape, "dtype:", a.dtype)
        print(f"OK, {n} snímků za {time.time()-t0:.2f}s")

if __name__ == "__main__":
    main()

