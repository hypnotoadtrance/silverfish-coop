local enemy_loot = {}
local guest_save = require("guest_save")
local transport = require("native_transport")

local map = "/Game/Classes/Maps/PlaytestMap_01"
local dead_bridge = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local cqc_class = "/Game/Classes/NPCs/NPC_State_Infantry_CQC.NPC_State_Infantry_CQC_C"
local cqc_path = dead_bridge .. ".DeadBridge_P:PersistentLevel.NPC_State_Infantry_CQC_C_0"
local cqc_name = "NPC_State_Infantry_CQC_C " .. cqc_path
local bandit_class = "/Game/Classes/NPCs/NPC_Bandit.NPC_Bandit_C"
local shells = "/Game/Classes/Items/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local projection_enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local observe = os.getenv("SILVERFISH_COOP_NATIVE_ENEMY_LOOT") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_NPC_TEST") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local enabled = observe and fixtures
local ordinary = projection_enabled and os.getenv("SILVERFISH_COOP_NATIVE_CQC_LOOT") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_LOOT") == "1"
local worlds = { [map] = "World " .. map .. ".PlaytestMap_01", [dead_bridge] = "World " .. dead_bridge .. ".DeadBridge_P" }
local watch, escrow, projection

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function participants(state, phase)
    if not enabled or phase ~= "hosting" or not worlds[state.map] or state.server == true or not state.listening
        or not valid(state.world) or state.world:GetFullName() ~= worlds[state.map]
        or not state.guests or #state.guests ~= 1 then return end
    local host, fresh = transport.scoped_host(state, phase)
    local guest = state.guests[1]
    if not host or not fresh or not same(host.pawn:GetWorld(), state.world) or type(guest.name) ~= "string"
        or not valid(guest.pawn) or guest.name ~= guest.pawn:GetFullName() then return end
    local protected, pawn, detached = pcall(guest_save.exact_item_context, state, phase, guest.name)
    if not protected or not same(pawn, guest.pawn) or not valid(detached) then return end
    return host.pawn, pawn
end

local function read(host)
    local result = table.pack(SilverfishReadNativeEnemyLoot(host:GetAddress()))
    if result.n ~= 15 and result.n ~= 23 and result.n ~= 26 then return end
    for index = 1, result.n do
        if result[index] == "" then result[index] = "-" end
        if type(result[index]) == "boolean" then result[index] = result[index] and 1 or 0 end
    end
    return result
end

local function ordinary_host(state, phase, finishing)
    if not ordinary or phase ~= "hosting" or state.map ~= dead_bridge or state.server ~= false or not state.listening
        or not valid(state.world) or state.world:GetFullName() ~= worlds[dead_bridge] then return end
    local host, fresh = transport.scoped_host(state, phase)
    if host and same(host.pawn:GetWorld(), state.world) and (fresh or finishing and watch and watch.ordinary
        and same(watch.world, state.world) and same(watch.host, host.pawn)) then return host.pawn end
end

local function member(state, phase, host, name)
    if type(name) ~= "string" or #name > 512 or name:find("%c") then return end
    if name == host:GetFullName() then return host end
    local ok, pawn, save, character = pcall(guest_save.exact_item_context, state, phase, name)
    if ok and valid(pawn) and valid(save) and type(character) == "string" then return pawn, save, character end
end

local function cqc_actor(state, name)
    if state.map ~= dead_bridge or name ~= cqc_name then return end
    local actor = StaticFindObject(cqc_path)
    if valid(actor) and not actor:IsActorBeingDestroyed() and actor:GetFullName() == cqc_name
        and same(actor:GetWorld(), state.world) and same(actor:GetClass(), StaticFindObject(cqc_class)) then return actor end
end

local function proxy_in(state, phase)
    if not projection_enabled or phase ~= "connected" or state.server ~= true or state.listening ~= true
        or state.map ~= dead_bridge then return end
    local world, controller = state.world, state.controller
    local detail = state.local_pawn
    local pawn = detail and detail.pawn
    if not valid(world) or world:GetFullName() ~= worlds[dead_bridge]
        or not same(world.PersistentLevel, StaticFindObject(dead_bridge .. ".DeadBridge_P:PersistentLevel"))
        or not valid(pawn) or not same(pawn:GetClass(), StaticFindObject(pawn_class))
        or not valid(controller) or not same(controller:GetClass(), StaticFindObject(controller_class))
        or not same(pawn:GetWorld(), world) or not same(controller:GetWorld(), world)
        or detail.name ~= pawn:GetFullName() or not same(detail.controller_object, controller)
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or pawn:HasAuthority() or controller:HasAuthority() or not pawn:IsLocallyControlled()
        or not controller:IsLocalController() or controller.Loading ~= false
        or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed() then return end
    local driver = world.NetDriver
    local connection = valid(driver) and driver.ServerConnection
    if not valid(connection) or not same(driver.World, world) or not same(connection.Driver, driver) then return end
    local actor = cqc_actor(state, cqc_name)
    if not valid(actor) or actor:GetLocalRole() ~= 1 or valid(actor.Controller)
        or not same(actor:GetOuter(), world.PersistentLevel) or type(actor.Alive) ~= "boolean" then return end
    return {world = world, pawn = pawn, connection = connection, actor = actor}
end

local function same_proxy(first, second)
    return first and second and same(first.world, second.world) and same(first.pawn, second.pawn)
        and same(first.connection, second.connection) and same(first.actor, second.actor)
end

local function clear_projection()
    local previous = projection
    projection = nil
    if not previous then return end
    pcall(function()
        local actor = previous.actor
        if not valid(actor) or actor:IsActorBeingDestroyed() or not same(actor:GetWorld(), previous.world)
            or actor:GetLocalRole() ~= 1 or valid(actor.Controller) or actor.Alive ~= true then return end
        if previous.hidden == false and actor.bHidden == true then actor:SetActorHiddenInGame(false) end
        if previous.collision == true and actor:GetActorEnableCollision() == false then actor:SetActorEnableCollision(true) end
    end)
end

function enemy_loot.clear()
    if watch and type(SilverfishClearNativeEnemyLootScope) == "function" then
        SilverfishClearNativeEnemyLootScope()
    end
    watch, escrow = nil, nil
    clear_projection()
end

function enemy_loot.clear_fixture()
    if watch and not watch.ordinary then enemy_loot.clear() end
end

function enemy_loot.departure_pending(state, phase)
    return ordinary and phase == "hosting" and state.map == dead_bridge and
        type(SilverfishNativeCqcEnemyLootPending) == "function" and SilverfishNativeCqcEnemyLootPending() == true
end

function enemy_loot.watched_cqc(state, phase, expected, receipt)
    local host, guest = participants(state, phase)
    if state.map ~= dead_bridge or expected ~= cqc_name or not watch or watch.name ~= expected
        or not same(state.world, watch.world)
        or not same(host, watch.host) or not watch.ordinary and not same(guest, watch.guest) then return end
    if receipt then return host, guest end
    local actor = cqc_actor(state, expected)
    if not valid(actor) or not actor:HasAuthority() or actor.Alive ~= true or actor.Initialized ~= true
        or not same(actor, watch.bandit) then return end
    local proof = read(host)
    if proof and proof[1] == "watching" then return host, guest, actor end
end

local function is_slot(slot)
    return type(slot) == "string" and #slot == 39 and slot:match("^SFCoop_[0-9a-f]+$")
end

local function is_guid(guid)
    return type(guid) == "string" and #guid == 32 and guid:match("^[0-9A-F]+$") and guid:find("[^0]")
end

local function project_death(fields, state, phase)
    local name, epoch, guid = fields[3], fields[4], fields[5]
    if #fields ~= 5 or name ~= cqc_name or #epoch ~= 32 or epoch:find("[^0-9a-f]") or not is_guid(guid) then
        error("native_enemy_loot_death_exact_receipt_required", 0)
    end
    local current = proxy_in(state, phase)
    if not current then error("native_enemy_loot_death_proxy_unavailable", 0) end
    local actor = current.actor
    if actor.Alive == false then return "stock enemy death flag already applied" end
    if actor.Alive ~= true or type(actor.bHidden) ~= "boolean" or type(actor:GetActorEnableCollision()) ~= "boolean" then
        error("native_enemy_loot_death_proxy_unavailable", 0)
    end
    if not same_proxy(current, projection) then
        clear_projection()
        current.hidden, current.collision = actor.bHidden, actor:GetActorEnableCollision()
        projection = current
    end
    local applied = pcall(function()
        if actor:GetActorEnableCollision() ~= false then actor:SetActorEnableCollision(false) end
        if actor.bHidden ~= true then actor:SetActorHiddenInGame(true) end
    end)
    if not applied or actor.bHidden ~= true or actor:GetActorEnableCollision() ~= false then
        clear_projection()
        error("native_enemy_loot_death_presentation_unverified", 0)
    end
    projection.epoch, projection.guid = epoch, guid
    return "stock enemy death presentation projected"
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) <= 10000000
end

local function retire_actor(ammo)
    ammo:SetActorEnableCollision(false)
    ammo:SetActorHiddenInGame(true)
    if ammo:GetActorEnableCollision() ~= false or ammo.bHidden ~= true then error("mutation_unverified_enemy_loot_retirement", 0) end
    ammo:K2_DestroyActor()
end

local function stash(state, phase, fields)
    local cqc = watch and watch.ordinary
    local host = cqc and ordinary_host(state, phase, true) or not cqc and participants(state, phase)
    local bandit, slot, expected = fields[3], fields[4], fields[5]
    local report = watch and watch.proof
    if not valid(host) or #fields ~= 5 or not is_slot(slot) or not report or not same(state.world, watch.world)
        or bandit ~= watch.name or (not cqc and not valid(watch.bandit)) or report[1] ~= "stock_loot_observed" or report[9] ~= 1
        or (cqc and (report.n ~= 26 or report[25] ~= 1 or report[26] ~= 0))
        or report[13] ~= expected or report[14] ~= shells then error("native_enemy_loot_roll_unavailable", 0) end
    local ammo = StaticFindObject(expected:match("^%S+ (.+)$") or "")
    if not valid(ammo) or not same(ammo:GetClass(), StaticFindObject(shells)) or not same(ammo:GetWorld(), state.world)
        or not ammo:HasAuthority() or ammo:IsActorBeingDestroyed() or ammo:GetFullName() ~= expected
        or type(ammo.Ammo) ~= "number" or ammo.Ammo ~= report[15] or ammo.Ammo < 3 or ammo.Ammo > 9 then
        error("native_enemy_loot_exact_drop_unavailable", 0)
    end
    local at, units = ammo:K2_GetActorLocation(), ammo.Ammo
    if not at or not finite(at.X) or not finite(at.Y) or not finite(at.Z) then error("native_enemy_loot_position_unavailable", 0) end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local class = StaticFindObject("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
    if not valid(api) or not valid(class) or api:DoesSaveGameExist(slot, 0)
        or type(SilverfishCaptureNativeEnemyLootEscrow) ~= "function" or type(SilverfishVerifyNativeLootEscrow) ~= "function" then
        error("native_enemy_loot_escrow_unavailable", 0)
    end
    local pool = api:CreateSaveGameObject(class)
    if not valid(pool) then error("native_enemy_loot_escrow_unavailable", 0) end
    local ok, reason, count, guid = SilverfishCaptureNativeEnemyLootEscrow(watch.address, pool:GetAddress(), ammo:GetAddress(), 0)
    if not ok then error(tostring(reason), 0) end
    if count ~= 1 or not is_guid(guid) or not api:SaveGameToSlot(pool, slot, 0) then
        error("mutation_unverified_enemy_loot_escrow_save", 0)
    end
    local restored = api:LoadGameFromSlot(slot, 0)
    if not valid(restored) or not SilverfishVerifyNativeLootEscrow(pool:GetAddress(), restored:GetAddress()) then
        error("mutation_unverified_enemy_loot_escrow_readback", 0)
    end
    escrow = {world = state.world, status = "captured", pawn = report[4], actor = expected, slot = slot, guid = guid, units = units,
        at = {X = at.X, Y = at.Y, Z = at.Z}}
    if cqc then
        watch.capture = escrow
        watch.capture.ammo, watch.capture.address = ammo, ammo:GetAddress()
        watch.proof = read(host) or watch.proof
    else
        retire_actor(ammo)
    end
    return "typed stock enemy ammunition escrowed"
end

local function retire(state, phase, fields)
    local host = ordinary_host(state, phase, true)
    local captured = watch and watch.ordinary and watch.capture
    if #fields ~= 5 or not valid(host) or not captured or not same(state.world, captured.world)
        or (fields[3] ~= captured.actor or fields[4] ~= captured.slot or fields[5] ~= captured.guid)
        or type(SilverfishFinishNativeCqcEnemyLoot) ~= "function" then
        error("native_enemy_loot_retirement_unavailable", 0)
    end
    if captured.status == "retired" then
        escrow = captured
        return "exact stock ammunition already retired"
    end
    local ammo = captured.ammo
    if not valid(ammo) or not same(ammo:GetWorld(), state.world) or ammo:GetFullName() ~= captured.actor
        or not same(ammo:GetClass(), StaticFindObject(shells)) or not ammo:HasAuthority() or ammo:IsActorBeingDestroyed() then
        error("mutation_unverified_enemy_loot_exact_retirement_actor", 0)
    end
    retire_actor(ammo)
    local ok, status = SilverfishFinishNativeCqcEnemyLoot(host:GetAddress(), captured.address)
    if ok ~= true or status ~= "retired" then error("mutation_unverified_enemy_loot_retirement", 0) end
    captured.status = "retired"
    escrow = captured
    watch.proof = read(host) or watch.proof
    return "exact stock ammunition retired after commit"
end

local function claim(state, phase, fields)
    local host = ordinary_host(state, phase)
    local guest
    local cqc = valid(host)
    if not cqc then host, guest = participants(state, phase) end
    local name, slot, guid = fields[3], fields[4], fields[5]
    local x, y, z = tonumber(fields[6]), tonumber(fields[7]), tonumber(fields[8])
    if #fields ~= 8 or not is_slot(slot) or not is_guid(guid) or not finite(x) or not finite(y) or not finite(z)
        or not valid(host) or type(SilverfishClaimNativeEnemyLootEscrow) ~= "function" then
        error("native_enemy_loot_claim_unavailable", 0)
    end
    local pawn = cqc and member(state, phase, host, name)
        or host:GetFullName() == name and host or valid(guest) and guest:GetFullName() == name and guest or nil
    if not pawn or pawn.Dead ~= false then error("native_enemy_loot_claimant_unavailable", 0) end
    local at, reach = pawn:K2_GetActorLocation(), pawn.InteractionRange
    if not at or not finite(reach) or reach <= 0 or reach > 1000 or
        (at.X - x)^2 + (at.Y - y)^2 + (at.Z - z)^2 > math.min(reach, 250)^2 then
        error("native_enemy_loot_out_of_reach", 0)
    end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pool = valid(api) and api:LoadGameFromSlot(slot, 0)
    if not valid(pool) or not valid(pawn.Save) or not valid(host.Save) then error("native_enemy_loot_claim_unavailable", 0) end
    local ok, status = SilverfishClaimNativeEnemyLootEscrow(pawn:GetAddress(), pawn.Save:GetAddress(),
        host.Save:GetAddress(), pool:GetAddress(), guid)
    if not ok then error(tostring(status), 0) end
    escrow = {world = state.world, status = status, pawn = name, actor = "-", slot = slot, guid = guid}
    return "typed stock enemy ammunition claim verified"
end

local function approach(state, phase, fields)
    local host = fixtures and ordinary_host(state, phase)
    local guest
    local cqc = valid(host)
    if not cqc then host, guest = participants(state, phase) end
    local name, guid = fields[3], fields[4]
    local x, y, z = tonumber(fields[5]), tonumber(fields[6]), tonumber(fields[7])
    if #fields ~= 7 or not is_guid(guid) or not finite(x) or not finite(y) or not finite(z) or not valid(host) then
        error("native_enemy_loot_fixture_unavailable", 0)
    end
    local pawn = cqc and member(state, phase, host, name)
        or host:GetFullName() == name and host or valid(guest) and guest:GetFullName() == name and guest or nil
    if not pawn or pawn.Dead ~= false then error("native_enemy_loot_claimant_unavailable", 0) end
    local before, facing = pawn:K2_GetActorLocation(), pawn:K2_GetActorRotation()
    local reach = pawn.InteractionRange
    if not finite(reach) or reach <= 0 or reach > 1000 or not before or not finite(before.Z) then
        error("native_enemy_loot_fixture_unavailable", 0)
    end
    for _, offset in ipairs({{123, 0}, {-123, 0}, {0, 123}, {0, -123}}) do
        local candidate = {X = x + offset[1], Y = y + offset[2], Z = math.max(z + 94, before.Z)}
        if pawn:K2_TeleportTo(candidate, facing) then
            local actual = pawn:K2_GetActorLocation()
            if finite(actual.X) and finite(actual.Y) and finite(actual.Z)
                and (actual.X - x)^2 + (actual.Y - y)^2 + (actual.Z - z)^2 <= math.min(reach, 250)^2 then
                escrow = {world = state.world, status = "approached", pawn = name, guid = guid, actor = "-", slot = "-"}
                return "stock enemy loot approach verified"
            end
            if not pawn:K2_TeleportTo(before, facing) then error("mutation_unverified_enemy_loot_fixture_rollback", 0) end
        end
    end
    error("native_enemy_loot_fixture_no_clear_location", 0)
end

function enemy_loot.sync(state, phase)
    if projection then
        local ok, current = pcall(proxy_in, state, phase)
        if not ok or not same_proxy(current, projection) then clear_projection() end
    end
    if watch and watch.ordinary and valid(state.world) and not same(state.world, watch.world) then enemy_loot.clear() end
    if ordinary and state.map == dead_bridge then
        local host = ordinary_host(state, phase)
        if not valid(host) or type(SilverfishSetNativeCqcEnemyLootScope) ~= "function"
            or type(SilverfishReadNativeEnemyLoot) ~= "function" then return end
        local actor = cqc_actor(state, cqc_name)
        local address = valid(actor) and actor:GetAddress() or watch and watch.ordinary and watch.address
        if not address then return end
        local arguments = {host:GetAddress(), address}
        for _, guest in ipairs(state.guests or {}) do
            local pawn, save, character = member(state, phase, host, guest.name)
            if valid(save) and same(pawn, guest.pawn) then
                arguments[#arguments + 1] = pawn:GetAddress()
                arguments[#arguments + 1] = save:GetAddress()
                arguments[#arguments + 1] = character
            end
        end
        if SilverfishSetNativeCqcEnemyLootScope(table.unpack(arguments)) == true then
            if not watch or not watch.ordinary then
                watch = {world = state.world, host = host, bandit = actor, name = cqc_name, address = address, ordinary = true}
            end
            watch.proof = read(host) or watch.proof
        end
        return
    end
    if not watch then return end
    local host, guest = participants(state, phase)
    if not same(state.world, watch.world) or not same(host, watch.host) or not same(guest, watch.guest) then
        enemy_loot.clear()
        return
    end
    if not valid(watch.bandit) or watch.bandit:IsActorBeingDestroyed() then return end
    local accepted = SilverfishSetNativeEnemyLootScope(host:GetAddress(), guest:GetAddress(), watch.bandit:GetAddress())
    if accepted then watch.proof = read(host) or watch.proof end
end

function enemy_loot.command(fields, state, phase)
    local action = fields[2]
    if action == "native-enemy-loot-project-death" then return project_death(fields, state, phase) end
    if action == "native-enemy-loot-retire" then return retire(state, phase, fields) end
    if action == "native-enemy-loot-bank" or action == "native-enemy-loot-claim" then
        escrow = nil
        return (action == "native-enemy-loot-bank" and stash or claim)(state, phase, fields)
    end
    if action == "native-enemy-loot-approach" then
        escrow = nil
        return approach(state, phase, fields)
    end
    if action == "native-enemy-loot-clear" and #fields == 2 then
        if not enabled or (ordinary and state.map == dead_bridge) then error("native_enemy_loot_fixture_unavailable", 0) end
        enemy_loot.clear_fixture()
        return "stock enemy loot observer cleared"
    end
    if action ~= "native-enemy-loot-watch" then return end
    local expected = fields[3]
    local host, guest = participants(state, phase)
    if (ordinary and state.map == dead_bridge) or not valid(host) or not valid(guest) or #fields ~= 3 or type(expected) ~= "string" or #expected > 512
        or expected:find("%c")
        or type(SilverfishSetNativeEnemyLootScope) ~= "function" or type(SilverfishReadNativeEnemyLoot) ~= "function" then
        error("The stock enemy loot observer requires an exact staged host and enrolled guest", 0)
    end
    local bandit
    if state.map == dead_bridge then
        bandit = cqc_actor(state, expected)
        if not valid(bandit) or not bandit:HasAuthority() or bandit.Alive ~= true or bandit.Initialized ~= true then
            error("The exact live Dead Bridge CQC is unavailable", 0)
        end
    else
        if type(SilverfishReadNativeBandit) ~= "function" then error("The stock Bandit authority hook is unavailable", 0) end
        local authority = table.pack(SilverfishReadNativeBandit(host:GetAddress(), guest:GetAddress()))
        if authority.n ~= 24 or authority[1] ~= true or authority[2] ~= "stock_bandit_authority"
            or authority[3] ~= 1 or authority[4] ~= 0 or authority[5] ~= expected
            or authority[11] ~= true or authority[12] ~= true then
            error("The exact live stock Bandit authority receipt is unavailable", 0)
        end
        local class = StaticFindObject(bandit_class)
        for _, candidate in ipairs(FindAllOf("NPC_Bandit_C") or {}) do
            if valid(candidate) and candidate:GetFullName() == expected and candidate:HasAuthority()
                and same(candidate:GetWorld(), state.world) and same(candidate:GetClass(), class) then
                if bandit then error("The exact stock Bandit is ambiguous", 0) end
                bandit = candidate
            end
        end
    end
    if not valid(bandit) then error("The exact stock Bandit is unavailable", 0) end
    enemy_loot.clear()
    local accepted, reason = SilverfishSetNativeEnemyLootScope(host:GetAddress(), guest:GetAddress(), bandit:GetAddress())
    if accepted ~= true or reason ~= "watching" then
        error("The stock enemy loot watch could not bind (" .. tostring(reason) .. ")", 0)
    end
    watch = { world = state.world, host = host, guest = guest, bandit = bandit, address = bandit:GetAddress(), name = expected, proof = read(host) }
    return "stock enemy loot watch armed"
end

function enemy_loot.rows(state, phase, status_row)
    local rows = {}
    if ordinary and type(SilverfishNativeCqcEnemyLootAvailable) == "function" and SilverfishNativeCqcEnemyLootAvailable() == true
        and type(SilverfishCaptureNativeEnemyLootEscrow) == "function" and type(SilverfishClaimNativeEnemyLootEscrow) == "function" then
        rows[#rows + 1] = status_row("native-enemy-loot-cqc-capability", 1)
    end
    local checked, current = pcall(proxy_in, state, phase)
    if checked and current then
        local actor = current.actor
        local mode, epoch, guid = actor.Alive == false and "stock" or "present", "-", "-"
        local hidden, collision = actor.bHidden == true, actor:GetActorEnableCollision() == true
        if same_proxy(current, projection) and projection.epoch and hidden and not collision then
            mode, epoch, guid = "projected", projection.epoch, projection.guid
        end
        rows[#rows + 1] = status_row("native-enemy-loot-death-view", dead_bridge, cqc_name, mode, epoch, guid,
            hidden and 1 or 0, collision and 1 or 0)
    end
    if observe and phase == "connected" and state.server == true and state.map == dead_bridge and valid(state.world)
        and state.world:GetFullName() == worlds[dead_bridge] then
        local exact = cqc_actor(state, cqc_name)
        local authorities, replicas, others = 0, 0, 0
        local class = StaticFindObject(cqc_class)
        for _, actor in ipairs(FindAllOf("NPC_State_Infantry_CQC_C") or {}) do
            if valid(actor) and same(actor:GetClass(), class) and same(actor:GetWorld(), state.world)
                and not actor:IsActorBeingDestroyed() then
                local role = actor:GetLocalRole()
                if role == 3 then authorities = authorities + 1
                elseif role == 1 then replicas = replicas + 1
                else others = others + 1 end
            end
        end
        rows[#rows + 1] = status_row("native-enemy-loot-counterpart", dead_bridge, cqc_name,
            valid(exact) and exact:GetLocalRole() or "-", valid(exact) and valid(exact.Controller) and 1 or 0,
            valid(exact) and exact.Alive == true and 1 or 0, authorities, replicas, others)
    end
    if phase ~= "hosting" or not worlds[state.map] then return rows end
    if watch and watch.proof and same(state.world, watch.world) then
        rows[#rows + 1] = status_row("native-enemy-loot", state.map, watch.name, table.unpack(watch.proof, 1, 15))
        if watch.proof.n >= 23 then
            rows[#rows + 1] = status_row("native-enemy-bounty", state.map, watch.name, table.unpack(watch.proof, 16, 23))
        end
        if watch.proof.n == 26 and watch.ordinary then
            rows[#rows + 1] = status_row("native-enemy-loot-cqc", state.map, watch.name, table.unpack(watch.proof, 24, 26))
        end
    end
    if escrow and same(escrow.world, state.world) then
        local at = escrow.at
        rows[#rows + 1] = status_row("native-enemy-loot-escrow", state.map, escrow.status, escrow.pawn, escrow.actor,
            escrow.slot, escrow.guid, escrow.units or "-", at and at.X or "-", at and at.Y or "-", at and at.Z or "-")
    end
    return rows
end

return enemy_loot
