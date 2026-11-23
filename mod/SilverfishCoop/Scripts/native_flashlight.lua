local flashlight = {}

local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local source_class = "/Game/Classes/Equipment/Equipment_Assets/FlashLight_Source_BP.FlashLight_Source_BP_C"
local light_class = "/Script/Engine.SpotLightComponent"
local cookie_path = "/Game/Assets/Materials/Mat_Misc/M_LightCookie.M_LightCookie"
local click_path = "/Game/Assets/Sounds/CUEs/Mechanical_Cues/Switch_Reset_Cue.Switch_Reset_Cue"
local click_range = 4000 -- Switch_Reset_Cue's stock attenuation maximum in centimeters.
local maths = "/Script/Engine.Default__KismetMathLibrary"
local statics = "/Script/Engine.Default__GameplayStatics"
local avatar = require("native_player_avatar")
local offhand_view = require("native_avatar_offhand")
local players = require("players")
local owner, guest_owner, target, peers, serial = nil, nil, nil, nil, 0
local equipment_path = "/Game/Classes/Equipment/"
local torch_path = "/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/"
-- Cooked TR_S and FL stock profiles, measured relative to their own held torch mesh.
local regular = {class = equipment_path .. "Eqp_Flashlight.Eqp_Flashlight_C",
    mesh = torch_path .. "SM_Prop_Flashlight_01.SM_Prop_Flashlight_01", intensity = 17, radius = 5000,
    inner = 15, outer = 40, pose = {0, 21.66062, 0, 0, 90, 0, 1, .970104, .947917}}
local heavy = {class = equipment_path .. "Eqp_Heavy_Flashlight.Eqp_Heavy_Flashlight_C",
    mesh = torch_path .. "SM_Prop_Flashlight_02.SM_Prop_Flashlight_02", intensity = 20, radius = 6000,
    inner = 30, outer = 60, pose = {0, 18.463014427, 16.476153329, 0, 90, 0, 1, 1, 1}}
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
    if not valid(actor) then return end
    local class = actor:GetClass()
    local profile = same(class, StaticFindObject(regular.class)) and regular
        or same(class, StaticFindObject(heavy.class)) and heavy
    if profile and not actor:IsActorBeingDestroyed() and same(actor:GetWorld(), world)
        and same(actor:GetOwner(), pawn) and same(actor:GetAttachParentActor(), pawn) then return profile end
end

local function power_token(view)
    return (view.power and "1" or "0") .. (view.offhand and "o" or view.profile == heavy and "h" or "")
end

local function power_profile(token)
    if token == "0" or token == "1" then return regular, token == "1" end
    if token == "0h" or token == "1h" then return heavy, token == "1h" end
    if token == "0o" or token == "1o" then return regular, token == "1o", true end
end

local function selected_stock(pawn, equipment)
    local save = pawn.Save
    if not valid(save) then return false end
    local index = math.tointeger(save.PlayerEquipIndex)
    local slots = save.PlayerEquipment
    return index and slots and index >= 0 and index < slots:GetArrayNum()
        and same(slots[index + 1][item_class], equipment:GetClass())
end

local function stock_offhand(pawn, world)
    local pistol = pawn.EquipedItem
    local camera, item = pawn.Camera, pawn.OffhandEquipment
    if pawn.Save.PlayerOffhandingFlashlight ~= true or not exact(pistol, "/Game/Classes/Weapons/Wep_Pistol.Wep_Pistol_C")
        or not pistol:HasAuthority() or not same(pistol.User, pawn) or not selected_stock(pawn, pistol)
        or not exact(item, regular.class) or item:IsActorBeingDestroyed() or item.bReplicates ~= false
        or not same(item.User, pawn) or not same(item:GetWorld(), world) or not valid(camera)
        or not same(camera:GetOwner(), pawn) or not valid(item.Box) or not same(item.Box:GetAttachParent(), camera) then return end
    local slots = pawn.Save.PlayerEquipment
    local class = item:GetClass()
    for index = 1, math.min(slots:GetArrayNum(), 8) do
        if same(slots[index][item_class], class) then return item end
    end
end

local function stock_beam(equipment, world, profile)
    local child = equipment.FlashLight_Source_BP
    local source = valid(child) and child.ChildActor
    local light = valid(source) and source.SpotLight
    if exact(source, source_class) and source.bReplicates == false and not source:IsActorBeingDestroyed()
        and same(child:GetOwner(), equipment) and same(source:GetParentActor(), equipment)
        and same(source:GetWorld(), world) and exact(light, light_class) and same(light:GetOwner(), source)
        and light.Intensity == profile.intensity and light.AttenuationRadius == profile.radius
        and light.InnerConeAngle == profile.inner and light.OuterConeAngle == profile.outer
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

local function capture(state, pawn, previous, guest)
    local equipment = pawn.EquipedItem
    local instance = state.world.OwningGameInstance
    if pawn:HasAuthority() ~= (not guest) or not valid(instance) or not same(pawn.Save, instance.SaveSlot_Ref) then return end
    local profile = held(equipment, pawn, state.world)
    local offhand = false
    if not profile and not guest then
        local item = stock_offhand(pawn, state.world)
        if item then equipment, profile, offhand = item, regular, true end
    end
    if not profile or not equipment:HasAuthority() or not same(equipment.User, pawn)
        or guest and not selected_stock(pawn, equipment) then return end
    local spot, source = stock_beam(equipment, state.world, profile)
    local model = equipment.SM_Prop_Torch_01
    if not spot or not source:HasAuthority() or type(source.Powered) ~= "boolean" or source.Powered ~= spot:IsVisible()
        or not valid(model) or not same(model:GetOwner(), equipment) or not valid(model.StaticMesh)
        or model.StaticMesh:GetFullName() ~= "StaticMesh " .. profile.mesh then return end
    local prior = previous and same(previous.source, source) and same(previous.pawn, pawn) and previous.offhand == offhand and previous
    if prior and not offhand and prior.power == source.Powered then return prior end

    local id = avatar.player_id(state.local_pawn)
    local math_api = StaticFindObject(maths)
    if id == nil or not valid(math_api) then return end
    local relative = math_api:MakeRelativeTransform(spot:K2_GetComponentToWorld(), model:K2_GetComponentToWorld())
    local p, r, c = relative.Translation, math_api:Quat_Rotator(relative.Rotation), source.Color
    local values = numbers({p.X, p.Y, p.Z, r.Pitch, r.Yaw, r.Roll, c.R, c.G, c.B}, 0)
    if not values then return end
    if guest or profile == heavy or offhand then
        for index, expected in ipairs(profile.pose) do
            if math.abs(expected - values[index]) > .05 then return end
        end
    end
    if offhand then
        local to_pawn = math_api:MakeRelativeTransform(spot:K2_GetComponentToWorld(), pawn:GetTransform())
        local aim = math_api:Quat_Rotator(to_pawn.Rotation)
        values[4], values[5], values[6] = math.floor(aim.Pitch * 10 + .5) / 10,
            math.floor(aim.Yaw * 10 + .5) / 10, math.floor(aim.Roll * 10 + .5) / 10
        if not numbers(values, 0) then return end
    end
    if not prior then serial = serial + 1 end
    return {world = state.world, pawn = pawn, id = id, source = source, instance = serial, profile = profile, offhand = offhand,
        switch = prior and (prior.switch + (prior.power == source.Powered and 0 or 1)) or 0, power = source.Powered, values = values}
end

local function clear_render(view)
    local rendered = view and view.rendered
    if rendered and valid(rendered.light) then
        if rendered.visual_only and valid(rendered.pawn) then rendered.light:K2_DestroyComponent(rendered.pawn)
        elseif valid(rendered.source) and rendered.source.Powered == false then rendered.light:SetVisibility(false, false) end
    end
    if rendered and rendered.offhand and valid(rendered.mount) and valid(rendered.pawn) then
        rendered.mount:K2_DestroyComponent(rendered.pawn)
    end
    if view then view.rendered = nil end
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

local function existing_beam(world, pawn, profile)
    local equipment = pawn.EquipedItem
    if held(equipment, pawn, world) == profile then return stock_beam(equipment, world, profile) end
    local output = {}
    pawn:GetAttachedActors(output, true, false)
    local attached = output.OutActors or output
    if #attached > 16 then return end
    local spot, source
    for _, item in ipairs(attached) do
        local actor = type(item.type) == "function" and item:type() == "RemoteUnrealParam" and item:get() or item
        if held(actor, pawn, world) == profile then
            local light, child = stock_beam(actor, world, profile)
            if light then
                if source then return end
                spot, source = light, child
            end
        end
    end
    return spot, source
end

local function local_beam(pawn, mount, profile, values)
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
        spot:SetIntensity(profile.intensity)
        spot:SetAttenuationRadius(profile.radius)
        spot:SetInnerConeAngle(profile.inner)
        spot:SetOuterConeAngle(profile.outer)
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

local function aim_offhand(view, now)
    local rendered = view.rendered
    if not rendered or not valid(rendered.light) or not valid(rendered.pawn) then return end
    local api = rendered.math_api
    if not valid(api) then return end
    local alpha = math.min((now - view.aim_at) / .22, 1)
    local aim = alpha < 1 and api:RLerp(view.aim_from, view.aim, alpha, true) or view.aim
    view.aim_current = aim
    local rotation = rendered.pawn:K2_GetActorRotation()
    local prior = rendered.pawn_rotation
    if rendered.aim == aim and prior and prior.Pitch == rotation.Pitch and prior.Yaw == rotation.Yaw
        and prior.Roll == rotation.Roll then return end
    rendered.light:K2_SetWorldRotation(api:ComposeRotators(aim, rotation), false, {}, true)
    rendered.aim, rendered.pawn_rotation = aim, rotation
end

local function project_offhand(view, pawn)
    local body = pawn and avatar.offhand_body(pawn)
    if not valid(body) then clear_render(view); return end
    local rendered = view.rendered
    if rendered and (not same(rendered.pawn, pawn) or not same(rendered.body, body)
        or not valid(rendered.mount) or not valid(rendered.light)) then clear_render(view); rendered = nil end
    local now = os.clock()
    if not rendered and now >= view.next_retry then
        view.next_retry = now + .5
        local mount = offhand_view.create(pawn, body)
        if not valid(mount) then return end
        local light = local_beam(pawn, mount, regular, regular.pose)
        if not valid(light) then mount:K2_DestroyComponent(pawn); return end
        light:SetAbsolute(false, true, false)
        rendered = {offhand = true, visual_only = true, body = body, pawn = pawn, mount = mount, light = light,
            math_api = StaticFindObject(maths)}
        view.rendered = rendered
    end
    if rendered then
        aim_offhand(view, now)
        if rendered.light:IsVisible() ~= view.power then rendered.light:SetVisibility(view.power, false) end
    end
end

function flashlight.animate(get_world)
    if target and target.offhand and target.rendered and same(target.world, get_world()) then
        aim_offhand(target, os.clock())
    end
end

local function project(state, listener, view)
    local pawn = remote(state, view.id)
    if view.offhand then project_offhand(view, pawn); return end
    local mount = pawn and avatar.equipment_mesh(pawn, view.profile.mesh)
    if not valid(pawn) or not valid(mount) then clear_render(view); return end
    local rendered = view.rendered
    if rendered and (not same(rendered.pawn, pawn) or not same(rendered.mount, mount) or rendered.instance ~= view.instance
        or not valid(rendered.light) or not view.power) then clear_render(view); rendered = nil end
    local now = os.clock()
    if view.power and not rendered and now >= view.next_retry then
        view.next_retry = now + .5
        local light, source = existing_beam(state.world, pawn, view.profile)
        -- If the source is already active on this seat, leave the stock effect in charge.
        if light and (source.Powered ~= false or light:IsVisible()) then return end
        if not light or view.guest then
            light, source = local_beam(pawn, mount, view.profile, view.values), nil
        end
        if valid(light) then
            rendered = {pawn = pawn, mount = mount, light = light, source = source,
                instance = view.instance, visual_only = not source}
            view.rendered = rendered
        end
    end
    if rendered and not rendered.light:IsVisible() then rendered.light:SetVisibility(true, false) end
    if view.click_until then
        local current = now <= view.click_until
        view.click_until = nil
        if current and click(listener, rendered and rendered.light or mount) then view.heard = view.switch end
    end
end

local function current(view, state, phase, pawn, driver, connection)
    return view and view.phase == phase and same(view.pawn, pawn) and same(view.driver, driver)
        and (phase == "hosting" or same(view.connection, connection)) and same(view.world, state.world)
        and view.map == state.map
end

local function clear_peers()
    if peers then
        for _, view in pairs(peers.views) do clear_render(view) end
        peers = nil
    end
end

function flashlight.sync(state, phase)
    local pawn, driver, connection = scope(state, phase)
    local host_ok, host_source = false, nil
    if phase == "hosting" and pawn then host_ok, host_source = pcall(capture, state, pawn, owner, false) end
    owner = host_ok and host_source or nil
    local guest_ok, guest_source = false, nil
    if phase == "connected" and pawn and players.is_downed(pawn) == false then
        guest_ok, guest_source = pcall(capture, state, pawn, guest_owner, true)
    end
    guest_owner = guest_ok and guest_source or nil
    if target and not current(target, state, phase, pawn, driver, connection) then clear_render(target); target = nil end
    if peers and not current(peers, state, phase, pawn, driver, connection) then clear_peers() end
    if target then project(state, pawn, target) end
    if peers then
        for _, view in pairs(peers.views) do project(state, pawn, view) end
    end
end

function flashlight.command(fields, state, phase)
    local guest_views = fields[2] == "native-guest-flashlight-view"
    if not guest_views and fields[2] ~= "native-flashlight-view" then return end
    local pawn, driver, connection = scope(state, phase)
    local epoch = fields[4]
    if (phase ~= "connected" and (not guest_views or phase ~= "hosting")) or fields[3] ~= state.map
        or not pawn or not epoch or #epoch ~= 32 or not epoch:match("^%x+$") then
        error("flashlight view requires an active co-op listener", 0)
    end
    if guest_views then
        if (#fields - 4) % 4 ~= 0 or #fields > 68 then error("invalid guest flashlight list", 0) end
        if #fields == 4 then clear_peers(); return "guest flashlight views cleared" end
        local previous = current(peers, state, phase, pawn, driver, connection) and peers.epoch == epoch and peers.views or {}
        local views, local_id = {}, avatar.player_id(state.local_pawn)
        for index = 5, #fields, 4 do
            local id = math.tointeger(tonumber(fields[index]))
            local instance = math.tointeger(tonumber(fields[index + 1]))
            local switch = math.tointeger(tonumber(fields[index + 2]))
            local profile, power, offhand = power_profile(fields[index + 3])
            if not finite(id, 0, 2147483647) or not finite(instance, 1, 2147483647)
                or not finite(switch, 0, 2147483647) or not profile or offhand or views[id] or id == local_id then
                error("invalid guest flashlight view", 0)
            end
            local prior = previous[id]
            if prior and (instance < prior.instance
                or instance == prior.instance and (switch < prior.switch or profile ~= prior.profile)) then
                error("stale guest flashlight view", 0)
            end
            local same_source = prior and instance == prior.instance
            views[id] = {guest = true, id = id, instance = instance, switch = switch, power = power,
                profile = profile, values = profile.pose, next_retry = 0, rendered = same_source and prior.rendered or nil,
                click_until = same_source and (switch > prior.switch and os.clock() + .75 or prior.click_until) or nil,
                heard = same_source and prior.heard or nil}
        end
        if peers and previous ~= peers.views then clear_peers() end
        for id, prior in pairs(previous) do
            if not views[id] or views[id].rendered ~= prior.rendered then clear_render(prior) end
        end
        peers = {world = state.world, driver = driver, connection = connection, map = state.map, pawn = pawn,
            phase = phase, epoch = epoch, views = views}
        for _, view in pairs(views) do project(state, pawn, view) end
        return "guest flashlight views received"
    end
    if #fields == 4 then clear_render(target); target = nil; return "flashlight view cleared" end
    local id, instance, switch = math.tointeger(tonumber(fields[5])), math.tointeger(tonumber(fields[6])), math.tointeger(tonumber(fields[7]))
    local profile, power, offhand = power_profile(fields[8])
    local values = numbers(fields, 8)
    if #fields ~= 17 or not finite(id, 0, 2147483647) or not finite(instance, 1, 2147483647)
        or not finite(switch, 0, 2147483647) or not profile or not values
        or id == avatar.player_id(state.local_pawn) then error("flashlight view receipt is invalid", 0) end
    local prior = target and target.epoch == epoch and target.id == id and target.instance == instance
        and same(target.pawn, pawn) and same(target.connection, connection) and target
    if prior and (switch < prior.switch or profile ~= prior.profile or offhand ~= prior.offhand) then
        error("flashlight view receipt is stale", 0)
    end
    if not prior then clear_render(target) end
    target = {world = state.world, driver = driver, connection = connection, map = state.map, pawn = pawn, epoch = epoch,
        phase = phase, id = id,
        instance = instance, switch = switch, power = power, profile = profile, offhand = offhand,
        values = profile == heavy and profile.pose or values, next_retry = 0,
        rendered = prior and prior.rendered or nil,
        click_until = prior and switch > prior.switch and os.clock() + .75 or nil, heard = prior and prior.heard or nil}
    if offhand then
        target.aim = {Pitch = values[4], Yaw = values[5], Roll = values[6]}
        target.aim_from = prior and prior.aim_current or target.aim
        target.aim_at = os.clock()
    end
    project(state, pawn, target)
    return "stock flashlight beam view received"
end

function flashlight.rows(state, phase, row)
    if phase == "hosting" and owner and same(owner.world, state.world) then
        return row("native-flashlight-source", owner.id, owner.instance, owner.switch, power_token(owner),
            table.unpack(owner.values))
    end
    if phase == "connected" and target and same(target.world, state.world) and target.map == state.map then
        local rendered = target.rendered
        if target.offhand then
            return row("native-flashlight-view", target.epoch, target.id, target.instance, target.switch, power_token(target),
                rendered and valid(rendered.light) and rendered.light:IsVisible() and 1 or 0, target.heard or "-",
                target.aim.Pitch, target.aim.Yaw, target.aim.Roll)
        end
        return row("native-flashlight-view", target.epoch, target.id, target.instance, target.switch, power_token(target),
            rendered and valid(rendered.light) and rendered.light:IsVisible() and 1 or 0, target.heard or "-")
    end
end

function flashlight.peer_rows(state, phase, row)
    local rows = {}
    if guest_owner and phase == "connected" and same(guest_owner.world, state.world) then
        rows[#rows + 1] = row("native-guest-flashlight-source", state.local_pawn.name, guest_owner.instance,
            guest_owner.switch, power_token(guest_owner))
    elseif phase == "hosting" then
        for _, details in ipairs(state.guests) do
            local pawn = details.pawn
            if valid(pawn) and pawn:HasAuthority() and not pawn:IsLocallyControlled() and same(pawn:GetWorld(), state.world)
                and not pawn:IsActorBeingDestroyed() then
                local equipment = pawn.EquipedItem
                local profile = held(equipment, pawn, state.world)
                if profile and same(equipment.User, pawn) and equipment:HasAuthority() and selected_stock(pawn, equipment) then
                    local id = avatar.player_id(details)
                    if id ~= nil then
                        rows[#rows + 1] = row("native-guest-flashlight-authority", id, details.name, profile == heavy and "heavy" or nil)
                    end
                end
            end
        end
    end
    if peers and peers.phase == phase and same(peers.world, state.world) then
        for id, view in pairs(peers.views) do
            local rendered = view.rendered
            rows[#rows + 1] = row("native-guest-flashlight-view", peers.epoch, id, view.instance, view.switch,
                power_token(view), rendered and valid(rendered.light) and rendered.light:IsVisible() and 1 or 0, view.heard or "-")
        end
    end
    return rows
end

return flashlight
