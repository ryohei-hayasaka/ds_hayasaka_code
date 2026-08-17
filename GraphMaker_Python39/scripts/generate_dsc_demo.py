from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def gaussian(x: float, center: float, width: float, height: float) -> float:
    return height * math.exp(-0.5 * ((x - center) / width) ** 2)


def generate(output_dir: Path, sample_count: int = 10) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for sample_index in range(sample_count):
        path = output_dir / f"DSC_demo_{sample_index + 1:02d}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("Record_ID", "Time_min", "Temperature_C", "HeatFlow_mW"))
            for record_id, temperature in enumerate(range(20, 301), start=1):
                time_min = (temperature - 20) / 10.0
                baseline = -0.15 + 0.0008 * (temperature - 20)
                glass_transition = 0.18 * math.tanh((temperature - (78 + sample_index)) / 4.0)
                melting = gaussian(
                    temperature,
                    center=150 + sample_index * 2.2,
                    width=7.0 + sample_index * 0.2,
                    height=2.1 + sample_index * 0.12,
                )
                crystallization = gaussian(
                    temperature,
                    center=112 + sample_index * 1.4,
                    width=5.5,
                    height=-(0.75 + sample_index * 0.05),
                )
                heat_flow = baseline + glass_transition + melting + crystallization
                writer.writerow(
                    (
                        record_id,
                        f"{time_min:.3f}",
                        f"{temperature:.3f}",
                        f"{heat_flow:.6f}",
                    )
                )

    blank_path = output_dir / "DSC_blank.csv"
    with blank_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("Record_ID", "Time_min", "Temperature_C", "HeatFlow_mW"))
        for record_id, temperature in enumerate(range(20, 301), start=1):
            time_min = (temperature - 20) / 10.0
            progress = (temperature - 20) / 280.0
            heat_flow = (
                -0.018
                + 0.026 * progress
                + 0.0018 * math.sin(temperature / 31.0)
                + 0.0007 * math.cos(temperature / 13.0)
            )
            writer.writerow(
                (
                    record_id,
                    f"{time_min:.3f}",
                    f"{temperature:.3f}",
                    f"{heat_flow:.6f}",
                )
            )
    print(f"{blank_path.name}: blank baseline, 281 points")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate DSC demo CSV files")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    generate(args.output_dir, args.count)


if __name__ == "__main__":
    main()
