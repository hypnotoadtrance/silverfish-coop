local switches = {}
local guest_save = require("guest_save")

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
for kind, profile in pairs(profiles) do
    profile.path = profile.path or root .. kind:sub(1, -3) .. "." .. kind
end
local bulb_profiles = {
    ["/Game/Classes/DynamicLights/DynamicLight_Master_BP.DynamicLight_Master_BP_C"] = "PointLight",
    ["/Game/Classes/DynamicLights/Dynamic_SpotLight_Master_BP.Dynamic_SpotLight_Master_BP_C"] = "SpotLight",
}
local statics_path = "/Script/Engine.Default__GameplayStatics"
local world, entries, dirty
local candle_overlaps, retired_lamps = {}, {}

local function valid(object) return object and object:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end
local function bit(value) return value and "1" or "0" end

local function qualified(actor, scope)
    return valid(actor) and same(actor:GetWorld(), scope) and same(actor:GetOuter(), scope.PersistentLevel)
        and not actor:IsActorBeingDestroyed()
end

local function parts(actor, scope)
    if not valid(actor) then return end
    local class = actor:GetClass()
    if not valid(class) then return end
    local profile = profiles[class:GetFName():ToString()]
    if not profile or class:GetFullName() ~= "BlueprintGeneratedClass " .. profile.path
        or not qualified(actor, scope) or type(actor[profile.power or "Powered"]) ~= "boolean" then return end
    local box = profile.candle and actor.Sphere or actor.Box
    if not valid(box) or not same(box:GetOwner(), actor) then return end
    if profile.candle then
        local mesh, light, flame = actor.PropMesh, actor.PointLight, actor.FX_CandleFlame_01_Converted
        if not same(mesh, actor.RootComponent) or not same(mesh:GetOwner(), actor)
            or not valid(light) or not same(light:GetOwner(), actor)
            or not valid(flame) or not same(flame:GetOwner(), actor) then return end
        return {actor = actor, box = box, mesh = mesh, light = light, flame = flame, power = profile.power,
            candle = true, lantern = profile.lantern}
    end
    if profile.power then return {actor = actor, box = box, power = profile.power, television = profile.television} end
    local lever, panel = actor[profile[1]], actor[profile[2]]
    if not valid(lever) or not valid(panel) then return end
    for _, component in ipairs({lever, panel}) do
        if not same(component:GetOwner(), actor) then return end
    end
    return {actor = actor, lever = lever, panel = panel, box = box}
end

local function light_links(actor, scope)
    local lights = {}
    local linked = actor.ActorsToPower
    if not linked then return lights end
    local bases = {}
    for path, component in pairs(bulb_profiles) do
        local base = StaticFindObject(path)
        if valid(base) then bases[#bases + 1] = {base, component} end
    end
    for index = 1, linked:GetArrayNum() do
        local target = linked[index]
        if valid(target) and type(target.Powered) == "boolean" and qualified(target, scope) then
            for _, base in ipairs(bases) do
                if target:IsA(base[1]) then
                    local bulb = target[base[2]]
                    if valid(bulb) and same(bulb:GetOwner(), target) then
                        lights[target:GetFullName()] = {target = target, bulb = bulb}
                    end
                    break
                end
            end
        end
    end
    return lights
end

function switches.start()
    if type(NotifyOnNewObject) ~= "function" then return end
    for kind, profile in pairs(profiles) do
        NotifyOnNewObject(profile.path, function()
            -- Construction can precede BeginPlay. Read stock properties during the regular census.
            if dirty then dirty[kind] = true end
        end)
    end
end

local function census(state, phase)
    if (phase ~= "connected" and phase ~= "hosting") or not state.listening
        or state.server ~= (phase == "connected") or not valid(state.world) then
        world, entries, dirty = nil, nil, nil
        return {}
    end
    if not same(world, state.world) then
        world, entries, dirty = state.world, {}, {}
        for kind in pairs(profiles) do dirty[kind] = true end
    end
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
                local entry = valid(actor) and same(actor:GetClass(), class) and parts(actor, world)
                if entry and (phase ~= "hosting" or actor:HasAuthority()) then
                    local name = actor:GetFullName()
                    if entry.power then
                        if not entries[name] or not same(entries[name].actor, actor) then
                            entries[name] = {actor = actor, class = class, power = entry.power,
                                path = name:match("^%S+ (.+)$"), candle = entry.candle, lantern = entry.lantern,
                                light = entry.light, flame = entry.flame}
                        end
                    elseif not entries[name] or not same(entries[name].actor, actor) then
                        entry.lights = light_links(actor, world)
                        entries[name] = entry
                    end
                end
            end
        end
    end
    return entries
end

local function find_switch(state, phase, name)
    local entry = census(state, phase)[name]
    if not entry or not entry.power then return entry end
    local actor = StaticFindObject(entry.path)
    local resolved = parts(actor, state.world)
    if resolved and resolved.power == entry.power then return resolved end
end

function switches.release_candles(state, phase)
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

function switches.read_row(entry, name, state, phase, row)
    if entry.power then
        local actor = entry.actor
        if not qualified(actor, state.world) then
            if not entry.path then return end
            actor = StaticFindObject(entry.path)
            if not qualified(actor, state.world) or not same(actor:GetClass(), entry.class) then
                if entry.lantern and phase == "hosting" then
                    return row("native-stock-switch", state.map, name, 0, 0, 0, 0, "-", "-", "removed")
                end
                return
            end
            entry.actor = actor
        end
        local powered = actor[entry.power]
        if type(powered) == "boolean" then
            if entry.candle and phase == "connected" then
                local sphere = actor.Sphere
                if valid(sphere) and same(sphere:GetOwner(), actor) then suppress_candle_overlap(name, sphere) end
            end
            local lamp_state
            local panel = "-"
            if entry.lantern then
                lamp_state = entry.retired and "removed" or actor.Broken_0 == true and "broken" or "intact"
                if phase == "connected" and lamp_state == "intact" and valid(entry.light) and valid(entry.flame)
                    and (entry.light:IsVisible() ~= powered or entry.flame:IsActive() ~= powered) then
                    panel = "lamp-visual-mismatch"
                end
            end
            if entry.row_power ~= powered or entry.row_lamp ~= lamp_state or entry.row_panel ~= panel
                or entry.row_encoder ~= row or entry.row_map ~= state.map then
                if lamp_state then
                    entry.status_row = row("native-stock-switch", state.map, name, bit(powered), 0, 0, 0, panel, "-", lamp_state)
                else
                    entry.status_row = row("native-stock-switch", state.map, name, bit(powered), 0, 0, 0, "-", "-")
                end
                entry.row_power, entry.row_lamp, entry.row_panel = powered, lamp_state, panel
                entry.row_encoder, entry.row_map = row, state.map
            end
            return entry.status_row
        end
    elseif valid(entry.actor) and not entry.actor:IsActorBeingDestroyed() and valid(entry.lever) then
        local angles = entry.lever.RelativeRotation
        local panel = entry.panel:GetMaterial(0)
        local lights = {}
        for id, link in pairs(entry.lights) do
            if valid(link.target) and valid(link.bulb) then
                local powered, visible = bit(link.target.Powered), bit(link.bulb:IsVisible())
                if link.row_power ~= powered or link.row_visible ~= visible then
                    link.status_row = id .. "|" .. powered .. "|" .. visible
                    link.row_power, link.row_visible = powered, visible
                end
                lights[#lights + 1] = link.status_row
            end
        end
        table.sort(lights)
        return row("native-stock-switch", state.map, name, bit(entry.actor.Powered),
            angles.Pitch, angles.Yaw, angles.Roll, valid(panel) and panel:GetFullName() or "-",
            #lights > 0 and table.concat(lights, ";") or "-")
    end
end

function switches.rows(state, phase, row)
    local rows = {}
    for name, entry in pairs(census(state, phase)) do
        rows[#rows + 1] = switches.read_row(entry, name, state, phase, row)
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
        return "stock-switch:" .. actor:GetFullName()
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

local function project(entry, powered, angles, lights)
    local actor, lever = entry.actor, entry.lever
    if entry.candle then
        if actor.Lit ~= powered or entry.lantern and entry.light:IsVisible() ~= powered then
            actor:SetLitUnlit(powered, false)
        end
        if entry.lantern and entry.flame:IsActive() ~= powered then
            if powered then entry.flame:Activate(true) else entry.flame:Deactivate() end
        end
        suppress_candle_overlap(actor:GetFullName(), entry.box)
        if actor.Lit ~= powered or entry.light:IsVisible() ~= powered then error("stock_switch_candle_unverified", 0) end
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
    for _, state in ipairs(lights) do
        local link = entry.lights[state.id]
        if link and valid(link.target) and valid(link.bulb) then
            if link.target.Powered ~= state.powered then
                link.target.Powered = state.powered
                link.target:TogglePower()
            end
            if link.bulb:IsVisible() ~= state.visible then link.bulb:SetVisibility(state.visible, true) end
        end
    end
end

function switches.command(fields, state, phase)
    local action = fields[2]
    local ignite = action == "native-stock-switch-ignite"
    if not ignite and action ~= "native-stock-switch-project" and action ~= "native-stock-switch-interact" then return end
    local entry = find_switch(state, phase, fields[3])
    if not entry or not valid(entry.actor) or entry.actor:IsActorBeingDestroyed() then
        error("stock_switch_unavailable", 0)
    end
    if (ignite or action == "native-stock-switch-interact") and phase == "hosting" and #fields == 4 then
        if entry.lantern and entry.actor.Broken_0 == true then error("stock_switch_lamp_broken", 0) end
        local pawn = guest_save.exact_item_context(state, phase, fields[4])
        if not valid(pawn) or pawn.Dead ~= false then error("stock_switch_guest_unavailable", 0) end
        local power = entry.power or "Powered"
        local before = entry.actor[power]
        -- The guest routes the exact stock interaction component it just hit.
        if not valid(entry.box) or not same(entry.box:GetOwner(), entry.actor) then error("stock_switch_unavailable", 0) end
        if entry.television and entry.actor.Working ~= true then error("stock_switch_unavailable", 0) end
        if ignite then
            if not entry.lantern then error("stock_switch_unavailable", 0) end
            if not before then entry.actor:SetOnFire(5, 0.2, pawn) end
        elseif not entry.candle or before then
            entry.actor:SendInteractionToActor(pawn, entry.box)
        end
        local expected = ignite or not entry.candle and not before
        if not valid(entry.actor) or entry.actor[power] ~= expected then error("stock_switch_toggle_unverified", 0) end
        return "stock switch interaction dispatched", entry
    end
    if action == "native-stock-switch-project" and phase == "connected" and fields[4] == state.map and #fields >= 8 then
        local power, pitch, yaw, roll = fields[5], tonumber(fields[6]), tonumber(fields[7]), tonumber(fields[8])
        if (power ~= "0" and power ~= "1") or not pitch or not yaw or not roll
            or not (math.abs(pitch) <= 360 and math.abs(yaw) <= 360 and math.abs(roll) <= 360) then
            error("stock_switch_state_unavailable", 0)
        end
        local lights = {}
        if entry.lantern then
            local lamp = fields[9]
            if #fields ~= 9 or (lamp ~= "intact" and lamp ~= "broken" and lamp ~= "removed") then
                error("stock_switch_lamp_state_unavailable", 0)
            end
            project(entry, power == "1", {Pitch = pitch, Yaw = yaw, Roll = roll}, lights)
            project_lamp(entry, entries[fields[3]], lamp)
            return "stock switch state projected", entry
        end
        for index = 9, #fields do
            local id, on, visible = fields[index]:match("^([^|]+)|([01])|([01])$")
            if not id then error("stock_switch_light_unavailable", 0) end
            lights[#lights + 1] = {id = id, powered = on == "1", visible = visible == "1"}
        end
        project(entry, power == "1", {Pitch = pitch, Yaw = yaw, Roll = roll}, lights)
        return "stock switch state projected", entry
    end
    error("stock_switch_scope_unavailable", 0)
end

return switches
