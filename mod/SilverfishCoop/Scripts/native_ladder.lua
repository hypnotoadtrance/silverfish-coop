local ladder = {}
local guest_save = require("guest_save")
local class = "BlueprintGeneratedClass /Game/Classes/Actors/Ladder_BP.Ladder_BP_C"
local local_mount
local mounts = {}

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function stock(actor, box, world)
    return valid(actor) and valid(box) and same(actor:GetWorld(), world)
        and actor:GetClass():GetFullName() == class and not actor:IsActorBeingDestroyed()
        and same(actor.Box, box) and same(box:GetOwner(), actor)
end

local function attached(actor, pawn)
    return valid(actor) and actor.Stick == true and pawn.OnLadder == true and same(actor.PlayerRef, pawn)
end

local function anchor(box)
    local point = box:K2_GetComponentLocation()
    return ("%.0f,%.0f,%.0f"):format(point.X, point.Y, point.Z)
end

function ladder.focused_target(pawn, box)
    if pawn.OnLadder ~= true or pawn:HasAuthority() or local_mount then return end
    box = valid(box) and box or pawn.InteractionTarget_Component
    local actor = valid(box) and box:GetOwner()
    if not stock(actor, box, pawn:GetWorld()) or not attached(actor, pawn) then return end
    local_mount = {pawn = pawn, world = pawn:GetWorld(), id = anchor(box)}
    return "ladder:mount:" .. local_mount.id
end

function ladder.release_target(pawn)
    local mount = local_mount
    if not mount then return end
    if not same(pawn, mount.pawn) or not same(pawn:GetWorld(), mount.world) then
        local_mount = nil
    elseif pawn.OnLadder == false then
        local_mount = nil
        return "ladder:release:" .. mount.id
    end
end

local function stop(mount)
    local actor, pawn = mount.actor, mount.pawn
    if valid(pawn) and not pawn:IsActorBeingDestroyed() and attached(actor, pawn) then
        actor["Start/Stop_Climbing"](actor, false)
    end
end

function ladder.sync(state, phase, downed)
    if phase ~= "connected" or not state.listening then local_mount = nil end
    for name, mount in pairs(mounts) do
        local pawn = mount.pawn
        if phase ~= "hosting" or not state.listening or not same(state.world, mount.world)
            or not valid(pawn) or pawn:IsActorBeingDestroyed() or downed[name] or not attached(mount.actor, pawn) then
            if same(state.world, mount.world) then stop(mount) end
            mounts[name] = nil
        end
    end
end

function ladder.command(fields, state, phase)
    if fields[2] ~= "native-ladder-state" then return end
    local name, mode, x, y, z = fields[3], fields[4], tonumber(fields[5]), tonumber(fields[6]), tonumber(fields[7])
    if #fields ~= 7 or phase ~= "hosting" or not state.listening
        or (mode ~= "mount" and mode ~= "release") or not x or not y or not z then error("ladder_scope_unavailable", 0) end
    local pawn = guest_save.exact_item_context(state, phase, name)
    if not valid(pawn) or not pawn:HasAuthority() or pawn:IsLocallyControlled() or not same(pawn:GetWorld(), state.world) then
        error("ladder_guest_unavailable", 0)
    end
    local id = ("%d,%d,%d"):format(x, y, z)
    local mount = mounts[name]
    if mode == "release" then
        if mount and same(mount.pawn, pawn) and mount.id == id then
            stop(mount)
            mounts[name] = nil
        end
        return "released"
    end
    if mount and attached(mount.actor, pawn) then
        if mount.id == id then return "mounted" end
        error("ladder_guest_already_attached", 0)
    end
    local selected, box
    for _, actor in ipairs(FindAllOf("Ladder_BP_C") or {}) do
        local part = valid(actor) and actor.Box
        if stock(actor, part, state.world) and anchor(part) == id then
            if selected then error("ladder_anchor_ambiguous", 0) end
            selected, box = actor, part
        end
    end
    if not selected or pawn.OnLadder or selected.Stick then error("ladder_unavailable", 0) end
    -- The stock start snaps the pawn to the ladder rail. Keep it in the immediate scene.
    local point, extent = pawn:K2_GetActorLocation(), box:GetScaledBoxExtent()
    local separation = (point.X-x)^2 + (point.Y-y)^2
    if separation > 350^2 or math.abs(point.Z-z) > extent.Z + 250 then error("ladder_anchor_out_of_scene", 0) end
    selected:SendInteractionToActor(pawn, box)
    if not attached(selected, pawn) then error("ladder_stock_mount_unverified", 0) end
    mounts[name] = {id = id, pawn = pawn, actor = selected, world = state.world}
    return "mounted"
end

return ladder

