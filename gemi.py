import sys
import time
from ifxradarsdk.fmcw import DeviceFmcw
from ifxradarsdk.presence import PresenceSensing
from ifxradarsdk.common.exceptions import ErrorNoDevice

# ------------------- Konfigurace -------------------
# Zde zadejte port, který jste identifikovali v systému.
# Pro váš Mac je to /dev/tty.usbmodem2101.
SERIAL_PORT = "/dev/tty.usbmodem2101"
# ---------------------------------------------------

def main():
    """Hlavní funkce pro detekci pohybu."""
    print("--- Detektor pohybu radarem Infineon BGT60 ---")
    print(f"Pokouším se připojit k zařízení na portu: {SERIAL_PORT}")

    try:
        # Použití 'with' zajistí, že se zařízení po skončení programu správně uzavře.
        # Předáme konkrétní port do konstruktoru, abychom přeskočili automatické hledání.
        with DeviceFmcw(port=SERIAL_PORT) as dev:
            print("Zařízení úspěšně připojeno.")

            # Vytvoříme instanci algoritmu pro detekci přítomnosti/pohybu
            ps = PresenceSensing(dev)

            print("Spouštím detekci... (Ukončete pomocí Ctrl+C)")
            
            # Nekonečná smyčka pro nepřetržité sledování
            while True:
                # Získáme další "snímek" (frame) dat ze senzoru
                frame = ps.get_next_frame()

                # Zkontrolujeme stav detekovaný algoritmem
                # frame.target_state == 1 znamená POHYB
                # frame.target_state == 0 znamená KLID (žádný cíl)
                if frame.target_state == 1:
                    print(">>> POHYB DETEKOVÁN! <<<")
                else:
                    print("Klid.")

                # Krátká pauza, aby se terminál nezahltil zprávami
                time.sleep(0.5)

    except KeyboardInterrupt:
        # Umožňuje čisté ukončení programu stiskem Ctrl+C
        print("\nProgram ukončen uživatelem.")
    except ErrorNoDevice:
        print(f"\nChyba: Zařízení na portu '{SERIAL_PORT}' nebylo nalezeno.")
        print("Ověřte, že je zařízení připojeno a že jste zadali správný port.")
    except Exception as e:
        # Zachytí jakékoliv další chyby
        print(f"\nDošlo k neočekávané chybě: {e}")
    finally:
        print("Detekce zastavena.")

if __name__ == "__main__":
    main()