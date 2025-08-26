from ifxradarsdk.fmcw import DeviceFmcw
from ifxradarsdk.common.exceptions import ErrorNoDevice
import numpy as np

# Zadejte svůj port pro Mac
SERIAL_PORT = "/dev/tty.usbmodem2101"

def main():
    print("--- Test komunikace s radarem (starší SDK) ---")
    try:
        # Použijeme 'with', aby se zařízení správně uzavřelo
        with DeviceFmcw(port=SERIAL_PORT) as dev:
            print("✅ Zařízení úspěšně připojeno.")

            # Nastavíme jednoduchou konfiguraci (může být nutné upravit pro váš senzor)
            config = dev.get_config()
            dev.set_config(config)

            # Zkusíme načíst jeden frame dat
            frame = dev.get_next_frame()

            # Zkontrolujeme, zda frame obsahuje data
            if frame is not None:
                print("✅ Úspěšně načten frame se surovými daty.")
                # Příklad, jak se podívat na data (bude to matice čísel)
                # Získáme data z první antény
                mat = frame[0]
                print(f"   - Rozměry datové matice: {mat.shape}")
                print(f"   - Typ dat: {mat.dtype}")
            else:
                print("❌ Nepodařilo se načíst frame.")

    except ErrorNoDevice:
        print(f"❌ Chyba: Zařízení na portu '{SERIAL_PORT}' nebylo nalezeno.")
    except Exception as e:
        print(f"❌ Došlo k neočekávané chybě: {e}")

if __name__ == "__main__":
    main()