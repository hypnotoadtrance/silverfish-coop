local switches = {}
local stock_door = require("native_stock_door")
local guest_save = require("guest_save")
local world_path = require("world_path")

local root = "/Game/Classes/ScriptingActors/"
local profiles = {
    CircutBox_BP_C = {"SM_Switch_Lever_Stick_02", "LightPanel"},
    LightSwitch_BP_C = {"Switch_Mesh", "SwitchPanel"},
    LightSwitch_02_BP_C = {"Switch_Mesh", "SwitchPanel"},
}
local props = "/Game/Assets/StaticMeshes/Mesh_Props/"
profiles.Prop_Radio01_C = {path = props .. "Prop_Radio01.Prop_Radio01_C", power = "Powered"}
for _, name in ipairs({"Prop_TV_01", "Prop_TV_02", "Prop_TV_03"}) do
    profiles[name .. "_C"] = {path = props .. name .. "." .. name .. "_C", power = "Power", television = true}
end
for _, name in ipairs({"Prop_Candle_01", "Prop_Candle_02", "Prop_Candle_03"}) do
    profiles[name .. "_C"] = {path = props .. name .. "." .. name .. "_C", power = "Lit", candle = true}
end
profiles.Prop_Lantern_01_C = {
    path = props .. "Prop_Lantern_01.Prop_Lantern_01_C", power = "Lit", candle = true, lantern = true,
}
profiles.Prop_GassLight_01_C = {
    path = props .. "Prop_GassLight_01.Prop_GassLight_01_C", power = "Lit", candle = true, gas_lamp = true,
}
profiles.DoorLatch_BP_C = {
    path = "/Game/Assets/StaticMeshes/Doors/DoorLatch_BP.DoorLatch_BP_C", latch = true,
}
local linked_door_class = "/Game/Assets/StaticMeshes/Doors/BasicDoor_Master.BasicDoor_Master_C"
local profiles_by_class = {}
for kind, profile in pairs(profiles) do
    profile.path = profile.path or root .. kind:sub(1, -3) .. "." .. kind
    profiles_by_class["BlueprintGeneratedClass " .. profile.path] = profile
end
local bulb_profiles = {
    ["/Game/Classes/DynamicLights/DynamicLight_Master_BP.DynamicLight_Master_BP_C"] = "PointLight",
    ["/Game/Classes/DynamicLights/Dynamic_SpotLight_Master_BP.Dynamic_SpotLight_Master_BP_C"] = "SpotLight",
}
local statics_path = "/Script/Engine.Default__GameplayStatics"
local world, entries, dirty, visible_streams
local candle_overlaps, retired_lamps = {}, {}
local local_power = {}
local observed_rows, observation = {}, 0
local lamp_surfaces = {}
local interact_channel, blocking = 16, 2

local function valid(object) return object and object:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end
local function bit(value) return value and "1" or "0" end

local function level_visible(level, scope)
    return valid(level) and (same(level, scope.PersistentLevel) or level.bIsVisible == true)
end

local function qualified(actor, scope, level)
    if not level then return world_path.visible_actor_level(actor, scope) end
    if not valid(actor) or not same(actor:GetOuter(), level) or actor:IsActorBeingDestroyed() then return false end
    if same(level, scope.PersistentLevel) then return same(actor:GetWorld(), scope) end
    return level.bIsVisible == true
end

local function placed_actor(name)
    if type(name) ~= "string" or #name > 384 then return end
    local path = name:match("^[A-Za-z0-9_%-]+_C (.+)$")
    return world_path.level_actor_package(path), path
end

local function inherited_profile(class)
    while valid(class) do
        local profile = profiles_by_class[class:GetFullName()]
        if profile then return profile end
        class = class:GetSuperStruct()
    end
end

local function parts(actor, scope, level)
    level = level or qualified(actor, scope)
    if not level then return end
    local class = actor:GetClass()
    local profile = inherited_profile(class)
    if not profile then return end
    if profile.latch then
        local door, box, dial = actor.Door, actor.Box_Knob, actor.SM_Prop_Kitchen_Grill_01_Dial_01
        local base = StaticFindObject(linked_door_class)
        if not valid(base) or not qualified(door, scope) or not door:IsA(base)
            or type(door.Locked) ~= "boolean" or type(door.Open) ~= "boolean" or type(door.LockBroken) ~= "boolean"
            or not valid(box) or not same(box:GetOwner(), actor) or not valid(dial) or not same(dial:GetOwner(), actor) then return end
        return {actor = actor, level = level, box = box, latch = true, door = door, lever = dial}
    end
    if type(actor[profile.power or "Powered"]) ~= "boolean" then return end
    local box = profile.candle and actor.Sphere or actor.Box
    if not valid(box) or not same(box:GetOwner(), actor) then return end
    if profile.candle then
        local mesh, light, flame = actor.PropMesh, actor.PointLight, actor.FX_CandleFlame_01_Converted
        if not same(mesh, actor.RootComponent) or not same(mesh:GetOwner(), actor)
            or not valid(light) or not same(light:GetOwner(), actor)
            or not valid(flame) or not same(flame:GetOwner(), actor) then return end
        return {actor = actor, level = level, box = box, mesh = mesh, light = light, flame = flame, power = profile.power,
            candle = true, lantern = profile.lantern, gas_lamp = profile.gas_lamp}
    end
    if profile.power then return {actor = actor, level = level, box = box, power = profile.power, television = profile.television} end
    local lever, panel = actor[profile[1]], actor[profile[2]]
    if not valid(lever) or not valid(panel) then return end
    for _, component in ipairs({lever, panel}) do
        if not same(component:GetOwner(), actor) then return end
    end
    return {actor = actor, level = level, lever = lever, panel = panel, box = box}
end

local function light_links(linked, count, scope, previous)
    local lights = {}
    if count == 0 then return lights end
    local bases = {}
    for path, component in pairs(bulb_profiles) do
        local base = StaticFindObject(path)
        if valid(base) then bases[#bases + 1] = {base, component} end
    end
    for index = 1, count do
        local target = linked[index]
        local level = valid(target) and type(target.Powered) == "boolean" and qualified(target, scope)
        if level then
            for _, base in ipairs(bases) do
                if target:IsA(base[1]) then
                    local bulb = target[base[2]]
                    if valid(bulb) and same(bulb:GetOwner(), target) then
                        local name = target:GetFullName()
                        if placed_actor(name) then
                            local old = previous and previous[name]
                            lights[name] = old and same(old.target, target) and same(old.bulb, bulb)
                                and same(old.level, level) and old or {target = target, bulb = bulb, level = level}
                        end
                    end
                    break
                end
            end
        end
    end
    return lights
end

local function refresh_lights(entry, scope, powered, force)
    local linked = entry.actor.ActorsToPower
    local count = linked and linked:GetArrayNum() or 0
    local now = os.clock()
    -- Stock script can replace a reference in place without toggling the switch or changing the array length.
    if not force and entry.link_count == count and entry.link_power == powered
        and (count == 0 or now < entry.next_link_check) then return end
    entry.lights = light_links(linked, count, scope, entry.lights)
    entry.link_count, entry.link_power, entry.next_link_check = count, powered, now + 0.75
end

local function release_lamp_surface(entry, current_world)
    local original = lamp_surfaces[entry]
    if original == nil then return end
    local mesh = entry.mesh
    if same(world, current_world) and valid(mesh) and same(mesh:GetOwner(), entry.actor)
        and not entry.actor:IsActorBeingDestroyed() and mesh:GetCollisionResponseToChannel(interact_channel) == blocking then
        mesh:SetCollisionResponseToChannel(interact_channel, original)
    end
    lamp_surfaces[entry] = nil
end

local function expose_lamp_surface(entry, powered, current_world)
    if not powered then
        release_lamp_surface(entry, current_world)
    elseif lamp_surfaces[entry] == nil and valid(entry.mesh) and same(entry.mesh:GetOwner(), entry.actor) then
        -- A lit oil lamp can be extinguished by aiming at its body as well as its fire sphere.
        local mesh = entry.mesh
        local response = mesh:GetCollisionResponseToChannel(interact_channel)
        if response == 0 or response == 1 then
            mesh:SetCollisionResponseToChannel(interact_channel, blocking)
            if mesh:GetCollisionResponseToChannel(interact_channel) == blocking then
                lamp_surfaces[entry] = response
            end
        end
    end
end

local function cache_entry(name, path, entry)
    local previous = entries[name]
    if previous then release_lamp_surface(previous, world) end
    local actor = entry.actor
    if entry.power then
        entries[name] = {actor = actor, address = actor:GetAddress(), level = entry.level,
            class = actor:GetClass(), power = entry.power,
            path = path, candle = entry.candle, lantern = entry.lantern, gas_lamp = entry.gas_lamp,
            light = entry.light, mesh = entry.mesh}
    else
        if not entry.latch then refresh_lights(entry, world, actor.Powered) end
        entries[name] = entry
    end
end

function switches.start()
    if type(NotifyOnNewObject) == "function" then
        for kind, profile in pairs(profiles) do
            NotifyOnNewObject(profile.path, function()
                -- Construction can precede BeginPlay. Read stock properties during the regular census.
                if dirty then dirty[kind] = true end
            end)
        end
    end
    world_path.start_stream_tracking()
end

local function refresh_streamed_levels()
    local current = world_path.visible_streams(world)
    if current == visible_streams then return end
    visible_streams = current
    for name, entry in pairs(entries) do
        if not valid(entry.level) then
            release_lamp_surface(entry, world)
            entries[name] = nil
        end
    end
    for kind in pairs(profiles) do dirty[kind] = true end
    return true
end

local function census(state, phase)
    if (phase ~= "connected" and phase ~= "hosting") or not state.listening
        or state.server ~= (phase == "connected") or not valid(state.world) then
        for entry in pairs(lamp_surfaces) do release_lamp_surface(entry, state.world) end
        world, entries, dirty, local_power = nil, nil, nil, {}
        observed_rows = {}
        return {}
    end
    if not same(world, state.world) then
        lamp_surfaces = {}
        world, entries, dirty, visible_streams = state.world, {}, {}, nil
        local_power, observed_rows = {}, {}
        for kind in pairs(profiles) do dirty[kind] = true end
    end
    local changed_levels = refresh_streamed_levels()
    if not next(dirty) then return entries end
    local statics = StaticFindObject(statics_path)
    if not valid(statics) then return entries end
    for kind in pairs(dirty) do
        dirty[kind] = nil
        local class = StaticFindObject(profiles[kind].path)
        if valid(class) then
            local output = {}
            statics:GetAllActorsOfClass(world, class, output)
            for _, actor in ipairs(output.OutActors or output) do
                if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
                local level = qualified(actor, world)
                if level and (phase ~= "hosting" or actor:HasAuthority()) then
                    world_path.remember_level(world, level)
                    local name = actor:GetFullName()
                    local package, path = placed_actor(name)
                    local previous = entries[name]
                    if package and (not previous or not same(previous.actor, actor)) then
                        local entry = parts(actor, world, level)
                        if entry then cache_entry(name, path, entry) end
                    elseif changed_levels and previous and previous.lights then
                        refresh_lights(previous, world, actor.Powered, true)
                    end
                end
            end
        end
    end
    return entries
end

local function find_switch(state, phase, name)
    local entry = census(state, phase)[name]
    if not entry then
        local _, path = placed_actor(name)
        if not entries or not path then return end
        local actor = StaticFindObject(path)
        local resolved = valid(actor) and actor:GetFullName() == name and parts(actor, state.world)
        if resolved and (phase ~= "hosting" or actor:HasAuthority()) then
            cache_entry(name, path, resolved)
            return resolved
        end
        return
    end
    if not entry.power then
        if qualified(entry.actor, state.world) then
            if entry.latch then return parts(entry.actor, state.world) end
            return entry
        end
        return
    end
    local actor = StaticFindObject(entry.path)
    if not valid(actor) or not same(actor:GetClass(), entry.class) then return end
    local resolved = parts(actor, state.world)
    if resolved and resolved.power == entry.power and (phase ~= "hosting" or actor:HasAuthority()) then return resolved end
end

function switches.release_candles(state, phase)
    if (phase ~= "connected" and phase ~= "hosting") or not state.listening or not same(world, state.world) then
        for entry in pairs(lamp_surfaces) do release_lamp_surface(entry, state.world) end
    end
    if not next(candle_overlaps) and not next(retired_lamps)
        or phase == "connected" and state.listening and same(world, state.world) then return end
    if same(world, state.world) then
        for _, sphere in pairs(candle_overlaps) do
            local owner = valid(sphere) and sphere:GetOwner()
            if qualified(owner, state.world) then sphere:SetGenerateOverlapEvents(true) end
        end
        for entry in pairs(retired_lamps) do
            if entry.retired and qualified(entry.actor, world) then
                entry.actor:SetActorHiddenInGame(entry.hidden)
                entry.actor:SetActorEnableCollision(entry.collision)
                entry.retired = nil
            end
        end
    end
    candle_overlaps, retired_lamps = {}, {}
end

local function suppress_candle_overlap(name, sphere)
    if sphere:GetGenerateOverlapEvents() then
        sphere:SetGenerateOverlapEvents(false)
        candle_overlaps[name] = sphere
    end
end

local function read_switch(entry, name, state, phase, row, native_world_address)
    if entry.latch then
        if qualified(entry.actor, state.world, entry.level) and qualified(entry.door, state.world)
            and valid(entry.lever) then
            local angles = entry.lever.RelativeRotation
            return row("native-stock-switch", state.map, name, bit(entry.door.Locked),
                angles.Pitch, angles.Yaw, angles.Roll, "-", "-", "door=" .. entry.door:GetFullName())
        end
    elseif entry.power then
        local actor = entry.actor
        local powered, broken
        if native_world_address then
            powered, broken = SilverfishReadNativeSwitchPower(native_world_address, entry.address, entry.path,
                entry.power, entry.lantern == true)
        end
        if powered == nil and not level_visible(entry.level, state.world) then
            release_lamp_surface(entry, state.world)
            return
        end
        if powered == nil and not qualified(actor, state.world, entry.level) then
            release_lamp_surface(entry, state.world)
            if not entry.path then return end
            actor = StaticFindObject(entry.path)
            if not qualified(actor, state.world, entry.level) or not same(actor:GetClass(), entry.class)
                or phase == "hosting" and not actor:HasAuthority() then
                if entry.lantern and phase == "hosting" then
                    return row("native-stock-switch", state.map, name, 0, 0, 0, 0, "-", "-", "removed")
                end
                return
            end
            local replacement = parts(actor, state.world)
            if not replacement then return end
            entry.actor, entry.address, entry.light, entry.mesh = actor, actor:GetAddress(), replacement.light, replacement.mesh
        end
        if powered == nil then
            powered = actor[entry.power]
            if entry.lantern then broken = actor.Broken_0 == true end
        end
        if type(powered) == "boolean" then
            if entry.candle and phase == "connected" then
                local sphere = actor.Sphere
                if valid(sphere) and same(sphere:GetOwner(), actor) then suppress_candle_overlap(name, sphere) end
            end
            local lamp_state
            local panel = "-"
            if entry.lantern then
                lamp_state = entry.retired and "removed" or broken and "broken" or "intact"
                if lamp_state ~= "intact" then powered = false end
                expose_lamp_surface(entry, powered, state.world)
            end
            if phase == "connected" and (entry.gas_lamp or lamp_state == "intact") and valid(entry.light)
                and entry.light:IsVisible() ~= powered then
                panel = "lamp-visual-mismatch"
            end
            if entry.row_power ~= powered or entry.row_lamp ~= lamp_state or entry.row_panel ~= panel
                or entry.row_encoder ~= row or entry.row_map ~= state.map then
                if lamp_state then
                    entry.status_row = row("native-stock-switch", state.map, name, bit(powered), 0, 0, 0, panel, "-", lamp_state)
                else
                    entry.status_row = row("native-stock-switch", state.map, name, bit(powered), 0, 0, 0, panel, "-")
                end
                entry.row_power, entry.row_lamp, entry.row_panel = powered, lamp_state, panel
                entry.row_encoder, entry.row_map = row, state.map
            end
            return entry.status_row
        end
    elseif level_visible(entry.level, state.world) and valid(entry.actor) and not entry.actor:IsActorBeingDestroyed()
        and valid(entry.lever) then
        local powered = entry.actor.Powered
        refresh_lights(entry, state.world, powered)
        local angles = entry.lever.RelativeRotation
        local panel = entry.panel:GetMaterial(0)
        local lights = {}
        for id, link in pairs(entry.lights) do
            if qualified(link.target, state.world, link.level) and valid(link.bulb) then
                local powered, visible = bit(link.target.Powered), bit(link.bulb:IsVisible())
                if link.row_power ~= powered or link.row_visible ~= visible then
                    link.status_row = id .. "|" .. powered .. "|" .. visible
                    link.row_power, link.row_visible = powered, visible
                end
                lights[#lights + 1] = link.status_row
            end
        end
        table.sort(lights)
        return row("native-stock-switch", state.map, name, bit(powered),
            angles.Pitch, angles.Yaw, angles.Roll, valid(panel) and panel:GetFullName() or "-",
            #lights > 0 and table.concat(lights, ";") or "-")
    end
end

function switches.read_row(entry, name, state, phase, row, native_world_address)
    local encoded = read_switch(entry, name, state, phase, row, native_world_address)
    if phase ~= "hosting" or type(encoded) ~= "string" then return encoded end
    local previous = observed_rows[name]
    if previous and previous.row == encoded then return previous.stamped end
    observation = observation + 1
    local stamped = encoded .. "\tv=" .. observation
    observed_rows[name] = {row = encoded, stamped = stamped}
    return stamped
end

function switches.rows(state, phase, row)
    local rows, visible = {}, phase == "hosting" and {}
    local discovered = census(state, phase)
    local native_world_address = next(discovered) and type(SilverfishReadNativeSwitchPower) == "function"
        and state.world:GetAddress()
    for name, entry in pairs(discovered) do
        local reading = switches.read_row(entry, name, state, phase, row, native_world_address)
        if reading then
            rows[#rows + 1] = reading
            if visible then visible[name] = true end
        end
    end
    if visible then
        for name in pairs(observed_rows) do
            if not visible[name] then
                observed_rows[name] = nil
                observation = observation + 1
            end
        end
        rows[#rows + 1] = row("native-stock-switch-census", state.map, observation)
    end
    return rows
end

function switches.focused_target(pawn, pressed_component)
    if not valid(pawn) or pawn:HasAuthority() or type(pressed_component) ~= "string"
        or #pressed_component == 0 or #pressed_component > 512 then return end
    local box = StaticFindObject(pressed_component)
    local actor = valid(box) and box:GetOwner()
    local entry = parts(actor, pawn:GetWorld())
    if entry and not (entry.lantern and actor.Broken_0 == true)
        and (same(box, entry.box) or entry.candle and same(box, entry.mesh)) then
        local name = actor:GetFullName()
        if placed_actor(name) then return (entry.power and "stock-prop-switch:" or "stock-switch:") .. name end
    end
end

local function project_lamp(entry, cached, state)
    local actor, glass = entry.actor, entry.actor.SM_Prop_Lantern_01_Glass
    if not valid(glass) or not same(glass:GetOwner(), actor) then error("stock_switch_lamp_parts_unavailable", 0) end
    local intact = state == "intact"
    if actor.Broken_0 ~= not intact then
        actor.Broken_0 = not intact
        glass:SetVisibility(intact, true)
    end
    if not intact then entry.box:SetCollisionEnabled(0) end
    if state == "removed" and not cached.retired then
        cached.hidden, cached.collision = actor.bHidden == true, actor:GetActorEnableCollision()
        actor:SetActorHiddenInGame(true)
        actor:SetActorEnableCollision(false)
        cached.retired = true
        retired_lamps[cached] = true
    elseif state ~= "removed" and cached.retired then
        actor:SetActorHiddenInGame(cached.hidden)
        actor:SetActorEnableCollision(cached.collision)
        cached.retired = nil
        retired_lamps[cached] = nil
    end
    entry.retired = cached.retired
end

local function project(entry, powered, angles, lights, replica)
    local actor, lever = entry.actor, entry.lever
    if entry.candle then
        if entry.gas_lamp then
            if actor.Lit ~= powered or entry.light:IsVisible() ~= powered then
                actor:Set_LitUnlit(powered, false)
            end
        else
            if not replica and not powered and actor.Lit then
                -- End the stock burn as well as changing its visible state.
                actor:Extinguish(1.0)
            end
            if actor.Lit ~= powered or entry.lantern and entry.light:IsVisible() ~= powered then
                actor:SetLitUnlit(powered, false)
            end
        end
        if replica then suppress_candle_overlap(actor:GetFullName(), entry.box) end
        if actor.Lit ~= powered or entry.light:IsVisible() ~= powered then error("stock_switch_candle_unverified", 0) end
        return
    end
    if entry.latch then
        local before, was_locked = lever.RelativeRotation, entry.door.Locked
        local restore = {Pitch = before.Pitch, Yaw = before.Yaw, Roll = before.Roll}
        local ok = pcall(function()
            lever:K2_SetRelativeRotation(angles, false, {}, true)
            entry.door.Locked = powered
            local actual = lever.RelativeRotation
            for _, axis in ipairs({"Pitch", "Yaw", "Roll"}) do
                if math.abs((actual[axis] - angles[axis] + 180) % 360 - 180) > .05 then error("latch pose mismatch") end
            end
            if entry.door.Locked ~= powered then error("latch lock mismatch") end
        end)
        if not ok then
            pcall(function() entry.door.Locked = was_locked end)
            pcall(function() lever:K2_SetRelativeRotation(restore, false, {}, true) end)
            error("stock_switch_latch_state_unverified", 0)
        end
        stock_door.refresh_linked_lock(entry.door, world)
        return
    end
    if entry.power then
        if actor[entry.power] ~= powered then actor:Toggle_Power(powered) end
        if actor[entry.power] ~= powered then error("stock_switch_toggle_unverified", 0) end
        return
    end
    actor.Powered = powered
    actor:Toggle_Visuals()
    lever:K2_SetRelativeRotation(angles, false, {}, true)
    -- Stock scripts can change ActorsToPower after the initial device census.
    refresh_lights(entry, world, powered, true)
    for _, state in ipairs(lights) do
        local link = entry.lights[state.id]
        if link and qualified(link.target, world, link.level) and valid(link.bulb) then
            if link.target.Powered ~= state.powered then
                link.target.Powered = state.powered
                link.target:TogglePower()
            end
            if link.bulb:IsVisible() ~= state.visible then link.bulb:SetVisibility(state.visible, true) end
        end
    end
end

local function can_set_power(entry)
    return entry.power and not (entry.lantern and entry.actor.Broken_0 == true)
        and not (entry.television and entry.actor.Working ~= true)
end

function switches.local_interaction(pawn, target, sequence)
    local name = type(target) == "string" and target:match("^stock%-prop%-switch:(.+)$")
    local package, path = placed_actor(name)
    if not package or not valid(pawn) or pawn:HasAuthority() or not pawn:IsLocallyControlled()
        or pawn.Dead ~= false or not same(world, pawn:GetWorld()) then return end
    local entry = parts(StaticFindObject(path), world)
    if not entry or not can_set_power(entry) then return end
    local actor = entry.actor
    local toggles = not entry.candle or entry.gas_lamp == true
    local expected = toggles and not actor[entry.power]
    project(entry, expected, nil, nil, true)
    if not valid(actor) or actor:IsActorBeingDestroyed() then return end
    local powered = actor[entry.power]
    local_power[name] = {actor = actor:GetAddress(), sequence = sequence, expires = os.clock() + 4}
    return "stock-switch-state:" .. sequence .. ":" .. bit(powered) .. ":" .. name
end

function switches.command(fields, state, phase)
    local action = fields[2]
    local ignite = action == "native-stock-switch-ignite"
    local report = action == "native-stock-switch-report"
    local clear = action == "native-stock-switch-release-local"
    if not ignite and not report and not clear and action ~= "native-stock-switch-project"
        and action ~= "native-stock-switch-interact" then return end
    if clear and phase == "connected" and #fields == 5 and fields[4] == state.map and same(world, state.world) then
        local pending = local_power[fields[3]]
        if pending and tonumber(fields[5]) == pending.sequence then local_power[fields[3]] = nil end
        return "stock switch local result released"
    end
    local entry = find_switch(state, phase, fields[3])
    if not entry or not valid(entry.actor) or entry.actor:IsActorBeingDestroyed() then
        error("stock_switch_unavailable", 0)
    end
    if report and phase == "hosting" and #fields == 5 and (fields[5] == "0" or fields[5] == "1") then
        local pawn = guest_save.exact_item_context(state, phase, fields[4])
        if not valid(pawn) or pawn.Dead ~= false then error("stock_switch_guest_unavailable", 0) end
        if not can_set_power(entry) then error("stock_switch_state_unavailable", 0) end
        project(entry, fields[5] == "1")
        return "stock switch report applied", entry
    end
    if (ignite or action == "native-stock-switch-interact") and phase == "hosting" and #fields == 4 then
        if entry.lantern and entry.actor.Broken_0 == true then error("stock_switch_lamp_broken", 0) end
        local pawn = guest_save.exact_item_context(state, phase, fields[4])
        if not valid(pawn) or pawn.Dead ~= false then error("stock_switch_guest_unavailable", 0) end
        if entry.latch then
            local door = entry.door
            if ignite or not door:HasAuthority() then error("stock_switch_unavailable", 0) end
            local before = door.Locked
            local expected = before
            if not door.Open and not door.LockBroken then expected = not before end
            entry.actor:SendInteractionToActor(pawn, entry.box)
            if not valid(door) or not same(entry.actor.Door, door) then error("stock_switch_unavailable", 0) end
            -- The stock callback can publish its lock change after this bridge command returns.
            -- Confirm the dispatch once and let the next world update carry the resulting state.
            if door.Locked ~= expected then return "stock latch interaction dispatched", entry end
            stock_door.refresh_linked_lock(door, state.world)
            return "stock switch interaction dispatched", entry
        end
        local power = entry.power or "Powered"
        local before = entry.actor[power]
        local toggles = not entry.candle or entry.gas_lamp == true
        -- The guest routes the exact stock interaction component it just hit.
        if not valid(entry.box) or not same(entry.box:GetOwner(), entry.actor) then error("stock_switch_unavailable", 0) end
        if entry.television and entry.actor.Working ~= true then error("stock_switch_unavailable", 0) end
        if ignite then
            if not entry.lantern then error("stock_switch_unavailable", 0) end
            if not before then entry.actor:SetOnFire(5, 0.2, pawn) end
        elseif toggles or before then
            entry.actor:SendInteractionToActor(pawn, entry.box)
        end
        local expected = ignite or toggles and not before
        if not valid(entry.actor) or entry.actor[power] ~= expected then error("stock_switch_toggle_unverified", 0) end
        return "stock switch interaction dispatched", entry
    end
    if action == "native-stock-switch-project" and phase == "connected" and fields[4] == state.map and #fields >= 8 then
        local name, cached = fields[3], entries[fields[3]]
        local pending = local_power[name]
        local last = #fields
        local confirmed = fields[last]:match("^ack=(%d+)$")
        if confirmed then last = last - 1 end
        local version = tonumber(fields[last]:match("^v=(%d+)$"))
        if version then last = last - 1 end
        if version and cached.projected_actor == entry.actor:GetAddress()
            and version < cached.projected_version then
            if pending and tonumber(confirmed) == pending.sequence then local_power[name] = nil end
            return "stock switch newer state retained", entry
        end
        if pending then
            if pending.actor == entry.actor:GetAddress() and os.clock() < pending.expires
                and tonumber(confirmed) ~= pending.sequence and (not entry.lantern or fields[9] == "intact") then
                return "stock switch local result pending", entry
            end
            local_power[fields[3]] = nil
        end
        local power, pitch, yaw, roll = fields[5], tonumber(fields[6]), tonumber(fields[7]), tonumber(fields[8])
        if (power ~= "0" and power ~= "1") or not pitch or not yaw or not roll
            or not (math.abs(pitch) <= 360 and math.abs(yaw) <= 360 and math.abs(roll) <= 360) then
            error("stock_switch_state_unavailable", 0)
        end
        local lights = {}
        if entry.latch then
            if last ~= 9 or fields[9] ~= "door=" .. entry.door:GetFullName() then
                error("stock_switch_latch_link_unavailable", 0)
            end
            project(entry, power == "1", {Pitch = pitch, Yaw = yaw, Roll = roll}, lights, true)
        elseif entry.lantern then
            local lamp = fields[9]
            if last ~= 9 or (lamp ~= "intact" and lamp ~= "broken" and lamp ~= "removed") then
                error("stock_switch_lamp_state_unavailable", 0)
            end
            local intact = lamp == "intact"
            if intact then project_lamp(entry, cached, lamp) end
            project(entry, intact and power == "1", {Pitch = pitch, Yaw = yaw, Roll = roll}, lights, true)
            if not intact then project_lamp(entry, cached, lamp) end
        else
            for index = 9, last do
                local id, on, visible = fields[index]:match("^([^|]+)|([01])|([01])$")
                if not id then error("stock_switch_light_unavailable", 0) end
                lights[#lights + 1] = {id = id, powered = on == "1", visible = visible == "1"}
            end
            project(entry, power == "1", {Pitch = pitch, Yaw = yaw, Roll = roll}, lights, true)
        end
        if version then
            cached.projected_actor, cached.projected_version = entry.actor:GetAddress(), version
        end
        return "stock switch state projected", entry
    end
    error("stock_switch_scope_unavailable", 0)
end

return switches
