local dig = {}
local guest_save = require("guest_save")
local wire = require("native_wire_quest")

local enabled = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local world_path = map .. ".CrossRoads_P"
local source_path = world_path .. ":PersistentLevel.BurriedItem_BP_C_1"
local spade_path = world_path .. ":PersistentLevel.MeleeWep_Spade_C_3"
local spade_class = "/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C"
local buried_class = "/Game/Classes/Actors/BurriedItem_BP.BurriedItem_BP_C"
local package_class = "/Game/Classes/Items/Key_Items/Package_Item_FarmHouse.Package_Item_FarmHouse_C"
local position = {X = 1665.2142939210705, Y = 5811.305953931523, Z = 339.15110679941967}
local spade_position = {X = 6651.893485940875, Y = 13362.280305292443, Z = 380.8485755771449}
local flag_name = "BurriedItem_BP_C_1-CrossRoads_P-"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local color = {R = 0, G = 0, B = 0, A = 0}
local actions = {
    ["native-host-dig-acquire-fixture"] = "acquire",
    ["native-host-dig-press-fixture"] = "press",
    ["native-host-dig-collect-fixture"] = "collect",
    ["native-host-dig-inspect-fixture"] = "inspect",
}
local current, proof

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(value, path) return valid(value) and same(value:GetClass(), StaticFindObject(path)) end
local function need(ok, reason, dirty)
    if not ok then error((dirty and "mutation_unverified_" or "") .. "stock_host_dig_" .. reason, 0) end
    return ok
end
local function finite(value) return type(value) == "number" and value == value and math.abs(value) <= 10000000 end
local function vector(value)
    if value and finite(value.X) and finite(value.Y) and finite(value.Z) then
        return {X = value.X, Y = value.Y, Z = value.Z}
    end
end
local function rotation(value)
    if value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) then
        return {Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll}
    end
end
local function squared(a, b) return (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 end
local function coordinates(value)
    return value and string.format("%.2f,%.2f,%.2f", value.X, value.Y, value.Z) or "unavailable"
end
local function owned(component, actor, class)
    return exact(component, "/Script/Engine." .. class) and same(component:GetOwner(), actor)
end

local function context(state, phase)
    local detail, controller, world = state.local_pawn, state.controller, state.world
    local pawn = detail and detail.pawn
    if not enabled or phase ~= "hosting" or state.map ~= map or not state.listening or state.server ~= false
        or state.clients ~= 1 or not valid(world) or world:GetFullName() ~= "World " .. world_path
        or not exact(pawn, "/Game/Classes/Player/Player_FP.Player_FP_C") or detail.name ~= pawn:GetFullName()
        or not exact(controller, "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or not same(controller:GetWorld(), world) or not same(pawn:GetWorld(), world)
        or not controller:HasAuthority() or not controller:IsLocalController() or not pawn:HasAuthority()
        or controller.Loading ~= false or pawn.Dead ~= false then return end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    local driver = world.NetDriver
    if not exact(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
        or not exact(save, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C") or not same(pawn.Save, save)
        or not same(instance["As Player FP"], pawn) or not valid(driver) or valid(driver.ServerConnection)
        or not same(driver.World, world) or not owned(pawn.Camera, pawn, "CameraComponent") then return end
    for _, peer in ipairs(state.pawns) do
        if peer.kind == "guest" then
            local qualified, other, detached = pcall(guest_save.exact_item_context, state, phase, peer.name)
            if qualified and valid(other) and valid(detached) and same(other:GetWorld(), world) and not same(detached, save)
                and same(other.Save, detached) and other.Dead == false then
                return {world = world, pawn = pawn, controller = controller, instance = instance, save = save,
                    guest = other, detached = detached, name = detail.name, camera = pawn.Camera}
            end
        end
    end
end

local function key()
    local result = FName(flag_name, EFindName.FNAME_Add)
    return need(result:GetComparisonIndex() ~= 0 and result:ToString() == flag_name and result, "flag_key_unavailable")
end

local function row_guid(row)
    local value = row and row[item_guid]
    if not value then return end
    local ids = {}
    for _, member in ipairs({"A", "B", "C", "D"}) do
        local word = math.tointeger(value[member])
        if not word or word < -0x80000000 or word > 0xffffffff then return end
        ids[#ids + 1] = word & 0xffffffff
    end
    local guid = string.format("%08X%08X%08X%08X", table.unpack(ids))
    return guid:find("[^0]") and guid or nil
end

local function inventory(save, class)
    local count, found = 0, nil
    for _, name in ipairs({"PlayerBackpack", "PlayerEquipment"}) do
        local rows = save[name]
        local n = rows and math.tointeger(rows:GetArrayNum())
        if not n or n < 0 or n > 4096 then return end
        for index = 1, n do
            local row = rows[index]
            if row and same(row[item_class], class) then
                count = count + 1
                found = {guid = row_guid(row), ammo = row[item_ammo], source = name, index = index - 1}
                if not found.guid then return end
            end
        end
    end
    return count, found
end

local function held(ctx, class)
    local n, found = inventory(ctx.save, class)
    local actor = ctx.pawn.EquipedItem
    if n == 1 and found.source == "PlayerEquipment" and found.index == ctx.save.PlayerEquipIndex
        and found.ammo == -1 and exact(actor, spade_class) and actor:HasAuthority() and not actor:IsActorBeingDestroyed()
        and same(actor:GetWorld(), ctx.world) and same(actor:GetOwner(), ctx.pawn)
        and same(actor.Player_Ref, ctx.pawn) and same(actor.User, ctx.pawn) and same(actor.Instigator, ctx.pawn)
        and actor.HeldByPlayer == true and actor.Ammo == -1 and actor.Stacks == false then
        return actor, found.guid
    end
end

local function angles(eye, point)
    local x, y, z = point.X-eye.X, point.Y-eye.Y, point.Z-eye.Z
    return {Pitch = math.deg(math.atan(z, math.sqrt(x*x + y*y))), Yaw = math.deg(math.atan(y, x)), Roll = 0}
end

local function trace(ctx, from, to, complex, ignore)
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local out = {}
    if valid(library) and library:LineTraceSingle(ctx.pawn, from, to, 0, complex, ignore, 0, out, true, color, color, 0) == true then
        return out.OutHit or out
    end
end

local function floor_at(ctx, x, y, z, ignore)
    local hit = trace(ctx, {X = x, Y = y, Z = z + 112}, {X = x, Y = y, Z = z - 200}, false, ignore)
    local normal = hit and vector(hit.ImpactNormal)
    if normal and normal.Z >= 0.7 then return vector(hit.ImpactPoint) end
end

local function pose(ctx)
    return {place = vector(ctx.pawn:K2_GetActorLocation()), facing = rotation(ctx.pawn:K2_GetActorRotation()),
        control = rotation(ctx.controller:GetControlRotation()), camera = rotation(ctx.camera.RelativeRotation)}
end

local function restore(ctx, prior, moved)
    if moved and (ctx.pawn:K2_TeleportTo(prior.place, prior.facing) ~= true
        or squared(ctx.pawn:K2_GetActorLocation(), prior.place) > 18^2) then return false end
    ctx.controller:SetControlRotation(prior.control)
    ctx.camera:K2_SetRelativeRotation(prior.camera, false, {}, true)
    ctx.pawn:LookTrace()
    local actual = rotation(ctx.camera.RelativeRotation)
    if not actual then return false end
    for _, member in ipairs({"Pitch", "Yaw", "Roll"}) do
        if math.abs((actual[member] - prior.camera[member] + 180) % 360 - 180) > 0.1 then return false end
    end
    return true
end

local function stock_f(ctx, actor, box, label)
    need(type(SilverfishProbeNativeInteractInput) == "function" and owned(box, actor, "BoxComponent")
        and same(box, actor.RootComponent), label .. "_interaction_unavailable")
    local center = need(vector(box:K2_GetComponentLocation()), label .. "_box")
    local prior = pose(ctx)
    local moved, tried_press = false, false
    need(prior.place and prior.facing and prior.control and prior.camera
        and owned(ctx.pawn.CapsuleComponent, ctx.pawn, "CapsuleComponent"), label .. "_pose")
    local half = ctx.pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    need(finite(half) and half >= 20 and half <= 150, label .. "_capsule")
    local ignored = {ctx.pawn, ctx.guest, actor}
    if valid(ctx.pawn.EquipedItem) then ignored[#ignored + 1] = ctx.pawn.EquipedItem end
    local ok, sequence = pcall(function()
        for _, distance in ipairs({85, 115, 145, 175, 210}) do
            for bearing = 0, 315, 45 do
                local angle = math.rad(bearing)
                local x, y = center.X + distance * math.cos(angle), center.Y + distance * math.sin(angle)
                local floor = floor_at(ctx, x, y, center.Z, ignored)
                local stand = floor and {X = x, Y = y, Z = floor.Z + half + 2}
                if stand and squared(stand, center) <= 235^2 and ctx.pawn:K2_TeleportTo(stand, prior.facing) then
                    moved = true
                    local eye = vector(ctx.camera:K2_GetComponentLocation())
                    if eye and squared(ctx.pawn:K2_GetActorLocation(), stand) <= 18^2 then
                        ctx.camera:K2_SetWorldRotation(angles(eye, center), false, {}, true)
                        ctx.pawn:LookTrace()
                        if ctx.pawn.CanInteract == true and same(ctx.pawn.InteractionTarget_Component, box) then
                            tried_press = true
                            local sent, status, serial = SilverfishProbeNativeInteractInput(ctx.pawn:GetAddress(), false)
                            need(sent == true and status == "stock_started" and math.type(serial) == "integer" and serial > 0,
                                label .. "_stock_input:" .. tostring(status), true)
                            return serial
                        end
                    end
                end
            end
        end
        need(false, label .. "_no_stock_focus")
    end)
    need(restore(ctx, prior, moved), label .. "_pose_restore", tried_press)
    if not ok then error(sequence, 0) end
    return sequence
end

local materials = {}
for _, name in ipairs({"PM_Dirt", "PM_Debris", "PM_Grass", "PM_MossyDirt", "PM_Mud", "PM_Sand"}) do
    materials["PhysicalMaterial /Game/Classes/Libraries/PhysicMaterials/" .. name .. "." .. name] = true
end
local function material(hit)
    local pointer = hit and hit.PhysMaterial
    local mat = pointer and pointer:Get()
    return valid(mat) and materials[mat:GetFullName()] and mat:GetFullName() or nil
end

local function dig_aim(ctx, spade, source, prior)
    local center = need(vector(source:K2_GetActorLocation()), "dig_site_pose")
    local ignore = {ctx.pawn, spade}
    local half = ctx.pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    need(owned(ctx.pawn.CapsuleComponent, ctx.pawn, "CapsuleComponent") and finite(half) and half >= 20 and half <= 150,
        "dig_capsule")
    local moved, floor_count = false, 0
    for _, gap in ipairs({{0,0}, {12,0}, {-12,0}, {0,15}, {0,-15}, {24,0}, {-24,0}, {0,26}, {0,-26}}) do
        local x, y = center.X + gap[1], center.Y + gap[2]
        local ground = trace(ctx, {X = x, Y = y, Z = center.Z + 55}, {X = x, Y = y, Z = center.Z - 46}, true, ignore)
        local target = ground and vector(ground.Location)
        if target and material(ground) and squared(target, center) <= 45^2 then
            for _, distance in ipairs({75, 105, 135}) do
                for bearing = 0, 315, 45 do
                    local angle = math.rad(bearing)
                    local sx, sy = x + distance * math.cos(angle), y + distance * math.sin(angle)
                    local floor = floor_at(ctx, sx, sy, center.Z, {ctx.pawn, ctx.guest, spade})
                    local stand = floor and {X = sx, Y = sy, Z = floor.Z + half + 2}
                    if stand and ctx.pawn:K2_TeleportTo(stand, prior.facing) then
                        moved, floor_count = true, floor_count + 1
                        local eye = vector(ctx.camera:K2_GetComponentLocation())
                        if eye and squared(ctx.pawn:K2_GetActorLocation(), stand) <= 18^2 then
                            local aim = angles(eye, target)
                            if aim.Pitch < -40 then
                                ctx.controller:SetControlRotation(aim)
                                ctx.camera:K2_SetWorldRotation(aim, false, {}, true)
                                local forward = vector(ctx.camera:GetForwardVector())
                                local endpoint = forward and {X = eye.X + forward.X*300, Y = eye.Y + forward.Y*300,
                                    Z = eye.Z + forward.Z*300}
                                local hit = endpoint and trace(ctx, eye, endpoint, true, ignore)
                                local contact = hit and vector(hit.Location)
                                local soil = material(hit)
                                if contact and soil and squared(contact, center) <= 45^2 then return contact, soil, moved end
                            end
                        end
                    end
                end
            end
        end
    end
    need(restore(ctx, prior, moved), "dig_aim_restore", moved)
    error("stock_host_dig_no_stock_ground_trace:floor=" .. floor_count, 0)
end

local function exact_site(ctx)
    local source = StaticFindObject(source_path)
    need(exact(source, buried_class) and source:HasAuthority() and not source:IsActorBeingDestroyed()
        and same(source:GetWorld(), ctx.world), "buried_source_identity")
    local actual = vector(source:K2_GetActorLocation())
    need(actual and squared(actual, position) <= 4, "buried_source_position:" .. coordinates(actual))
    need(source.Replenish_Mode == 2 and source.PsyDmg == 0
        and same(source.BurriedActor, StaticFindObject(package_class))
        and owned(source.Sphere, source, "SphereComponent") and math.abs(source.Sphere:GetScaledSphereRadius() - 25) < 0.1,
        "buried_source_shape")
    return source
end

local function site_retired(ctx)
    local source = StaticFindObject(source_path)
    return not valid(source) or exact(source, buried_class) and same(source:GetWorld(), ctx.world)
        and source:IsActorBeingDestroyed()
end

local function matches(ctx)
    return current and same(current.world, ctx.world) and same(current.pawn, ctx.pawn)
        and same(current.save, ctx.save) and same(current.detached, ctx.detached)
end

function dig.command(fields, state, phase)
    local action = actions[fields[2]]
    if not action then return end
    local ctx = need(context(state, phase), "exact_headless_host_unavailable")
    need(fields[3] == ctx.name and #fields == (action == "press" and 4 or 3), "fixture_arguments")
    local spade_type, package_type = StaticFindObject(spade_class), StaticFindObject(package_class)
    need(valid(spade_type) and valid(package_type), "stock_classes_unavailable")
    local flag, present = wire.stock_flag(ctx.instance, key())
    local guest_spade, guest_package = inventory(ctx.detached, spade_type), inventory(ctx.detached, package_type)
    need(guest_spade == 0 and guest_package == 0, "guest_inventory_unqualified")
    if action == "acquire" then
        local actor = StaticFindObject(spade_path)
        local slot = ctx.save.PlayerEquipment[1]
        need(not present and not valid(ctx.pawn.EquipedItem) and slot and not valid(slot[item_class])
            and ctx.instance.GamePlay_Config.AutoEquip == true and inventory(ctx.save, spade_type) == 0,
            "stock_spade_autoequip_unavailable")
        need(exact(actor, spade_class) and actor:HasAuthority() and same(actor:GetWorld(), ctx.world)
            and not actor:IsActorBeingDestroyed(), "placed_stock_spade_identity")
        local actual = vector(actor:K2_GetActorLocation())
        need(actual and squared(actual, spade_position) <= 25, "placed_stock_spade_position:" .. coordinates(actual))
        need(actor.Physics == false and actor.Stacks == false and actor.Ammo == -1, "placed_stock_spade_shape")
        local sequence = stock_f(ctx, actor, actor.Box, "spade")
        local equipped, guid = held(ctx, spade_type)
        need(equipped and guid and (not valid(actor) or actor:IsActorBeingDestroyed()) and
            inventory(ctx.detached, spade_type) == 0, "stock_spade_equip_readback", true)
        proof = {status = "equipped", guid = guid, flag = 0, stock_f = sequence}
        return "ordinary stock Interact equipped the placed spade"
    end
    if action == "inspect" then
        local amount, row = inventory(ctx.save, package_type)
        local _, tool = held(ctx, spade_type)
        need(present and flag == 1 and site_retired(ctx) and amount == 1 and row.source == "PlayerBackpack" and tool,
            "permanent_dig_or_package_unavailable")
        proof = {status = "restored", guid = tool, package_guid = row.guid, flag = flag}
        return "stock permanent dig and sealed package verified"
    end
    local spade, identity = held(ctx, spade_type)
    need(spade and identity, "owned_spade_unavailable")
    if action == "press" then
        local source = exact_site(ctx)
        need(not present and fields[4] == identity and inventory(ctx.save, package_type) == 0
            and type(SilverfishPressNativeHostDigFixture) == "function"
            and type(SilverfishInspectNativeHostDigFixture) == "function", "dig_preflight")
        local prior = pose(ctx)
        need(prior.place and prior.facing and prior.control and prior.camera, "dig_pose")
        local contact, soil, moved = dig_aim(ctx, spade, source, prior)
        local ok, status = SilverfishPressNativeHostDigFixture(ctx.world:GetAddress(), ctx.pawn:GetAddress(),
            contact.X, contact.Y, contact.Z)
        if ok ~= true then
            need(restore(ctx, prior, moved), "dig_pose_restore", moved)
            error(status, 0)
        end
        current = {world = ctx.world, pawn = ctx.pawn, save = ctx.save, detached = ctx.detached,
            prior = prior, moved = moved, guid = identity}
        proof = {status = "pressed", guid = identity, flag = 0, contact = contact, material = soil}
        return "stock host primary Started dispatched"
    end
    need(matches(ctx) and proof and proof.status == "dug" and proof.actor and valid(proof.actor)
        and proof.guid == identity and present and flag == 1 and site_retired(ctx), "spawned_package_unavailable", true)
    local actor = proof.actor
    local sequence = stock_f(ctx, actor, actor.Box, "package")
    local amount, row = inventory(ctx.save, package_type)
    local _, held_after = held(ctx, spade_type)
    need(amount == 1 and row.source == "PlayerBackpack" and (row.ammo == 0 or row.ammo == 1)
        and held_after == identity and (not valid(actor) or actor:IsActorBeingDestroyed())
        and inventory(ctx.detached, package_type) == 0,
        "stock_package_custody", true)
    proof.status, proof.stock_f, proof.package_guid = "collected", sequence, row.guid
    return "ordinary stock Interact secured the sealed package"
end

function dig.rows(state, phase, status_row)
    if not proof then return {} end
    local ctx = context(state, phase)
    if not ctx then return {} end
    if proof.status == "pressed" and matches(ctx) then
        local ok, status, address, x, y, z, holes, incidental =
            SilverfishInspectNativeHostDigFixture(ctx.world:GetAddress(), ctx.pawn:GetAddress())
        if ok ~= true then
            proof.status, proof.fault = "failed", status
            if not restore(ctx, current.prior, current.moved) then
                proof.fault = "mutation_unverified_stock_host_dig_pose_restore"
            end
        elseif status == "observed" then
            local actor
            for _, found in ipairs(FindAllOf("Package_Item_FarmHouse_C") or {}) do
                if exact(found, package_class) and found:GetAddress() == address and same(found:GetWorld(), ctx.world) then
                    actor = found
                    break
                end
            end
            local flag, present = wire.stock_flag(ctx.instance, key())
            local _, guid = held(ctx, StaticFindObject(spade_class))
            local restored = restore(ctx, current.prior, current.moved)
            if actor and present and flag == 1 and guid == proof.guid and holes == 1 and (incidental == 0 or incidental == 1)
                and site_retired(ctx) and restored then
                proof.status, proof.actor, proof.actor_name = "dug", actor, actor:GetFullName()
                proof.flag, proof.contact, proof.holes, proof.incidental = flag, {X = x, Y = y, Z = z}, holes, incidental
            else
                proof.status, proof.fault = "failed", "mutation_unverified_stock_host_dig_readback"
            end
        end
    end
    local hit = proof.contact or {X = "-", Y = "-", Z = "-"}
    return {status_row("native-host-dig", map, ctx.name, proof.status, proof.guid, flag_name, proof.flag,
        proof.actor_name or "-", proof.package_guid or "-", hit.X, hit.Y, hit.Z,
        proof.material or "-", proof.holes or "-", proof.incidental or "-", proof.stock_f or "-", proof.fault or "-")}
end

function dig.clear()
    current, proof = nil, nil
end

return dig
