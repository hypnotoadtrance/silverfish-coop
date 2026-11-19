"""Regenerate the native exit and Lua wall registries from the Python catalog."""

import argparse
from itertools import groupby
import json
from pathlib import Path

from .authored_routes import AUTHORED_ROUTES, MAPS, ROUTE_MAPS


HEADER = """#pragma once

// Generated from tools/silverfish/authored_routes.py.
// Regenerate with: python -m tools.silverfish.generate_authored_exit_registry

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>

#include <String/StringType.hpp>

namespace silverfish::authored_exits
{
    enum class Mode : std::uint8_t
    {
        overlap,
        door,
    };

    namespace detail
    {
        struct World
        {
            RC::StringViewType path;
            RC::StringViewType package;
        };

"""

ROUTE = """
    struct Route
    {
        RC::StringViewType source_world;
        RC::StringViewType source_actor;
        Mode mode;
        RC::StringViewType destination_world;
        RC::StringViewType destination_package;
        RC::StringViewType entrance;
        RC::StringViewType arrival_actor;
        // Door child names identify cooked exports. Match live ownership and
        // component pointers when qualifying the two ChildActor instances.
        RC::StringViewType source_child;
        RC::StringViewType arrival_child;
        std::optional<std::int32_t> travel_time_step;

        constexpr Route(detail::World source, detail::World destination, Mode mode,
                        RC::StringViewType actor, RC::StringViewType entrance, RC::StringViewType arrival,
                        std::optional<std::int32_t> time_step = std::nullopt,
                        RC::StringViewType travel_child = {}, RC::StringViewType arrival_child = {}) noexcept
            : source_world{source.path}, source_actor{actor}, mode{mode},
              destination_world{destination.path}, destination_package{destination.package},
              entrance{entrance}, arrival_actor{arrival}, source_child{travel_child},
              arrival_child{arrival_child}, travel_time_step{time_step}
        {
        }
    };

    inline constexpr std::array routes{
"""

LOOKUP = """    };

    static_assert([] {
        for (std::size_t i = 1; i < routes.size(); ++i)
        {
            const auto& previous = routes[i - 1];
            const auto& current = routes[i];
            if (previous.source_world > current.source_world ||
                (previous.source_world == current.source_world && previous.source_actor >= current.source_actor))
            {
                return false;
            }
        }
        return true;
    }(), "Authored exits must have unique, sorted source identities");

    [[nodiscard]] constexpr auto for_world(RC::StringViewType world) noexcept -> std::span<const Route>
    {
        const auto* end = routes.data() + routes.size();
        const auto* first = std::lower_bound(routes.data(), end, world,
                                            [](const Route& route, auto value) { return route.source_world < value; });
        const auto* last = std::upper_bound(first, end, world,
                                           [](auto value, const Route& route) { return value < route.source_world; });
        return {first, last};
    }

    [[nodiscard]] constexpr auto find(RC::StringViewType world, RC::StringViewType actor) noexcept -> const Route*
    {
        const auto entries = for_world(world);
        const auto found = std::lower_bound(entries.begin(), entries.end(), actor,
                                            [](const Route& route, auto value) { return route.source_actor < value; });
        return found != entries.end() && found->source_actor == actor ? &*found : nullptr;
    }
}
"""


def basename(package: str) -> str:
    return package.rsplit("/", 1)[1]


def literal(value: str) -> str:
    return f"STR({json.dumps(value)})"


def render() -> str:
    worlds = {basename(package): package for package in ROUTE_MAPS}
    if len(worlds) != len(ROUTE_MAPS) or any(not name.isascii() or not name.isidentifier() for name in worlds):
        raise ValueError("Authored route worlds need unique C++ identifiers")

    content = [HEADER]
    for name, package in sorted(worlds.items()):
        content.append(f"        inline constexpr World {name}{{{literal(package + '.' + name)},\n"
                       f"            {literal(package)}}};\n")
    content.extend(("    }\n", ROUTE))

    entries = sorted(AUTHORED_ROUTES, key=lambda route: (route.source, route.actor))
    for route in entries:
        fields = [literal(route.actor), literal(route.entrance), literal(route.arrival_actor)]
        if route.travel_time_step is not None or route.child is not None:
            fields.append(str(route.travel_time_step) if route.travel_time_step is not None else "std::nullopt")
        content.append(f"        Route{{detail::{basename(route.source)}, detail::{basename(route.destination)}, "
                       f"Mode::{route.kind},\n            " + ", ".join(fields))
        if route.child is not None:
            if route.arrival_child is None:
                raise ValueError("An authored door requires both cooked child export names")
            content.append(f",\n            {literal(route.child)}, {literal(route.arrival_child)}")
        content.append("},\n")
    content.append(LOOKUP)
    return "".join(content)


def render_wall_routes() -> str:
    content = ["-- Generated from tools/silverfish/authored_routes.py.\nreturn {\n"]
    entries = sorted(AUTHORED_ROUTES, key=lambda route: (route.source, route.actor))
    for package, routes in groupby(entries, key=lambda route: route.source):
        content.append(f"    [{json.dumps(package)}] = {{\n")
        for route in routes:
            content.append(f"        {{ actor = {json.dumps(route.actor)}, kind = {json.dumps(route.kind)}, "
                           f"entrance = {json.dumps(route.entrance)} }},\n")
        content.append("    },\n")
    content.append("}\n")
    return "".join(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the checked-in registries without writing them")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    maps = "-- Generated from tools/silverfish/authored_routes.py.\nreturn {\n" + "".join(
        f"    {json.dumps(package)},\n" for package in sorted(MAPS)
    ) + "}\n"
    outputs = {
        root / "native" / "authored_exit_registry.hpp": render(),
        root / "mod" / "SilverfishCoop" / "Scripts" / "authored_zone_maps.lua": maps,
        root / "mod" / "SilverfishCoop" / "Scripts" / "authored_wall_routes.lua": render_wall_routes(),
    }
    for target, expected in outputs.items():
        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != expected:
                parser.error(f"{target.name} needs regeneration")
        else:
            target.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
