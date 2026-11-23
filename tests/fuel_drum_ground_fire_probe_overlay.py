"""Materialize an opt-in, read-only stock ground-fire observation overlay."""

import argparse
from pathlib import Path


REPLACEMENTS = {
    'local canisters = require("native_explosive_canisters")': '''local canisters = require("native_explosive_canisters")
local ground_probe = require("fuel_drum_ground_fire_probe")''',
    '        canisters.created(actor, class, seat, replica)': '''        if ground_probe.created then ground_probe.created(actor, class, seat) end
        canisters.created(actor, class, seat, replica)''',
    '            canisters.ended(address, destroyed)': '''            if ground_probe.ended then ground_probe.ended(actor, destroyed) end
            canisters.ended(address, destroyed)''',
    '    for _, canister in ipairs(canisters.rows(state, phase, row)) do rows[#rows + 1] = canister end': '''    for _, canister in ipairs(canisters.rows(state, phase, row)) do rows[#rows + 1] = canister end
    if ground_probe.rows then
        for _, sample in ipairs(ground_probe.rows(state, phase, row)) do rows[#rows + 1] = sample end
    end''',
}


def materialize(scripts: Path, output: Path):
    scripts, output = scripts.resolve(strict=True), output.resolve()
    if scripts == output or output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty dedicated diagnostic output directory")
    fields = (scripts / "native_anomaly_fields.lua").read_text(encoding="utf-8")
    for expected, replacement in REPLACEMENTS.items():
        if fields.count(expected) != 1:
            raise ValueError("The source does not match the known stock field observation path")
        fields = fields.replace(expected, replacement)
    probe = Path(__file__).with_name("fixtures") / "fuel_drum_ground_fire_probe.lua"
    output.mkdir(parents=True, exist_ok=True)
    (output / "native_anomaly_fields.lua").write_text(fields, newline="\n", encoding="utf-8")
    (output / probe.name).write_bytes(probe.read_bytes())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripts", type=Path, required=True, help="unmodified exact release Lua script directory")
    parser.add_argument("--output", type=Path, required=True, help="empty project-only overlay output directory")
    args = parser.parse_args()
    materialize(args.scripts, args.output)
