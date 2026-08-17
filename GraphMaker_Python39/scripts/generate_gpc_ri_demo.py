from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def gaussian(x: float, center: float, width: float, height: float) -> float:
    return height * math.exp(-0.5 * ((x - center) / width) ** 2)


# Retention time (min), standard deviation (min), and RI response (mV).
# These deterministic profiles are synthetic and do not encode a calibration
# curve or real molecular-weight values.
PROFILES: tuple[tuple[str, tuple[tuple[float, float, float], ...]], ...] = (
    ("Narrow_early", ((10.8, 0.52, 54.0),)),
    ("Narrow_middle", ((12.4, 0.63, 61.0),)),
    ("Narrow_late", ((15.8, 0.58, 49.0),)),
    ("Broad_unimodal", ((13.5, 1.48, 45.0),)),
    ("High_MW_shoulder", ((11.8, 0.92, 49.0), (9.7, 0.58, 14.0))),
    ("Low_MW_shoulder", ((13.2, 0.86, 52.0), (15.4, 0.64, 17.0))),
    ("Bimodal", ((10.9, 0.72, 36.0), (14.8, 0.93, 42.0))),
    ("Broad_bimodal", ((12.0, 1.28, 32.0), (16.2, 1.17, 29.0))),
    ("Low_response", ((14.1, 1.05, 21.0),)),
    ("Multimodal", ((10.2, 0.55, 20.0), (13.1, 0.82, 46.0), (16.8, 0.68, 16.0))),
)


def ri_response(
    retention_time_min: float,
    sample_index: int,
    peaks: tuple[tuple[float, float, float], ...],
) -> float:
    baseline = 0.55 + sample_index * 0.025
    baseline += 0.017 * retention_time_min
    baseline += 0.12 * math.exp(-retention_time_min / 7.5)
    response = sum(
        gaussian(
            retention_time_min,
            center + sample_index * 0.015,
            width,
            height,
        )
        for center, width, height in peaks
    )
    injection_disturbance = gaussian(
        retention_time_min, 3.25 + sample_index * 0.01, 0.16, 1.2
    )
    deterministic_noise = 0.045 * math.sin(
        retention_time_min * (5.2 + sample_index * 0.07)
    )
    deterministic_noise += 0.018 * math.cos(
        retention_time_min * (12.7 + sample_index * 0.03)
    )
    return baseline + response + injection_disturbance + deterministic_noise


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    point_count = 1501
    for sample_index, (sample_name, peaks) in enumerate(PROFILES):
        output_path = output_dir / f"GPC_RI_demo_{sample_index + 1:02d}.csv"
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(("Record_ID", "RetentionTime_min", "RI_mV"))
            for offset in range(point_count):
                retention_time = offset * 0.02
                writer.writerow(
                    (
                        offset + 1,
                        f"{retention_time:.3f}",
                        f"{ri_response(retention_time, sample_index, peaks):.6f}",
                    )
                )
        print(f"{output_path.name}: {sample_name}, {point_count} points")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic GPC refractive-index detector demo CSV files"
    )
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    generate(args.output_dir)


if __name__ == "__main__":
    main()
