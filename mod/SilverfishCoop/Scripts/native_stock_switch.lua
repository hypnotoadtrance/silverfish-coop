local switches = {}
local guest_save = require("guest_save")
local stock_focus = require("native_loot_crate_input").stock_focus

local root = "/Game/Classes/ScriptingActors/"
local profiles = {
    CircutBox_BP_C = {"SM_Switch_Lever_Stick_02", "LightPanel"},
    LightSwitch_BP_C = {"Switch_Mesh", "SwitchPanel"},
    LightSwitch_02_BP_C = {"Switch_Mesh", "SwitchPanel"},
}
local bulb_profiles = {
    ["/Game/Classes/DynamicLights/DynamicLight_Master_BP.DynamicLight_Master_BP_C"] = "PointLight",
    ["/Game/Classes/DynamicLights/Dynamic_SpotLight_Master_BP.Dynamic_SpotLight_Master_BP_C"] = "SpotLight",
}
local statics_path = "/Script/Engine.Default__GameplayStatics"
local world, entries

local function valid(object) return object and object:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end
local function bit(value) return value and "1" or "0" end

local function qualified(actor, scope)
    return valid(actor) and same(actor:GetWorld(), scope) and same(actor:GetOuter(), scope.PersistentLevel)
        and not actor:IsActorBeingDestroyed()
end

local function parts(actor, scope)
    if not qualified(actor, scope) or type(actor.Powered) ~= "boolean" then return end
    local kind = actor:GetClass():GetFName():ToString()
    local profile = profiles[kind]
    local package = profile and kind:sub(1, -3)
    if not package or actor:GetClass():GetFullName() ~= "BlueprintGeneratedClass " .. root .. package .. "." .. kind then return end
    local lever, panel, box = actor[profile[1]], actor[profile[2]], actor.Box
    if not valid(lever) or not valid(panel) or not valid(box) then return end
    for _, component in ipairs({lever, panel, box}) do
        if not same(component:GetOwner(), actor) then return end
    end
    return lever, panel, box
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

local function census(state, phase)
    if (phase ~= "connected" and phase ~= "hosting") or not state.listening
        or state.server ~= (phase == "connected") or not valid(state.world) then
        world, entries = nil, nil
        return {}
    end
    if same(world, state.world) then return entries end
    local statics = StaticFindObject(statics_path)
    if not valid(statics) then return {} end
    world, entries = state.world, {}
    for _, package in ipairs({"CircutBox_BP", "LightSwitch_BP"}) do
        local class = StaticFindObject(root .. package .. "." .. package .. "_C")
        if valid(class) then
            local output = {}
            statics:GetAllActorsOfClass(world, class, output)
            for _, actor in ipairs(output.OutActors or output) do
                if type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
                local lever, panel, box = parts(actor, world)
                if lever and (phase ~= "hosting" or actor:HasAuthority()) then
                    local name = actor:GetFullName()
                    entries[name] = {actor = actor, lever = lever, panel = panel, box = box, lights = light_links(actor, world)}
                end
            end
        end
    end
    return entries
end

function switches.rows(state, phase, row)
    local rows = {}
    for name, entry in pairs(census(state, phase)) do
        if valid(entry.actor) and not entry.actor:IsActorBeingDestroyed() and valid(entry.lever) then
            local angles = entry.lever.RelativeRotation
            local panel = entry.panel:GetMaterial(0)
            local lights = {}
            for id, link in pairs(entry.lights) do
                if valid(link.target) and valid(link.bulb) then
                    lights[#lights + 1] = table.concat({id, bit(link.target.Powered), bit(link.bulb:IsVisible())}, "|")
                end
            end
            table.sort(lights)
            rows[#rows + 1] = row("native-stock-switch", state.map, name, bit(entry.actor.Powered),
                angles.Pitch, angles.Yaw, angles.Roll, valid(panel) and panel:GetFullName() or "-",
                #lights > 0 and table.concat(lights, ";") or "-")
        end
    end
    return rows
end

function switches.focused_target(pawn, pressed_component)
    if not valid(pawn) or pawn:HasAuthority() or type(pressed_component) ~= "string"
        or #pressed_component == 0 or #pressed_component > 512 then return end
    local box = StaticFindObject(pressed_component)
    local actor = valid(box) and box:GetOwner()
    local lever, _, expected = parts(actor, pawn:GetWorld())
    if lever and same(box, expected) then return "stock-switch:" .. actor:GetFullName() end
end

local function project(entry, powered, angles, lights)
    local actor, lever = entry.actor, entry.lever
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
    if action ~= "native-stock-switch-project" and action ~= "native-stock-switch-interact" then return end
    local entry = census(state, phase)[fields[3]]
    if not entry or not valid(entry.actor) or entry.actor:IsActorBeingDestroyed() then
        error("stock_switch_unavailable", 0)
    end
    if action == "native-stock-switch-interact" and phase == "hosting" and #fields == 4 then
        local pawn = guest_save.exact_item_context(state, phase, fields[4])
        if not valid(pawn) or pawn.Dead ~= false then error("stock_switch_guest_unavailable", 0) end
        local before = entry.actor.Powered
        stock_focus(pawn, entry.box, function() entry.actor:SendInteractionToActor(pawn, entry.box) end,
            "stock_switch_guest_out_of_reach")
        if not valid(entry.actor) or entry.actor.Powered ~= not before then error("stock_switch_toggle_unverified", 0) end
        return "stock switch interaction dispatched"
    end
    if action == "native-stock-switch-project" and phase == "connected" and fields[4] == state.map and #fields >= 8 then
        local power, pitch, yaw, roll = fields[5], tonumber(fields[6]), tonumber(fields[7]), tonumber(fields[8])
        if (power ~= "0" and power ~= "1") or not pitch or not yaw or not roll
            or not (math.abs(pitch) <= 360 and math.abs(yaw) <= 360 and math.abs(roll) <= 360) then
            error("stock_switch_state_unavailable", 0)
        end
        local lights = {}
        for index = 9, #fields do
            local id, on, visible = fields[index]:match("^([^|]+)|([01])|([01])$")
            if not id then error("stock_switch_light_unavailable", 0) end
            lights[#lights + 1] = {id = id, powered = on == "1", visible = visible == "1"}
        end
        project(entry, power == "1", {Pitch = pitch, Yaw = yaw, Roll = roll}, lights)
        return "stock switch state projected"
    end
    error("stock_switch_scope_unavailable", 0)
end

return switches
