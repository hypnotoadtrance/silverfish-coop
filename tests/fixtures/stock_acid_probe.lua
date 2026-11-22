local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") ~= "1"
    or not mailbox or not mailbox:find("acid%-proof%-%x+") then return end

local level = "/Game/Classes/Maps/PlaytestMap_01.PlaytestMap_01"
local source_path = level .. ":PersistentLevel.ToxicSludge_Plane_BP_C_1"
local damage_function = "/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage"
local damage_effect = "/Game/Classes/Player/Player_FP.Player_FP_C:Pulse_Dmg_FX"
local acid_field = "Acid_20_F4C551B34F620FDF72D2F2A64DE15F3E"
local scope, previous, hooks_installed

local function valid(object)
    return object and object:IsValid()
end

local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end

local function exact(object, class)
    return valid(object) and same(object:GetClass(), StaticFindObject(class))
end

local function qualified(pawn, world, locally_controlled)
    return exact(pawn, "/Game/Classes/Player/Player_FP.Player_FP_C") and same(pawn:GetWorld(), world)
        and not pawn:IsActorBeingDestroyed() and pawn:HasAuthority()
        and pawn:IsLocallyControlled() == locally_controlled and valid(pawn.Save)
end

local function watch_damage()
    if hooks_installed then return end
    RegisterHook(damage_function, function(context, damage, _, instigator)
        local current = scope
        if not current or not same(instigator:get(), current.source) then return end
        local pawn = context:get()
        local acid = damage:get()[acid_field]
        if type(acid) ~= "number" or not (acid > 0) then return end
        if same(pawn, current.victim) then
            current.hits = current.hits + 1
            current.acid = acid
        elseif same(pawn, current.helper) then
            current.helper_hits = current.helper_hits + 1
        end
    end)
    RegisterHook(damage_effect, function(context, amount, _, _, damage)
        local current = scope
        if current and same(context:get(), current.victim) and amount:get() > 0
            and (damage:get()[acid_field] or 0) > 0 then
            current.effects = current.effects + 1
        end
    end)
    hooks_installed = true
end

local function stock_source(world)
    local source = StaticFindObject(source_path)
    assert(exact(source, "/Game/Classes/Actors/ToxicSludge_Plane_BP.ToxicSludge_Plane_BP_C")
        and source:GetFullName() == "ToxicSludge_Plane_BP_C " .. source_path
        and same(source:GetWorld(), world) and same(source:GetOuter(), world.PersistentLevel)
        and source:HasAuthority() and not source:IsActorBeingDestroyed(), "the placed acid sludge is unavailable")
    local water, dot = source.WaterBox, source.DotVolume
    assert(exact(water, "/Script/Engine.BoxComponent") and same(water:GetOwner(), source)
        and water:GetGenerateOverlapEvents()
        and exact(dot, "/Game/Classes/Actors/StatusEffect_Actors/DotVolume.DotVolume_C")
        and same(dot:GetOwner(), source) and dot.DotActive and dot.BaseDamage[acid_field] == 20,
        "the placed sludge's stock acid overlap is unavailable")
    return source, water
end

local function place(current)
    local victim, source, water = current.victim, current.source, current.water
    local capsule = victim.CapsuleComponent
    assert(exact(capsule, "/Script/Engine.CapsuleComponent") and same(capsule:GetOwner(), victim))
    local center, extent = water:K2_GetComponentLocation(), water:GetScaledBoxExtent()
    assert(extent.X > 900 and extent.Y > 900 and extent.Z > 50, "unexpected acid-water bounds")
    local top = center.Z + extent.Z
    local feet_offset = capsule:GetScaledCapsuleHalfHeight()
    for _, offset in ipairs({{0, 0}, {-0.5, 0}, {0.5, 0}, {0, -0.5}, {0, 0.5}}) do
        for _, depth in ipairs({20, 60}) do
            local point = {X = center.X + extent.X * offset[1], Y = center.Y + extent.Y * offset[2],
                Z = top + feet_offset - depth}
            if victim:K2_TeleportTo(point, current.rotation)
                and water:IsOverlappingActor(victim) and source:IsOverlappingActor(victim)
                and not source:IsOverlappingActor(current.helper) then
                victim:ForceNetUpdate()
                return
            end
        end
    end
    assert(victim:K2_TeleportTo(current.origin, current.rotation), "could not return after checking acid overlap")
    error("could not enter the stock acid-water overlap")
end

local function command(operation, world, driver, host)
    assert(not valid(driver.ServerConnection) and qualified(host, world, true), "requires the native listen host")
    local connections = driver.ClientConnections
    assert(#connections == 1, "requires exactly one connected test guest")
    local peer = connections[1].PlayerController
    local guest = valid(peer) and peer.Pawn
    assert(qualified(guest, world, false) and not same(host.Save, guest.Save), "guest save is not detached")

    if operation == "return" then
        local current = scope
        assert(current and same(current.world, world) and same(current.host, host) and same(current.guest, guest)
            and same(current.victim.Save, current.save), "the qualified acid trial changed")
        local victim = current.victim
        assert(victim:K2_TeleportTo(current.origin, current.rotation) and not current.source:IsOverlappingActor(victim),
            "could not return the victim from the acid overlap")
        victim:ForceNetUpdate()
        scope = nil
        return ("%d|%d|%g|%d|%g|%g|%g|%g"):format(current.hits, current.helper_hits, current.acid, current.effects,
            current.victim_before, current.save.PlayerHealth, current.helper_before, current.helper.Save.PlayerHealth)
    end

    assert(not scope and (operation == "host" or operation == "guest"), "choose a standing seat for the acid overlap")
    local source, water = stock_source(world)
    local victim, helper = host, guest
    if operation == "guest" then victim, helper = guest, host end
    assert(victim.Dead == false and helper.Dead == false and victim.Save.PlayerHealth > 0 and helper.Save.PlayerHealth > 0
        and not source:IsOverlappingActor(victim) and not source:IsOverlappingActor(helper),
        "both test players must start alive and outside the acid")
    watch_damage()
    scope = {world = world, host = host, guest = guest, victim = victim, helper = helper, save = victim.Save,
        source = source, water = water, origin = victim:K2_GetActorLocation(), rotation = victim:K2_GetActorRotation(),
        victim_before = victim.Save.PlayerHealth, helper_before = helper.Save.PlayerHealth,
        hits = 0, helper_hits = 0, acid = 0, effects = 0}
    local placed, issue = pcall(place, scope)
    if not placed then
        scope = nil
        error(issue)
    end
    return source:GetFullName() .. "|" .. victim:GetFullName()
end

LoopInGameThreadWithDelay(60, function()
    local file = io.open(mailbox .. "/acid-probe", "rb")
    if not file then return end
    local request = file:read("*l")
    file:close()
    if not request or request == previous then return end
    local id, operation = request:match("^([a-f0-9]+)|([a-z]+)$")
    if not id then return end
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) or world:GetFullName() ~= "World " .. level or not valid(world.NetDriver) then return end
    previous = request
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local ok, result = pcall(command, operation, world, world.NetDriver, statics:GetPlayerPawn(world, 0))
    local reply = assert(io.open(mailbox .. "/acid-reply", "wb"))
    reply:write(id, ok and "|ok|" or "|failed|", tostring(result):gsub("[\r\n]", " "), "\n")
    reply:close()
end)
