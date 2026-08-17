from __future__ import annotations

from tga_analyzer.compat import strict_zip

import argparse
import csv
import math
from pathlib import Path


# Each component is (median particle diameter in micrometres,
# log10 standard deviation, volume fraction).  The generated curves are
# deterministic synthetic distributions for UI and plotting development; they
# are not measurements of real materials.
PROFILES: tuple[
    tuple[str, tuple[tuple[float, float, float], ...]], ...
] = (
    ("Fine_narrow", ((1.2, 0.105, 1.0),)),
    ("Medium_narrow", ((8.0, 0.125, 1.0),)),
    ("Coarse_narrow", ((55.0, 0.145, 1.0),)),
    ("Broad_unimodal", ((22.0, 0.310, 1.0),)),
    ("Fine_shoulder", ((3.2, 0.130, 0.24), (15.0, 0.205, 0.76))),
    ("Coarse_shoulder", ((28.0, 0.205, 0.72), (125.0, 0.155, 0.28))),
    ("Bimodal", ((4.5, 0.145, 0.43), (72.0, 0.185, 0.57))),
    ("Broad_bimodal", ((12.0, 0.260, 0.52), (185.0, 0.235, 0.48))),
    ("Submicron_with_agglomerates", ((0.55, 0.115, 0.78), (18.0, 0.225, 0.22))),
    (
        "Multimodal",
        ((1.8, 0.125, 0.20), (24.0, 0.205, 0.56), (210.0, 0.180, 0.24)),
    ),
)


def log_gaussian(
    particle_diameter_um: float,
    median_um: float,
    log10_sigma: float,
) -> float:
    z = (math.log10(particle_diameter_um) - math.log10(median_um)) / log10_sigma
    return math.exp(-0.5 * z * z)


def volume_frequency(
    diameters_um: list[float],
    components: tuple[tuple[float, float, float], ...],
) -> list[float]:
    distribution = [0.0] * len(diameters_um)
    for median_um, log10_sigma, fraction in components:
        component = [
            log_gaussian(diameter, median_um, log10_sigma)
            for diameter in diameters_um
        ]
        component_total = sum(component)
        for index, value in enumerate(component):
            distribution[index] += fraction * value / component_total

    total = sum(distribution)
    percentages = [round(value * 100.0 / total, 6) for value in distribution]
    # Keep the displayed bin frequencies reproducibly normalized to 100.000000%.
    residual = round(100.0 - sum(percentages), 6)
    peak_index = max(range(len(percentages)), key=percentages.__getitem__)
    percentages[peak_index] = round(percentages[peak_index] + residual, 6)
    return percentages


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    point_count = 161
    diameters_um = [10 ** (-1.0 + index * 0.025) for index in range(point_count)]

    for sample_index, (sample_name, components) in enumerate(PROFILES, start=1):
        frequencies = volume_frequency(diameters_um, components)
        output_path = output_dir / f"ParticleSize_demo_{sample_index:02d}.csv"
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(
                ("Record_ID", "ParticleDiameter_um", "VolumeFrequency_percent")
            )
            for record_id, (diameter, frequency) in enumerate(
                strict_zip(diameters_um, frequencies, context='generate_particle_size_demo.py:78'), start=1
            ):
                writer.writerow(
                    (record_id, f"{diameter:.6f}", f"{frequency:.6f}")
                )
        print(
            f"{output_path.name}: {sample_name}, {point_count} points, "
            f"sum={sum(frequencies):.6f}%"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic particle-size volume-frequency demo CSV files"
    )
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    generate(args.output_dir)


if __name__ == "__main__":
    main()
