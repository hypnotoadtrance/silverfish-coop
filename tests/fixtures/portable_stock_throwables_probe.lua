local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-stock-throwables-"
local world_name = "World /Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local folder = "/Game/Classes/Equipment/"
local projectile_folder = folder .. "Equipment_Assets/Equimpents_Projectiles/"
local inventory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local profiles = {
    bolt = {"Eqp_Bolt", "Bolt_Projectile_BP_C"},
    pipebomb = {"Gren_PipeBomb", "PipeBomb_Projectile_BP_C"},
}
local shots, blasts, last = {}, {}, nil
local watch_pawn, short_bolt_samples

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path); object = StaticFindObject(path) end
    assert(valid(object), path)
    return object
end

local function face_open_space(pawn, held, kind)
    local camera, controller = pawn.Camera, pawn.Controller
    local trace = asset("/Script/Engine.Default__KismetSystemLibrary")
    local direction = controller:GetControlRotation()
    local origin = camera:K2_GetComponentLocation()
    local color = {R = 0, G = 0, B = 0, A = 1}
    local aim
    if kind == "pipebomb" then
        aim = {Pitch = -18, Yaw = direction.Yaw, Roll = 0}
    else
        for _, pitch in ipairs({25, 45, 60, 5}) do
            for turn = 0, 7 do
                local yaw = direction.Yaw + 45 * turn
                local pitch_rad, yaw_rad = math.rad(pitch), math.rad(yaw)
                local point = {X = origin.X + 480 * math.cos(pitch_rad) * math.cos(yaw_rad),
                    Y = origin.Y + 480 * math.cos(pitch_rad) * math.sin(yaw_rad), Z = origin.Z + 480 * math.sin(pitch_rad)}
                if not trace:LineTraceSingle(pawn, origin, point, 0, true, {pawn, held}, 0, {}, true, color, color, 0) then
                    aim = {Pitch = pitch, Yaw = yaw, Roll = 0}
                    break
                end
            end
            if aim then break end
        end
    end
    assert(aim, "no clear line for the stock bolt")
    controller:SetControlRotation(aim)
    pawn:K2_SetActorRotation({Pitch = 0, Yaw = aim.Yaw, Roll = 0}, false)
    camera:K2_SetWorldRotation(aim, false, {}, true)
end

local function stock_owner(pawn, world, seat)
    assert(pawn:HasAuthority(), "stock placement and seeding take place on the host")
    if seat == "host" then return pawn end
    assert(seat == "guest")
    local owner
    for _, other in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(other) and other:HasAuthority() and not other:IsLocallyControlled()
            and not other:IsActorBeingDestroyed() and same(other:GetWorld(), world) then
            assert(not owner, "the private fixture expects one guest")
            owner = other
        end
    end
    assert(owner and valid(owner.Save) and not same(owner.Save, pawn.Save), "no detached guest inventory")
    return owner
end

local function stage_bolt(pawn, world, owner_side)
    local owner = stock_owner(pawn, world, owner_side)
    local trace = asset("/Script/Engine.Default__KismetSystemLibrary")
    local color = {R = 0, G = 0, B = 0, A = 1}
    -- Use the outdoor yard below the campfire where the stock pipe bomb lands.
    local positions = owner_side == "guest" and {420, 150} or {-400, -120}
    for _, y in ipairs(positions) do
        local x, ground = 3810, {}
        if trace:LineTraceSingle(owner, {X = x, Y = y, Z = -2180}, {X = x, Y = y, Z = -2450},
            0, false, {pawn, owner}, 0, ground, true, color, color, 0) then
            local floor = (ground.OutHit or ground).ImpactPoint
            local target = {X = x, Y = y, Z = floor.Z + owner.CapsuleComponent:GetScaledCapsuleHalfHeight() + 3}
            if owner:K2_TeleportTo(target, {Pitch = 0, Yaw = 90, Roll = 0}) then
                owner:ForceNetUpdate()
                return ("%.3f,%.3f,%.3f"):format(target.X, target.Y, target.Z)
            end
        end
    end
    error("could not stage the stock throw in the yard")
end

RegisterBeginPlayPostHook(function(context)
    local actor = context:get()
    if not valid(actor) or actor:IsActorBeingDestroyed() then return end
    local name = actor:GetClass():GetFullName()
    local world = actor:GetWorld()
    if not valid(world) or not valid(world.NetDriver) or world:GetFullName() ~= world_name then return end
    if name == "BlueprintGeneratedClass /Game/Classes/Actors/ExplosionActor_BP.ExplosionActor_BP_C" then
        local sources = actor["Actors to Ignore"]
        local source = sources and sources[1]
        if valid(source) and same(source, shots.pipebomb and shots.pipebomb.actor) then
            local pfx, sfx, attenuation = actor.PFX, actor.SFX, actor["Attenuation Settings"]
            blasts[#blasts + 1] = table.concat({actor:GetFullName(),
                valid(pfx) and pfx:GetFullName() or "-", valid(sfx) and sfx:GetFullName() or "-",
                valid(attenuation) and attenuation:GetFullName() or "-"}, "|")
        end
        return
    end
    if not name:find(projectile_folder, 1, true) then return end
    for kind, profile in pairs(profiles) do
        if name:sub(-#profile[2]) == profile[2] then
            local point = actor:K2_GetActorLocation()
            shots[kind] = {actor = actor, class = actor:GetClass(), role = actor:GetLocalRole(), name = actor:GetFullName(),
                clock = StaticFindObject("/Script/Engine.Default__GameplayStatics"), world = world,
                x = point.X, y = point.Y, z = point.Z, peak = 0, fuse = actor.FuseTime, visible = 0}
            shots[kind].began = shots[kind].clock:GetTimeSeconds(world)
        end
    end
end)

local function run(action, kind, arg)
    local profile = assert(profiles[kind], "unknown throwable kind")
    if action == "blasts" then return #blasts .. ":" .. table.concat(blasts, ";") end
    if action == "shot" then
        if kind == "bolt" and (short_bolt_samples or 0) > 0 then return "visual," .. short_bolt_samples end
        local shot = shots[kind]
        if not shot then return "absent" end
        local active, result = false, {}
        shot.clock:GetAllActorsOfClass(shot.world, shot.class, result)
        for _, actor in ipairs(result.OutActors or result) do
            if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
            if same(actor, shot.actor) then active = true; break end
        end
        local display = active and shot.actor.DisplayMesh
        local detail = valid(display) and table.concat({tostring(display:IsVisible()), tostring(display.bHiddenInGame),
            tostring(valid(display.StaticMesh)), tostring(shot.actor.bHidden)}, ":") or "no-mesh"
        return ("%s,%d,%s,%.3f,%.3f,%d,%.3f,%s"):format(shot.name, shot.role, tostring(not not active), shot.peak,
            shot.fuse, shot.visible, shot.clock:GetTimeSeconds(shot.world) - shot.began, detail)
    end
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and valid(world.NetDriver) and world:GetFullName() == world_name, "Observatory unavailable")
    local pawn = asset("/Script/Engine.Default__GameplayStatics"):GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn.Save, world.OwningGameInstance.SaveSlot_Ref),
        "the local stock pawn is not ready")
    if action == "reset" then
        shots[kind] = nil
        watch_pawn, short_bolt_samples = kind == "bolt" and pawn or nil, 0
        return "reset"
    end
    if action == "stage" then return stage_bolt(pawn, world, arg) end
    local name = profile[1]
    local class = asset(folder .. name .. "." .. name .. "_C")
    if action == "seed" then
        local owner = stock_owner(pawn, world, arg)
        local output = {}
        asset(inventory):MakeItemStack(class, 1, owner, output)
        local row = output.Item or output
        assert(same(row[item_class], class), "the stock constructor returned a different item")
        local guid = row[item_guid]
        if owner ~= pawn then
            local pack = owner.Save.PlayerBackpack
            pack[pack:GetArrayNum() + 1] = row
        else
            local slots = pawn.Save.PlayerEquipment
            local slot
            for index = 1, 8 do
                if not valid(slots[index][item_class]) then slot = index - 1; break end
            end
            assert(slot, "the host needs one free quick slot")
            slots[slot + 1] = row
            pawn:Equip_NextItem(slot, false)
            assert(valid(pawn.EquipedItem) and same(pawn.EquipedItem:GetClass(), class), "host equip failed")
        end
        return ("%08X%08X%08X%08X"):format(guid.A & 0xffffffff, guid.B & 0xffffffff, guid.C & 0xffffffff, guid.D & 0xffffffff)
    end
    if action == "equip" then
        local slot = assert(math.tointeger(tonumber(arg)))
        local row = pawn.Save.PlayerEquipment[slot + 1]
        if not row or not same(row[item_class], class) then return "waiting" end
        pawn:Equip_NextItem(slot, false)
        assert(valid(pawn.EquipedItem) and same(pawn.EquipedItem:GetClass(), class), "guest equip failed")
        return "ready"
    end
    assert(action == "press" or action == "release", "unknown throw action")
    local held = pawn.EquipedItem
    assert(valid(held) and same(held:GetClass(), class), "the stock item is no longer held")
    if action == "press" then face_open_space(pawn, held, kind) end
    held:Use_Item_Primary(action == "press", 1.0)
    return action
end

LoopInGameThreadWithDelay(25, function()
    if valid(watch_pawn) and short_bolt_samples == 0 then
        local class = StaticFindObject("/Script/Engine.StaticMeshComponent")
        for _, component in ipairs(watch_pawn:K2_GetComponentsByClass(class) or {}) do
            if component and type(component.type) == "function" and component:type() == "RemoteUnrealParam" then
                component = component:get()
            end
            local mesh = valid(component) and component.StaticMesh
            if valid(mesh) and component.bAbsoluteLocation and component:IsVisible() and not component.bHiddenInGame
                and mesh:GetFullName() == "StaticMesh /Game/Assets/StaticMeshes/Mesh_Misc/SM_Bolt.SM_Bolt" then
                short_bolt_samples = short_bolt_samples + 1
                break
            end
        end
    end
    for _, shot in pairs(shots) do
        if valid(shot.actor) and not shot.actor:IsActorBeingDestroyed() and valid(shot.actor.RootComponent) then
            local point = shot.actor:K2_GetActorLocation()
            shot.peak = math.max(shot.peak, math.sqrt((point.X - shot.x)^2 + (point.Y - shot.y)^2 + (point.Z - shot.z)^2))
            local mesh = shot.actor.DisplayMesh
            if valid(mesh) and valid(mesh.StaticMesh) and mesh:IsVisible() and not mesh.bHiddenInGame then
                shot.visible = shot.visible + 1
            end
        end
    end
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local data = request:read(128)
    request:close()
    if not data then return end
    local ticket, action, kind, arg = data:match("^(%x+)\t([a-z]+)\t([a-z]+)\t?(%w*)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, result = pcall(run, action, kind, arg)
    local response = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(result):gsub("[\r\n\t]", " ")
    response:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    response:close()
end)
