from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def gaussian(x: float, center: float, width: float, height: float) -> float:
    return height * math.exp(-0.5 * ((x - center) / width) ** 2)


# Synthetic spectra for UI and processing development. They are not reference
# spectra and must not be used to identify real substances.
SPECTRA: tuple[tuple[str, tuple[tuple[float, float, float], ...]], ...] = (
    ("Aromatic_UV", ((224, 13, 1.18), (278, 18, 0.62))),
    ("Conjugated_UV", ((252, 18, 0.86), (348, 31, 0.73))),
    ("Blue_absorber", ((286, 25, 0.38), (432, 29, 1.12))),
    ("Green_absorber", ((272, 22, 0.44), (518, 38, 1.28))),
    ("Red_absorber", ((310, 34, 0.39), (612, 44, 1.05))),
    ("Broad_visible", ((236, 17, 0.52), (412, 76, 0.92))),
    ("Dual_visible", ((260, 20, 0.46), (465, 30, 0.72), (575, 35, 0.83))),
    ("Chlorophyll_like", ((220, 14, 0.58), (430, 24, 1.06), (664, 22, 0.79))),
    ("Protein_like", ((218, 12, 1.34), (280, 16, 0.36))),
    ("Mixed_band", ((245, 16, 0.71), (335, 27, 0.55), (542, 48, 0.66))),
)


def absorbance(
    wavelength_nm: float,
    sample_index: int,
    peaks: tuple[tuple[float, float, float], ...],
) -> float:
    baseline = 0.012 + sample_index * 0.0015
    baseline += 0.012 * (wavelength_nm - 200.0) / 600.0
    uv_scattering = (0.055 + sample_index * 0.002) * math.exp(
        -(wavelength_nm - 200.0) / 72.0
    )
    bands = sum(
        gaussian(
            wavelength_nm,
            center + sample_index * 0.18,
            width,
            height,
        )
        for center, width, height in peaks
    )
    deterministic_noise = 0.0022 * math.sin(
        wavelength_nm * (0.071 + sample_index * 0.0011)
    )
    deterministic_noise += 0.0011 * math.cos(
        wavelength_nm * (0.137 + sample_index * 0.0007)
    )
    return max(0.0, baseline + uv_scattering + bands + deterministic_noise)


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for sample_index, (sample_name, peaks) in enumerate(SPECTRA):
        output_path = output_dir / f"UVVis_demo_{sample_index + 1:02d}.csv"
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(("Record_ID", "Wavelength_nm", "Absorbance"))
            for record_id, wavelength in enumerate(range(200, 801), start=1):
                writer.writerow(
                    (
                        record_id,
                        f"{wavelength:.3f}",
                        f"{absorbance(wavelength, sample_index, peaks):.6f}",
                    )
                )
        print(f"{output_path.name}: {sample_name}, 601 points")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic absorbance UV-vis demo CSV files"
    )
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    generate(args.output_dir)


if __name__ == "__main__":
    main()
