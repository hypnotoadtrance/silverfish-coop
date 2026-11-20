local fixture = {}
local guest_save = require("guest_save")
local mindcore = require("native_mindcore")
local players = require("players")

local enabled = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local install = "native-mindcore-install-fixture"
local approach = "native-mindcore-approach-fixture"

local function refuse(reason) error("native_mindcore_fixture_refused_" .. reason, 0) end
local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function finite(n) return type(n) == "number" and n == n and math.abs(n) <= 10000000 end
local function point(p) return p and finite(p.X) and finite(p.Y) and finite(p.Z) end
local function within(a, b, distance)
    return point(a) and point(b) and (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 <= distance^2
end

local function move_core(scene, pawn)
    if pawn ~= scene.host:GetFullName() or scene.host.Dead ~= false or valid(scene.host.HeldObject)
        or scene.status ~= 0 or scene.installed or scene.cradle.Powered ~= false then refuse("inactive_host_scope") end
    local cap = scene.cradle.Capsule
    local capsule_class = StaticFindObject("/Script/Engine.CapsuleComponent")
    if not same(cap and cap:GetClass(), capsule_class) or not same(cap:GetOwner(), scene.cradle)
        or not cap:GetGenerateOverlapEvents() or not scene.core:GetActorEnableCollision()
        or cap:IsOverlappingActor(scene.core) ~= false or not scene.frame:GetGenerateOverlapEvents()
        or scene.frame:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)) ~= true then refuse("stock_overlap_unavailable") end

    local center, source = cap:K2_GetComponentLocation(), scene.core:K2_GetActorLocation()
    if not within(source, {X = 714.3901, Y = -1686.9511, Z = 118.3686}, 300)
        or not within(center, scene.cradle:K2_GetActorLocation(), 100) then refuse("authored_geometry_unavailable") end
    local moved, result = pcall(function()
        return scene.core:K2_SetActorLocation({X = center.X, Y = center.Y, Z = center.Z}, false, {}, true)
    end)
    if not moved or result ~= true then error("mutation_unverified_native_mindcore_fixture_core_move", 0) end
    if cap:IsOverlappingActor(scene.core) ~= true
        and not within(scene.core:K2_GetActorLocation(), scene.mount:K2_GetComponentLocation(), 12) then
        error("mutation_unverified_native_mindcore_fixture_stock_overlap", 0)
    end
    return "headless exact stock Core moved into authored Capsule overlap"
end

local function place_guest(scene, state, phase, name)
    local pawn, detached = guest_save.exact_item_context(state, phase, name)
    if not valid(pawn) or not valid(detached) or same(detached, scene.canonical)
        or pawn.Dead ~= false or not players.is_standing_guest(pawn, name) then refuse("standing_guest_unavailable") end
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local height = valid(pawn.CapsuleComponent) and pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    local range, origin = pawn.InteractionRange, scene.controls:K2_GetComponentLocation()
    if not valid(library) or not point(origin) or not finite(height) or height < 20 or height > 150
        or not finite(range) or range <= 0 or range > 1000 then refuse("stock_controls_geometry_unavailable") end
    local old, rotation = pawn:K2_GetActorLocation(), pawn:K2_GetActorRotation()
    local anchor, color = scene.cradle:K2_GetActorLocation(), {R = 0, G = 0, B = 0, A = 0}
    local moved = false
    for _, offset in ipairs({{0, -130}, {0, 130}, {-130, 0}, {130, 0},
                              {-95, -95}, {95, -95}, {-95, 95}, {95, 95},
                              {0, -170}, {0, 170}, {-170, 0}, {170, 0}}) do
        local x, y = origin.X + offset[1], origin.Y + offset[2]
        local hit = {}
        local traced = library:LineTraceSingle(pawn, {X = x, Y = y, Z = origin.Z + 90},
            {X = x, Y = y, Z = origin.Z - 300}, 0, false, {pawn, scene.host, scene.cradle, scene.core},
            0, hit, true, color, color, 0)
        hit = hit.OutHit or hit
        if traced == true and point(hit.ImpactPoint) and point(hit.ImpactNormal) and hit.ImpactNormal.Z >= .7 then
            local target = {X = x, Y = y, Z = hit.ImpactPoint.Z + height + 4}
            if within(target, origin, range) and within(target, anchor, range) and pawn:K2_TeleportTo(target, rotation) then
                moved = true
                local actual = pawn:K2_GetActorLocation()
                if within(actual, target, 12) and within(actual, origin, range) and within(actual, anchor, range) then
                    pawn:ForceNetUpdate()
                    return "headless detached guest reached the exact stock console controls"
                end
            end
        end
    end
    if moved then
        if not pawn:K2_TeleportTo(old, rotation) or not within(pawn:K2_GetActorLocation(), old, 12) then
            error("mutation_unverified_native_mindcore_fixture_guest_restore", 0)
        end
        pawn:ForceNetUpdate()
    end
    refuse("console_approach_no_safe_floor")
end

function fixture.command(fields, state, phase)
    local verb, name = fields[2], fields[3]
    if verb ~= install and verb ~= approach then return end
    if not enabled then refuse("not_enabled") end
    if #fields ~= 3 or type(name) ~= "string" or #name > 512 or not name:match("^Player_FP_C ") then
        refuse("exact_pawn_required")
    end
    local scene = mindcore.scene(state, phase)
    if verb == install then return move_core(scene, name) end
    return place_guest(scene, state, phase, name)
end

return fixture
