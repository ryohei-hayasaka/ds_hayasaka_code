from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def gaussian(x: float, center: float, width: float, depth: float) -> float:
    """Return a downward Gaussian absorption band in transmittance units."""
    return depth * math.exp(-0.5 * ((x - center) / width) ** 2)


# Polymer-like demonstration spectra. These are synthetic curves and are not
# intended to be reference spectra for material identification.
SPECTRA: tuple[tuple[str, tuple[tuple[float, float, float], ...]], ...] = (
    (
        "PE_like",
        (
            (2916, 24, 43), (2848, 22, 38), (1472, 18, 25),
            (1462, 15, 17), (730, 10, 30), (720, 9, 27),
        ),
    ),
    (
        "PP_like",
        (
            (2950, 22, 35), (2918, 20, 29), (2838, 21, 24),
            (1455, 20, 28), (1377, 16, 24), (1167, 15, 19),
            (997, 12, 25), (973, 11, 21), (841, 12, 24),
        ),
    ),
    (
        "PS_like",
        (
            (3080, 18, 10), (3060, 16, 12), (3025, 17, 18),
            (2922, 22, 17), (2850, 22, 10), (1601, 15, 25),
            (1493, 16, 29), (1452, 18, 20), (1028, 13, 18),
            (758, 10, 38), (698, 10, 43),
        ),
    ),
    (
        "PMMA_like",
        (
            (2995, 22, 17), (2950, 22, 21), (1728, 24, 62),
            (1435, 18, 20), (1385, 16, 15), (1260, 19, 30),
            (1190, 17, 25), (1145, 18, 39), (990, 14, 15),
            (750, 14, 12),
        ),
    ),
    (
        "PET_like",
        (
            (2970, 24, 15), (1715, 23, 59), (1578, 15, 15),
            (1505, 14, 18), (1409, 16, 19), (1242, 19, 42),
            (1095, 17, 36), (1017, 13, 16), (872, 11, 27),
            (725, 12, 34),
        ),
    ),
    (
        "PA6_like",
        (
            (3300, 75, 32), (2930, 24, 23), (2860, 23, 17),
            (1640, 24, 46), (1540, 22, 42), (1465, 18, 19),
            (1260, 20, 18), (690, 22, 17),
        ),
    ),
    (
        "PVA_like",
        (
            (3350, 120, 48), (2940, 28, 19), (1730, 23, 12),
            (1420, 22, 20), (1325, 20, 15), (1090, 28, 44),
            (850, 19, 14),
        ),
    ),
    (
        "PDMS_like",
        (
            (2962, 23, 22), (2905, 21, 10), (1410, 17, 14),
            (1260, 17, 40), (1095, 24, 48), (1020, 24, 50),
            (800, 17, 57),
        ),
    ),
    (
        "PU_like",
        (
            (3320, 80, 30), (2930, 25, 23), (2860, 23, 15),
            (1725, 25, 49), (1595, 19, 17), (1530, 23, 34),
            (1460, 18, 16), (1225, 23, 31), (1100, 28, 35),
            (760, 17, 13),
        ),
    ),
    (
        "PC_like",
        (
            (3050, 20, 11), (2965, 24, 20), (1775, 24, 57),
            (1600, 17, 20), (1505, 17, 26), (1410, 18, 13),
            (1220, 22, 42), (1185, 20, 29), (1015, 15, 18),
            (830, 13, 25), (760, 12, 21),
        ),
    ),
)


def transmittance(
    wavenumber: float,
    sample_index: int,
    peaks: tuple[tuple[float, float, float], ...],
) -> float:
    baseline = 96.5 - sample_index * 0.12
    baseline += 0.75 * math.sin(wavenumber / 690.0 + sample_index * 0.31)
    baseline += 0.25 * math.cos(wavenumber / 215.0 + sample_index * 0.47)
    absorption = sum(
        gaussian(
            wavenumber,
            center + sample_index * 0.15,
            width,
            depth,
        )
        for center, width, depth in peaks
    )
    deterministic_noise = 0.10 * math.sin(
        wavenumber * (0.071 + sample_index * 0.0017)
    )
    return min(100.0, max(1.0, baseline - absorption + deterministic_noise))


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for sample_index, (sample_name, peaks) in enumerate(SPECTRA):
        output_path = output_dir / f"IR_demo_{sample_index + 1:02d}.csv"
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(
                ("Record_ID", "Wavenumber_cm-1", "Absorbance")
            )
            for record_id, wavenumber in enumerate(range(4000, 399, -2), start=1):
                transmittance_percent = transmittance(
                    wavenumber, sample_index, peaks
                )
                absorbance = -math.log10(transmittance_percent / 100.0)
                writer.writerow(
                    (record_id, f"{wavenumber:.3f}", f"{absorbance:.6f}")
                )
        print(f"{output_path.name}: {sample_name}, 1801 points")

    blank_path = output_dir / "IR_blank.csv"
    with blank_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("Record_ID", "Wavenumber_cm-1", "Absorbance"))
        for record_id, wavenumber in enumerate(range(4000, 399, -2), start=1):
            progress = (4000 - wavenumber) / 3600.0
            absorbance = (
                0.0045
                + 0.0012 * progress
                + 0.00030 * math.sin(wavenumber / 185.0)
                + 0.00012 * math.cos(wavenumber / 71.0)
            )
            writer.writerow(
                (record_id, f"{wavenumber:.3f}", f"{absorbance:.6f}")
            )
    print(f"{blank_path.name}: blank baseline, 1801 points")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic FT-IR demo CSV files")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    generate(args.output_dir)


if __name__ == "__main__":
    main()
