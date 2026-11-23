local flashlight = {}

local equipment_class = "/Game/Classes/Equipment/Eqp_Flashlight.Eqp_Flashlight_C"
local source_class = "/Game/Classes/Equipment/Equipment_Assets/FlashLight_Source_BP.FlashLight_Source_BP_C"
local mesh_path = "/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Prop_Flashlight_01.SM_Prop_Flashlight_01"
local light_class = "/Script/Engine.SpotLightComponent"
local cookie_path = "/Game/Assets/Materials/Mat_Misc/M_LightCookie.M_LightCookie"
local click_path = "/Game/Assets/Sounds/CUEs/Mechanical_Cues/Switch_Reset_Cue.Switch_Reset_Cue"
local click_range = 4000 -- Switch_Reset_Cue's stock attenuation maximum in centimeters.
local maths = "/Script/Engine.Default__KismetMathLibrary"
local statics = "/Script/Engine.Default__GameplayStatics"
local avatar = require("native_player_avatar")
local owner, target, rendered, serial = nil, nil, nil, 0
local transform = {Translation = {X = 0, Y = 0, Z = 0}, Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Scale3D = {X = 1, Y = 1, Z = 1}}

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function finite(value, low, high) return type(value) == "number" and value >= low and value <= high end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path); object = StaticFindObject(path) end
    return object
end

local function scope(state, phase)
    local host, world = phase == "hosting", state.world
    local local_player = state.local_pawn
    local pawn = local_player and local_player.pawn
    if (not host and phase ~= "connected") or not state.listening or state.server ~= (not host)
        or not valid(world) or not valid(pawn) or pawn:IsActorBeingDestroyed() or not pawn:IsLocallyControlled()
        or not same(pawn:GetWorld(), world) or not same(pawn.Controller, state.controller) then return end
    local driver = world.NetDriver
    local connection = valid(driver) and driver.ServerConnection
    if not valid(driver) or not same(driver.World, world) or (host and valid(connection))
        or (not host and (not valid(connection) or not same(connection.Driver, driver))) then return end
    return pawn, driver, connection
end

local function held(actor, pawn, world)
    return exact(actor, equipment_class) and not actor:IsActorBeingDestroyed() and same(actor:GetWorld(), world)
        and same(actor:GetOwner(), pawn) and same(actor:GetAttachParentActor(), pawn)
end

local function stock_beam(equipment, world)
    local child = equipment.FlashLight_Source_BP
    local source = valid(child) and child.ChildActor
    local light = valid(source) and source.SpotLight
    if exact(source, source_class) and source.bReplicates == false and not source:IsActorBeingDestroyed()
        and same(child:GetOwner(), equipment) and same(source:GetParentActor(), equipment)
        and same(source:GetWorld(), world) and exact(light, light_class) and same(light:GetOwner(), source)
        and light.Intensity == 17 and light.AttenuationRadius == 5000
        and light.InnerConeAngle == 15 and light.OuterConeAngle == 40
        and valid(light.LightFunctionMaterial) and light.LightFunctionMaterial:GetFullName() == "Material " .. cookie_path then
        return light, source
    end
end

local function numbers(fields, offset)
    local values = {}
    for index = 1, 9 do
        local value = tonumber(fields[offset + index])
        local bound = index <= 3 and 250 or index <= 6 and 180 or 1
        if not finite(value, index <= 6 and -bound or 0, bound) then return end
        values[index] = value
    end
    return values
end

local function capture(state, pawn)
    local equipment = pawn.EquipedItem
    local instance = state.world.OwningGameInstance
    if not pawn:HasAuthority() or not held(equipment, pawn, state.world) or not equipment:HasAuthority()
        or not same(equipment.User, pawn) or not valid(instance) or not same(pawn.Save, instance.SaveSlot_Ref) then
        owner = nil
        return
    end
    local spot, source = stock_beam(equipment, state.world)
    local model = equipment.SM_Prop_Torch_01
    if not spot or not source:HasAuthority() or type(source.Powered) ~= "boolean" or source.Powered ~= spot:IsVisible()
        or not valid(model) or not same(model:GetOwner(), equipment) or not valid(model.StaticMesh)
        or model.StaticMesh:GetFullName() ~= "StaticMesh " .. mesh_path then owner = nil; return end
    if owner and same(owner.source, source) and same(owner.pawn, pawn) and owner.power == source.Powered then return end

    local id = avatar.player_id(state.local_pawn)
    local math_api = StaticFindObject(maths)
    if id == nil or not valid(math_api) then owner = nil; return end
    local relative = math_api:MakeRelativeTransform(spot:K2_GetComponentToWorld(), model:K2_GetComponentToWorld())
    local p, r, c = relative.Translation, math_api:Quat_Rotator(relative.Rotation), source.Color
    local values = numbers({p.X, p.Y, p.Z, r.Pitch, r.Yaw, r.Roll, c.R, c.G, c.B}, 0)
    if not values then owner = nil; return end
    local prior = owner and same(owner.source, source) and same(owner.pawn, pawn) and owner
    if not prior then serial = serial + 1 end
    owner = {world = state.world, pawn = pawn, id = id, source = source, instance = serial,
        switch = prior and prior.switch + 1 or 0, power = source.Powered, values = values}
end

local function clear_render()
    if rendered and valid(rendered.light) then
        if rendered.visual_only and valid(rendered.pawn) then rendered.light:K2_DestroyComponent(rendered.pawn)
        elseif valid(rendered.source) and rendered.source.Powered == false then rendered.light:SetVisibility(false, false) end
    end
    rendered = nil
end

local function remote(state, id)
    local pawn
    for _, details in ipairs(state.guests) do
        if valid(details.pawn) and not details.pawn:IsLocallyControlled() and not details.pawn:IsActorBeingDestroyed()
            and same(details.pawn:GetWorld(), state.world) and avatar.player_id(details) == id then
            if pawn then return end
            pawn = details.pawn
        end
    end
    return pawn
end

local function existing_beam(world, pawn)
    local equipment = pawn.EquipedItem
    if held(equipment, pawn, world) then return stock_beam(equipment, world) end
    local output = {}
    pawn:GetAttachedActors(output, true, false)
    local attached = output.OutActors or output
    if #attached > 16 then return end
    local spot, source
    for _, item in ipairs(attached) do
        local actor = type(item.type) == "function" and item:type() == "RemoteUnrealParam" and item:get() or item
        if held(actor, pawn, world) then
            local light, child = stock_beam(actor, world)
            if light then
                if source then return end
                spot, source = light, child
            end
        end
    end
    return spot, source
end

local function local_beam(pawn, mount, values)
    local class, cookie = StaticFindObject(light_class), asset(cookie_path)
    if not valid(class) or not valid(cookie) then return end
    local spot = pawn:AddComponentByClass(class, true, transform, true)
    if not valid(spot) then return end
    local ok = pcall(function()
        spot:SetVisibility(false, false)
        spot:SetMobility(2)
        pawn:FinishAddComponent(spot, true, transform)
        local none = FName("None", EFindName.FNAME_Find)
        if not spot:K2_AttachToComponent(mount, none, 2, 2, 0, false) then error("flashlight view attachment refused") end
        spot:K2_SetRelativeLocationAndRotation({X = values[1], Y = values[2], Z = values[3]},
            {Pitch = values[4], Yaw = values[5], Roll = values[6]}, false, {}, true)
        spot:SetIntensity(17)
        spot:SetAttenuationRadius(5000)
        spot:SetInnerConeAngle(15)
        spot:SetOuterConeAngle(40)
        spot:SetLightColor({R = values[7], G = values[8], B = values[9], A = 1}, true)
        spot:SetLightFunctionMaterial(cookie)
    end)
    if ok then return spot end
    spot:K2_DestroyComponent(pawn)
end

local function click(listener, mount)
    local api, sound = StaticFindObject(statics), asset(click_path)
    if not valid(api) or not valid(sound) then return false end
    local point, ear = mount:K2_GetComponentLocation(), listener:K2_GetActorLocation()
    if (point.X - ear.X)^2 + (point.Y - ear.Y)^2 + (point.Z - ear.Z)^2 > click_range^2 then return false end
    api:PlaySoundAtLocation(listener, sound, point, {Pitch = 0, Yaw = 0, Roll = 0}, 1, 1, 0, nil, nil, nil, nil)
    return true
end

local function project(state, listener)
    local pawn = target and remote(state, target.id)
    local mount = pawn and avatar.equipment_mesh(pawn, mesh_path)
    if not target or not valid(pawn) or not valid(mount) then clear_render(); return end
    if rendered and (not same(rendered.pawn, pawn) or not same(rendered.mount, mount) or rendered.instance ~= target.instance
        or not valid(rendered.light) or not target.power) then clear_render() end
    local now = os.clock()
    if target.power and not rendered and now >= target.next_retry then
        target.next_retry = now + .5
        local light, source = existing_beam(state.world, pawn)
        -- If the source is already active on this seat, leave the stock effect in charge.
        if light and (source.Powered ~= false or light:IsVisible()) then return end
        if not light then
            light, source = local_beam(pawn, mount, target.values), nil
        end
        if valid(light) then
            rendered = {pawn = pawn, mount = mount, light = light, source = source,
                instance = target.instance, visual_only = not source}
        end
    end
    if rendered and not rendered.light:IsVisible() then rendered.light:SetVisibility(true, false) end
    if target.click_until then
        local current = now <= target.click_until
        target.click_until = nil
        if current and click(listener, rendered and rendered.light or mount) then target.heard = target.switch end
    end
end

function flashlight.sync(state, phase)
    local pawn, driver, connection = scope(state, phase)
    if phase ~= "hosting" or not pawn or not pcall(capture, state, pawn) then owner = nil end
    if target and (phase ~= "connected" or not same(target.pawn, pawn) or not same(target.driver, driver)
        or not same(target.connection, connection) or not same(target.world, state.world) or target.map ~= state.map) then target = nil end
    if phase == "connected" and pawn then project(state, pawn) else clear_render() end
end

function flashlight.command(fields, state, phase)
    if fields[2] ~= "native-flashlight-view" then return end
    local pawn, driver, connection = scope(state, phase)
    local epoch = fields[4]
    if phase ~= "connected" or fields[3] ~= state.map or not pawn or not epoch or #epoch ~= 32 or not epoch:match("^%x+$") then
        error("flashlight view requires the authenticated local guest", 0)
    end
    if #fields == 4 then target = nil; clear_render(); return "flashlight view cleared" end
    local id, instance, switch = math.tointeger(tonumber(fields[5])), math.tointeger(tonumber(fields[6])), math.tointeger(tonumber(fields[7]))
    local power, values = fields[8], numbers(fields, 8)
    if #fields ~= 17 or not finite(id, 0, 2147483647) or not finite(instance, 1, 2147483647)
        or not finite(switch, 0, 2147483647) or (power ~= "0" and power ~= "1") or not values
        or id == avatar.player_id(state.local_pawn) then error("flashlight view receipt is invalid", 0) end
    local prior = target and target.epoch == epoch and target.id == id and target.instance == instance
        and same(target.pawn, pawn) and same(target.connection, connection) and target
    if prior and switch < prior.switch then error("flashlight view receipt is stale", 0) end
    target = {world = state.world, driver = driver, connection = connection, map = state.map, pawn = pawn, epoch = epoch, id = id,
        instance = instance, switch = switch, power = power == "1", values = values, next_retry = 0,
        click_until = prior and switch > prior.switch and os.clock() + .75 or nil, heard = prior and prior.heard or nil}
    project(state, pawn)
    return "stock flashlight beam view received"
end

function flashlight.rows(state, phase, row)
    if phase == "hosting" and owner and same(owner.world, state.world) then
        return row("native-flashlight-source", owner.id, owner.instance, owner.switch, owner.power and 1 or 0,
            table.unpack(owner.values))
    end
    if phase == "connected" and target and same(target.world, state.world) and target.map == state.map then
        return row("native-flashlight-view", target.epoch, target.id, target.instance, target.switch, target.power and 1 or 0,
            rendered and valid(rendered.light) and rendered.light:IsVisible() and 1 or 0, target.heard or "-")
    end
end

return flashlight
