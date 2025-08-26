# python >=3.9
# pip install numpy
# pip install /cesta/k/ifxradarsdk-<verze>-py3-none-*.whl

import time
import numpy as np

from ifxradarsdk.fmcw import DeviceFmcw
from ifxradarsdk.fmcw.types import FmcwSimpleSequenceConfig, FmcwMetrics

def print_devices():
    # Vypíše dostupné radary (včetně UUID), hodí se když máš víc kusů
    devs = DeviceFmcw.get_list()
    if not devs:
        print("Nenalezeno žádné FMCW zařízení.")
        return
    print("Nalezená zařízení:")
    for d in devs:
        # Objekt má obvykle atributy jako uuid, port apod.
        print(d)

def make_sequence_for_15m(dev: DeviceFmcw, fps: float = 60.0):
    """
    Sestaví jednoduchou sekvenci z metrik: ~15 m dosah + rozumné rozlišení.
    Používá 60.75 GHz střední kmitočet UTR11 a 1 RX / 1 TX.
    """
    # 1) metriky (SDK si podle nich dopočítá rampu / šířku pásma atd.)
    metrics = FmcwMetrics(
        range_resolution_m=0.05,         # ~5 cm (pro 15 m je to fajn)
        max_range_m=15.0,                # cílový dosah
        max_speed_m_s=0.0,               # statická vzdálenost → rychlost neřešíme
        speed_resolution_m_s=0.0,
        center_frequency_Hz=60_750_000_000
    )

    # 2) vytvoř „simple“ sekvenci a doplň podle metrik
    seq = dev.create_simple_sequence(FmcwSimpleSequenceConfig())
    seq.loop.repetition_time_s = 1.0 / fps

    # Získáme „chirp loop“ a necháme SDK převést metriky → parametry chirpu
    chirp_loop = seq.loop.sub_sequence.contents
    dev.sequence_from_metrics(metrics, chirp_loop)

    # 3) doladění parametrů chirpu, které metriky neřeší
    chirp = chirp_loop.loop.sub_sequence.contents.chirp
    chirp.sample_rate_Hz = 1_000_000     # 1 MS/s (bezpečná volba)
    chirp.rx_mask = 0b0001               # UTR11 = 1 RX
    chirp.tx_mask = 0b0001               # 1 TX
    chirp.tx_power_level = 31
    chirp.if_gain_dB = 33
    chirp.lp_cutoff_Hz = 500_000
    chirp.hp_cutoff_Hz = 80_000

    return seq

def main(n_frames: int = 200, fps: float = 60.0, save_npy: bool = False):
    print_devices()

    # Otevře první dostupné zařízení (alternativně DeviceFmcw(uuid="..."))
    with DeviceFmcw() as dev:
        seq = make_sequence_for_15m(dev, fps=fps)
        dev.set_acquisition_sequence(seq)

        print("Zahajuji čtení snímků…")
        t0 = time.time()
        frames = []

        for i in range(n_frames):
            frame = dev.get_next_frame()
            # 'frame' je strukturovaný objekt; v praxi pracujeme s frame[0]
            # a potom s maticí [rx, samples, chirps]. Ověříme si rozměry:
            f0 = frame[0]
            if i == 0:
                try:
                    print("Tvar frame[0]:", np.array(f0).shape, "dtype:", np.array(f0).dtype)
                except Exception:
                    pass

            if save_npy:
                frames.append(np.array(f0))

            # Tady můžeš přidat vlastní zpracování (FFT do vzdálenosti apod.)

        dt = time.time() - t0
        print(f"Hotovo. Přečteno {n_frames} snímků za {dt:.2f} s (~{n_frames/dt:.1f} fps).")

        if save_npy and frames:
            arr = np.stack(frames)
            np.save("bgt60utr11_frames.npy", arr)
            print("Uloženo do bgt60utr11_frames.npy")

if __name__ == "__main__":
    # Nastav n_frames=0 pro nekonečné čtení (podle potřeby si připiš while True)
    main(n_frames=200, fps=60.0, save_npy=False)
