local npc = {}
local transport = require("native_transport")
local guest_save = require("guest_save")
local guest_weapon = require("native_guest_weapon")
local enemy_loot = require("native_enemy_loot")

local map = "/Game/Classes/Maps/PlaytestMap_01"
local world_name = "World " .. map .. ".PlaytestMap_01"
local source_name = "AI_Spawner_Single_BP_C " .. map .. ".PlaytestMap_01:PersistentLevel.AI_Spawner_Single_BP_C_4"
local bandit_class = "BlueprintGeneratedClass /Game/Classes/NPCs/NPC_Bandit.NPC_Bandit_C"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_NPC_TEST") == "1" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local hostile = os.getenv("SILVERFISH_COOP_NATIVE_NPC_HOSTILE") == "1"
local enemy_loot_fixture = os.getenv("SILVERFISH_COOP_NATIVE_ENEMY_LOOT") == "1"
local stock_weapons = {
    ["BlueprintGeneratedClass /Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C"] = true,
    ["BlueprintGeneratedClass /Game/Classes/Weapons/Wep_SMG_UBGL.Wep_SMG_UBGL_C"] = true,
}
local aim_fixture
local trace_color = { R = 0, G = 0, B = 0, A = 1 }

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function active(state, phase)
    local detail = state.local_pawn
    local pawn = detail and detail.pawn
    if not enabled or state.map ~= map or not valid(state.world) or state.world:GetFullName() ~= world_name
        or not valid(pawn) or not same(pawn:GetWorld(), state.world) or detail.name ~= pawn:GetFullName()
        or not valid(detail.controller_object) or not detail.controller_object:IsLocalController()
        or not same(detail.controller_object.Pawn, pawn) or phase ~= "hosting" and phase ~= "connected" then return end
    return pawn
end

local function authority_guest(state, phase)
    if phase ~= "hosting" or #state.guests ~= 1 then return end
    local detail = state.guests[1]
    local pawn, controller = detail.pawn, detail.controller_object
    if not valid(pawn) or detail.name ~= pawn:GetFullName() or not pawn:HasAuthority()
        or not same(pawn:GetWorld(), state.world) or not valid(controller) or controller:IsLocalController()
        or not same(controller.Pawn, pawn) or not same(controller:GetWorld(), state.world) then return end
    return pawn
end

local function source_in(world)
    local source
    for _, candidate in ipairs(FindAllOf("AI_Spawner_Single_BP_C") or {}) do
        if valid(candidate) and same(candidate:GetWorld(), world) and candidate:GetFullName() == source_name then
            if source then return end
            source = candidate
        end
    end
    local arrow, class = valid(source) and source.Arrow, valid(source) and source.AI_Class
    if not valid(arrow) or not valid(class) or not same(arrow:GetOwner(), source) or class:GetFullName() ~= bandit_class then return end
    return source, arrow
end

local function read(pawn, watched)
    return table.pack(SilverfishReadNativeBandit(pawn:GetAddress(), watched and watched:GetAddress() or 0))
end

function npc.rows(state, phase, status_row)
    local aim = aim_fixture
    if state.map ~= map and aim and same(state.world, aim.world) then
        local _, watched = enemy_loot.watched_cqc(state, phase, aim.name, true)
        if same(watched, aim.guest) then
            return { status_row("native-bandit-aim", state.map, aim.name, watched:GetFullName(),
                aim.distance, aim.yaw, aim.pitch, aim.component) }
        end
    end
    local pawn = active(state, phase)
    if not pawn or type(SilverfishReadNativeBandit) ~= "function" then return {} end
    local watched = authority_guest(state, phase)
    local report = read(pawn, watched)
    if report.n ~= 24 then return {} end
    if phase == "hosting" and hostile and type(SilverfishReadNativeBanditCombat) == "function" then
        local combat = table.pack(SilverfishReadNativeBanditCombat(pawn:GetAddress(), watched and watched:GetAddress() or 0))
        if combat.n == 8 then
            for index = 1, combat.n do report[report.n + index] = combat[index] end
            report.n = report.n + combat.n
        end
    end
    for index = 1, report.n do
        if type(report[index]) == "boolean" then report[index] = report[index] and 1 or 0 end
        if report[index] == "" then report[index] = "-" end
    end
    local rows = { status_row("native-bandit", map, phase, pawn:GetFullName(), watched and watched:GetFullName() or "-",
        table.unpack(report, 1, report.n)) }
    if aim and phase == "hosting" and report[5] == aim.name and report[12] == 1
        and same(state.world, aim.world) and same(watched, aim.guest) then
        rows[#rows + 1] = status_row("native-bandit-aim", map, aim.name, watched:GetFullName(),
            aim.distance, aim.yaw, aim.pitch, aim.component)
    end
    return rows
end

local function place(pawn, point, rotation, forward_x, forward_y, offset)
    local dx, dy = forward_x * offset, forward_y * offset
    for _, height in ipairs({ 90, 130, 170 }) do
        if pawn:K2_TeleportTo({ X = point.X + dx, Y = point.Y + dy, Z = point.Z + height }, rotation) then
            pawn:ForceNetUpdate()
            return true
        end
    end
    return false
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function vector(value)
    return value and finite(value.X) and finite(value.Y) and finite(value.Z)
end

local function rotation(value)
    if value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) then
        return { Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll }
    end
end

local function look_from(origin, target)
    local x, y, z = target.X - origin.X, target.Y - origin.Y, target.Z - origin.Z
    local horizontal = math.sqrt(x * x + y * y)
    return { Pitch = math.deg(math.atan(z, horizontal)), Yaw = math.deg(math.atan(y, x)), Roll = 0 },
        math.sqrt(horizontal * horizontal + z * z)
end

local function stock_bandit(state, report, name)
    if type(name) ~= "string" or #name > 512 or name:find("%c") or report.n ~= 24
        or report[1] ~= true or report[2] ~= "stock_bandit_authority" or report[3] ~= 1
        or report[4] ~= 0 or report[5] ~= name or report[11] ~= true or report[12] ~= true then return end
    local bandit
    for _, actor in ipairs(FindAllOf("NPC_Bandit_C") or {}) do
        if valid(actor) and actor:GetFullName() == name and same(actor:GetWorld(), state.world)
            and actor:HasAuthority() and not actor:IsActorBeingDestroyed()
            and actor:GetClass():GetFullName() == bandit_class then
            if bandit then return end
            bandit = actor
        end
    end
    return bandit
end

local function owned_component(component, owner, class_name)
    return valid(component) and same(component:GetOwner(), owner)
        and component:GetClass():GetFullName() == "Class /Script/Engine." .. class_name
end

local function floor_at(context, point, x, y, half_height)
    local start = { X = x, Y = y, Z = point.Z + 95 }
    local finish = { X = x, Y = y, Z = point.Z - 220 }
    local hit = {}
    local found = context.trace:LineTraceSingle(context.guest, start, finish, 0, false,
        { context.host, context.guest, context.bandit, context.weapon }, 0, hit, true, trace_color, trace_color, 0)
    hit = hit.OutHit or hit
    if found == true and vector(hit.ImpactPoint) and vector(hit.ImpactNormal) and hit.ImpactNormal.Z >= 0.7
        and hit.ImpactPoint.Z < start.Z - 1 and hit.ImpactPoint.Z >= finish.Z then
        return { X = x, Y = y, Z = hit.ImpactPoint.Z + half_height + 2 }
    end
end

local function stock_hit(context, start, finish)
    local hit = {}
    -- Match the projectile's MovementUpdate trace, including complex collision.
    local found = context.trace:LineTraceSingle(context.guest, start, finish, 2, true,
        { context.guest, context.weapon }, 0, hit, true, trace_color, trace_color, 0)
    hit = hit.OutHit or hit
    if found ~= true or not hit.Component then return end
    local component = hit.Component:Get()
    if valid(component) and same(component:GetOwner(), context.bandit) then return component end
end

local function stock_head(bandit, center)
    local output = {}
    local ok = pcall(function() bandit:GetHeadLocation(output, output) end)
    local point = output.HeadLocation
    if not ok or output.FoundHead ~= true or not vector(point) or point.Z <= center.Z + 15 then return end
    local _, distance = look_from(center, point)
    if distance <= 125 then return { X = point.X, Y = point.Y, Z = point.Z } end
end

local function approach_revive(host, guest, bandit, trace)
    local capsule = host.CapsuleComponent
    local guest_location, bandit_location = guest:K2_GetActorLocation(), bandit:K2_GetActorLocation()
    local original = host:K2_GetActorLocation()
    local orientation = rotation(host:K2_GetActorRotation())
    if host.Dead ~= false or host.IsWeaponBlocked ~= false or not valid(trace)
        or not owned_component(capsule, host, "CapsuleComponent")
        or not vector(original) or not orientation or not vector(guest_location) or not vector(bandit_location) then
        error("The stock Bandit revive approach requires an exact standing host", 0)
    end
    local _, current_distance = look_from(original, guest_location)
    if current_distance <= 220 then return "stock Bandit revive approach verified" end
    local height = capsule:GetScaledCapsuleHalfHeight()
    if not finite(height) or height <= 0 or height > 150 then error("The stock host capsule is unavailable", 0) end

    local bearing = math.atan(guest_location.Y - bandit_location.Y, guest_location.X - bandit_location.X)
    local context = { trace = trace, host = host, guest = guest, bandit = bandit }
    local moved = false
    for _, offset in ipairs({ 0, 55, -55, 105, -105, 180 }) do
        local direction = bearing + math.rad(offset)
        local x, y = guest_location.X + math.cos(direction) * 165, guest_location.Y + math.sin(direction) * 165
        local point = floor_at(context, guest_location, x, y, height)
        if point and math.abs(point.Z - guest_location.Z) <= 75 and host:K2_TeleportTo(point, orientation) then
            moved = true
            local actual = host:K2_GetActorLocation()
            if vector(actual) then
                local _, distance = look_from(actual, guest_location)
                if distance <= 220 then
                    host:ForceNetUpdate()
                    return "stock Bandit revive approach verified"
                end
            end
        end
    end
    if moved and not host:K2_TeleportTo(original, orientation) then
        error("mutation_unverified_stock_bandit_revive_approach", 0)
    end
    if moved then host:ForceNetUpdate() end
    error("No floor-verified stock Bandit revive position within normal interact range", 0)
end


local function settle_aim(context, point)
    local eye = context.camera:K2_GetComponentLocation()
    if not vector(eye) then return end
    local view = look_from(eye, point)
    context.controller:SetControlRotation(view)
    context.camera:K2_SetWorldRotation(view, false, {}, true)

    local origin = context.muzzle:K2_GetComponentLocation()
    if not vector(origin) then return end
    -- SpawnProjectile reads the held Arrow_Muzzle world transform and applies
    -- stock spread. Keep that spread and all stock damage handling intact.
    local targets = {}
    if context.head then targets[1] = context.head end
    for _, height in ipairs({ 18, 5, 32, -10, 47 }) do
        targets[#targets + 1] = { X = point.X, Y = point.Y, Z = point.Z + height }
    end
    for _, target in ipairs(targets) do
        local aim, distance = look_from(origin, target)
        if distance >= 90 and distance <= 550 and math.abs(aim.Pitch) < 35 then
            context.muzzle:K2_SetWorldRotation(aim, false, {}, true)
            local muzzle = context.muzzle:K2_GetComponentLocation()
            local forward = context.muzzle:GetForwardVector()
            local actual_aim = rotation(context.muzzle:K2_GetComponentRotation())
            if vector(muzzle) and vector(forward) and actual_aim
                and (forward.X * (target.X - origin.X) + forward.Y * (target.Y - origin.Y)
                    + forward.Z * (target.Z - origin.Z)) / distance > 0.999 then
                local reach = distance + 60
                local finish = { X = muzzle.X + forward.X * reach,
                    Y = muzzle.Y + forward.Y * reach, Z = muzzle.Z + forward.Z * reach }
                local contact = stock_hit(context, muzzle, finish)
                if contact then return distance, actual_aim, contact end
            end
        end
    end
end

local function exact_watch(state, phase, host, guest, name, tracked)
    if not enemy_loot_fixture or type(SilverfishReadNativeEnemyLoot) ~= "function" then return false end
    if tracked then
        local current_host, current_guest, actor = enemy_loot.watched_cqc(state, phase, name)
        return same(host, current_host) and same(guest, current_guest) and same(tracked, actor)
    end
    local row = enemy_loot.rows(state, phase, table.pack)[1]
    if not row or row[1] ~= "native-enemy-loot" or row[2] ~= state.map or row[3] ~= name or row[4] ~= "watching" then
        return false
    end
    -- The native reader also rechecks the current pair and exact stock enemy binding.
    local live = table.pack(SilverfishReadNativeEnemyLoot(host:GetAddress()))
    return (live.n == 15 or live.n == 23) and live[1] == "watching"
end

local function fire_aimed_weapon(state, phase, name, guid)
    local called, proof = pcall(function()
        guest_weapon.command({ "", "native-guest-weapon-fire", name, guid }, state, phase)
        for _, row in ipairs(guest_weapon.rows(state, phase, table.pack)) do
            if row[1] == "native-guest-weapon" and row[2] == state.map and row[3] == "fire"
                and row[4] == name and row[5] == guid then return row end
        end
    end)
    if not called or not proof or type(proof[7]) ~= "string" then
        return false, "mutation_unverified_native_bandit_shot"
    end
    if proof[6] ~= 1 then return false, proof[7] end
    if proof[7] ~= "equipped" or type(proof[8]) ~= "number" or proof[9] ~= proof[8] - 1
        or proof[10] ~= proof[9] or proof[11] ~= proof[9] or proof[12] ~= 1 then
        return false, "mutation_unverified_native_bandit_shot_receipt"
    end
    return true
end

local function bandit_aim(state, phase, host, guest, before, name, guid, tracked)
    aim_fixture = nil
    local bandit = tracked or stock_bandit(state, before, name)
    local detail = state.guests[1]
    local controller = detail.controller_object
    local qualified, detached = guest_save.exact_item_context(state, phase, detail.name)
    if not bandit or not same(guest, qualified) or not valid(detached) then
        error("The exact enrolled guest or living Bandit is unavailable", 0)
    end
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if enemy_loot_fixture and guest.Dead == false and guest.IsWeaponBlocked == true
        and finite(detached.PlayerHealth) and detached.PlayerHealth <= 0 then
        if guid then error("downed", 0) end
        return approach_revive(host, guest, bandit, trace)
    end
    if guid then
        if not exact_watch(state, phase, host, guest, name, tracked) then error("native_bandit_shot_watch_required", 0) end
        if not finite(detached.PlayerHealth) or detached.PlayerHealth <= 0 then error("downed", 0) end
        if type(SilverfishInspectNativeGuestWeapon) ~= "function" or type(SilverfishFireNativeGuestWeapon) ~= "function" then
            error("native_bandit_shot_weapon_unavailable", 0)
        end
        local checked, equipped, status, live, stored = pcall(SilverfishInspectNativeGuestWeapon,
            guest:GetAddress(), detached:GetAddress(), guid)
        if not checked or equipped ~= true or status ~= "equipped" or not finite(live) or live ~= stored then
            error("native_bandit_shot_equipped_guid_required", 0)
        end
        if live < 2 then error("guest_weapon_preloaded_magazine_required", 0) end
    end
    local weapon, camera, capsule = guest.EquipedItem, guest.Camera, guest.CapsuleComponent
    if guest.Dead ~= false or guest.IsWeaponBlocked ~= false or not valid(weapon) or not weapon:HasAuthority()
        or not same(weapon:GetWorld(), state.world) or not same(weapon:GetOwner(), guest)
        or not same(weapon:GetInstigator(), guest) or not same(weapon.User, guest) or not same(weapon.Player_Ref, guest)
        or not stock_weapons[weapon:GetClass():GetFullName()] or weapon.HeldByPlayer ~= true
        or not owned_component(camera, guest, "CameraComponent") or not owned_component(capsule, guest, "CapsuleComponent")
        or not owned_component(weapon.Arrow_Muzzle, weapon, "ArrowComponent")
        or not owned_component(bandit.CapsuleComponent, bandit, "CapsuleComponent") then
        error("The verified standing guest, stock equipped weapon or living Bandit is unavailable", 0)
    end
    local muzzle = weapon.Arrow_Muzzle
    local point = bandit.CapsuleComponent:K2_GetComponentLocation()
    local height = capsule:GetScaledCapsuleHalfHeight()
    local original = {
        position = guest:K2_GetActorLocation(), pawn = rotation(guest:K2_GetActorRotation()),
        control = rotation(controller:GetControlRotation()), camera = rotation(camera.RelativeRotation),
        muzzle = rotation(muzzle.RelativeRotation),
    }
    if not valid(trace) or not vector(point) or not finite(height) or height <= 0 or height > 150
        or not vector(original.position) or not original.pawn or not original.control or not original.camera
        or not original.muzzle then error("The stock Bandit aim geometry is unavailable", 0) end
    local context = { trace = trace, host = host, guest = guest, bandit = bandit, weapon = weapon,
        controller = controller, camera = camera, muzzle = muzzle, head = enemy_loot_fixture and stock_head(bandit, point) }
    local moved = false
    local applied, distance, aim, contact = pcall(function()
        local _, now = look_from(original.position, point)
        if now >= 215 and now <= 430 then
            local length, current_aim, hit = settle_aim(context, point)
            if hit then return length, current_aim, hit end
        end
        local bearing = math.atan(original.position.Y - point.Y, original.position.X - point.X)
        for _, offset in ipairs({ 0, 55, -55, 115, -115, 180 }) do
            local direction = bearing + math.rad(offset)
            for _, range in ipairs({ 295, 360 }) do
                local x, y = point.X + math.cos(direction) * range, point.Y + math.sin(direction) * range
                local at = floor_at(context, point, x, y, height)
                if at and math.abs(at.Z - point.Z) <= 95 then
                    local facing = look_from(at, point)
                    if guest:K2_TeleportTo(at, { Pitch = 0, Yaw = facing.Yaw, Roll = 0 }) then
                        moved = true
                        local actual = guest:K2_GetActorLocation()
                        local _, error_distance = look_from(at, actual)
                        if error_distance <= 25 then
                            local length, current_aim, hit = settle_aim(context, point)
                            if hit then return length, current_aim, hit end
                        end
                    end
                end
            end
        end
    end)
    local function restore_pose()
        local restored, position_restored = pcall(function()
            local located = not moved or guest:K2_TeleportTo(original.position, original.pawn)
            controller:SetControlRotation(original.control)
            camera:K2_SetRelativeRotation(original.camera, false, {}, true)
            muzzle:K2_SetRelativeRotation(original.muzzle, false, {}, true)
            guest:ForceNetUpdate()
            return located
        end)
        if not restored or not position_restored then error("mutation_unverified_native_bandit_aim_pose", 0) end
    end
    if not applied or not valid(contact) then
        restore_pose()
        error(applied and "No exact stock Bandit line of fire from the qualified staging points"
            or "Stock Bandit aim could not be verified (" .. tostring(distance) .. ")", 0)
    end
    local read_aim, receipt = pcall(function()
        return { world = state.world, guest = guest, name = name, distance = distance,
            yaw = aim.Yaw, pitch = aim.Pitch, component = contact:GetFullName() }
    end)
    if not read_aim then
        restore_pose()
        error("The stock Bandit aim component was unavailable", 0)
    end
    if guid then
        -- No frame or stock AI tick between the qualifying trace and the standard shot.
        local read_scope, exact = pcall(function()
            return exact_watch(state, phase, host, guest, name, tracked) and same(guest.EquipedItem, weapon)
        end)
        if not read_scope or not exact then
            restore_pose()
            error("native_bandit_shot_scope_changed", 0)
        end
        local fired, status = fire_aimed_weapon(state, phase, detail.name, guid)
        if not fired then
            if not status:match("^mutation_unverified_") then restore_pose() end
            error(status, 0)
        end
        local updated = pcall(function() guest:ForceNetUpdate() end)
        if not updated then error("mutation_unverified_native_bandit_shot_net_update", 0) end
        aim_fixture = receipt
        return "stock Bandit aim and fire verified"
    end
    guest:ForceNetUpdate()
    aim_fixture = receipt
    return "stock Bandit aim verified"
end

function npc.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-bandit-position" and action ~= "native-bandit-spawn" and action ~= "native-bandit-client-spawner-probe"
        and action ~= "native-bandit-aim-fixture" and action ~= "native-bandit-shot-fixture" then return end
    if state.map ~= map and (action == "native-bandit-aim-fixture" or action == "native-bandit-shot-fixture") then
        local name, guid = fields[3], fields[5]
        local host, guest, exact = enemy_loot.watched_cqc(state, phase, name)
        if not fixtures or not exact then error("native_bandit_shot_watch_required", 0) end
        if action == "native-bandit-shot-fixture" then
            if #fields ~= 5 or fields[4] ~= guest:GetFullName() or type(guid) ~= "string" or #guid ~= 32
                or guid:find("[^0-9A-F]") or not guid:find("[^0]") then
                error("native_bandit_shot_exact_guest_and_weapon_required", 0)
            end
        elseif #fields ~= 3 then
            error("The stock enemy aim requires one exact live actor name", 0)
        end
        return bandit_aim(state, phase, host, guest, nil, name, guid, exact)
    end
    local pawn = active(state, phase)
    if not pawn or type(SilverfishReadNativeBandit) ~= "function" then error("The staged Playtest Bandit scope is unavailable", 0) end
    local source, arrow = source_in(state.world)
    if not source then error("The exact authored Bandit spawner is unavailable", 0) end

    if action == "native-bandit-client-spawner-probe" then
        if phase ~= "connected" or #fields ~= 2 then error("The Bandit spawner denial probe requires a staged client", 0) end
        local before = read(pawn)
        if not before[1] or before[3] ~= 0 or before[4] ~= 0 then error("The client Bandit baseline is unavailable", 0) end
        source:Send_PowerSignal(true, pawn)
        local after = read(pawn)
        if not after[1] or after[3] ~= 0 or after[4] ~= 0 or after[23] <= before[23] then
            error("The stock client spawner denial was not verified", 0)
        end
        return "stock client Bandit spawner denied"
    end

    local host, fresh = transport.scoped_host(state, phase)
    local guest = authority_guest(state, phase)
    if not fixtures or not host or not fresh or not same(host.pawn, pawn) or not guest or not source:HasAuthority() then
        error("The stock Bandit fixture requires an exact paired listen host", 0)
    end
    local before = read(pawn, guest)
    if action == "native-bandit-shot-fixture" then
        local name, guid = fields[4], fields[5]
        if #fields ~= 5 or name ~= state.guests[1].name or #guid ~= 32 or guid:find("[^0-9A-F]")
            or not guid:find("[^0]") then error("native_bandit_shot_exact_guest_and_weapon_required", 0) end
        return bandit_aim(state, phase, pawn, guest, before, fields[3], guid)
    end
    if action == "native-bandit-aim-fixture" then
        if #fields ~= 3 then error("The stock Bandit aim requires one exact live actor name", 0) end
        return bandit_aim(state, phase, pawn, guest, before, fields[3])
    end
    if not before[1] or before[2] ~= "waiting_for_stock_bandit" then error("The stock Bandit fixture requires an empty baseline", 0) end

    if action == "native-bandit-spawn" then
        if #fields ~= 2 or type(SilverfishSpawnNativeBandit) ~= "function" then error("The native Bandit spawn is unavailable", 0) end
        local ok, status, name = SilverfishSpawnNativeBandit(pawn:GetAddress(), source:GetAddress())
        if not ok then error("The stock Bandit spawn was not verified (" .. tostring(status) .. ")", 0) end
        return "stock Bandit spawned " .. name
    end

    if #fields ~= 3 or fields[3] ~= "host" and fields[3] ~= "guest" then error("Expose the exact host or guest to the stock Bandit", 0) end
    local exposed = fields[3] == "host" and pawn or guest
    local covered = same(exposed, pawn) and guest or pawn
    local origin, direction = arrow:K2_GetComponentLocation(), arrow:K2_GetComponentRotation()
    local yaw = math.rad(direction.Yaw)
    local forward_x, forward_y = math.cos(yaw), math.sin(yaw)
    local original_location, original_rotation = covered:K2_GetActorLocation(), covered:K2_GetActorRotation()
    if not place(covered, origin, original_rotation, forward_x, forward_y, -1300) then
        error("The rear Bandit fixture station could not be reached", 0)
    end
    if not place(exposed, origin, exposed:K2_GetActorRotation(), forward_x, forward_y, hostile and 480 or 850) then
        covered:K2_TeleportTo(original_location, original_rotation)
        covered:ForceNetUpdate()
        error("The front Bandit fixture station could not be reached", 0)
    end
    return "staged Bandit position for " .. fields[3]
end

return npc
