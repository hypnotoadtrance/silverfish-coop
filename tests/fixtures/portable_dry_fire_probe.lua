local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-dry-fire-"
local map = "World /Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local click = "SoundCue /Game/Assets/Sounds/CUEs/Weapon_Cues/Reload_Cues/TriggerClick_Cue.TriggerClick_Cue"
local factory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local reserve_9mm = "/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
local arm_profiles = {
    arm = {"/Game/Classes/Weapons/Wep_Pistol.Wep_Pistol_C", reserve_9mm, 9, 2},
    ["arm-rusty"] = {"/Game/Classes/Weapons/Wep_RustyPistol.Wep_RustyPistol_C", reserve_9mm, 9, 2},
    ["arm-light-ar"] = {"/Game/Classes/Weapons/Wep_LightAR_01.Wep_LightAR_01_C",
        "/Game/Classes/Items/Ammo_IntrRifle_BP.Ammo_IntrRifle_BP_C", 7, 3},
    ["arm-nagant"] = {"/Game/Classes/Weapons/Wep_Nagant_01.Wep_Nagant_01_C", reserve_9mm, 0, 6},
    ["arm-obrez"] = {"/Game/Classes/Weapons/Wep_Obrez_01.Wep_Obrez_01_C", reserve_9mm, 0, 7},
}
local weapon_path, reserve_path, reserve_units, slot
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local payload = "DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"
local skeletal_tail = "/Game/Classes/NPCs/NPC_Assets/NPC_Tails/SK_Tail_01.SK_Tail_01"
local last, local_pawn, peer, failure, world_address
local hooked = false
local clicks, projectiles, shot_sounds, muzzle_flashes = {}, 0, 0, 0
local local_flashes, local_reload = 0, nil
local retire_projectile, projectile_retired

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function observing()
    if not world_address then return false end
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    return valid(world) and world:GetAddress() == world_address and valid(local_pawn) and valid(peer)
        and not local_pawn:IsActorBeingDestroyed() and not peer:IsActorBeingDestroyed()
end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path); object = StaticFindObject(path) end
    assert(valid(object), "unavailable stock asset " .. path)
    return object
end

local function row_identity(row)
    local guid = row[item_guid]
    return ("%08X%08X%08X%08X"):format(guid.A & 0xffffffff, guid.B & 0xffffffff, guid.C & 0xffffffff, guid.D & 0xffffffff)
end

local function identify(row)
    return same(row[item_class], asset(weapon_path)) and row_identity(row) or "-"
end

local function peer_muzzle(component)
    local weapon = peer.EquipedItem
    if valid(weapon) and same(component, weapon.Arrow_Muzzle) then return weapon end
end

local function arm()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == map, "stock Observatory unavailable")
    local statics = asset("/Script/Engine.Default__GameplayStatics")
    local_pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(local_pawn) and local_pawn:IsLocallyControlled() and same(local_pawn:GetWorld(), world), "expected local stock pawn")
    peer = nil
    for _, pawn in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(pawn) and same(pawn:GetWorld(), world) and not pawn:IsActorBeingDestroyed() and not pawn:IsLocallyControlled() then
            assert(not peer, "duplicate remote stock pawn")
            peer = pawn
        end
    end
    assert(valid(peer), "expected a remote stock pawn")
    world_address = world:GetAddress()
    if hooked then return "armed" end
    RegisterHook("/Script/Engine.GameplayStatics:PlaySoundAtLocation", function(_, context, sound, location)
        if not observing() then return end
        local ok, message = pcall(function()
            local cue = sound:get()
            if not valid(cue) or cue:GetFullName() ~= click then return end
            local source, point = context:get(), location:get()
            local original = same(source, local_pawn.EquipedItem)
            if not original and not same(source, local_pawn) then return end
            clicks[#clicks + 1] = ("%s,%.3f,%.3f,%.3f"):format(original and "stock-weapon" or "observer", point.X, point.Y, point.Z)
        end)
        if not ok then failure = message end
    end)
    RegisterBeginPlayPostHook(function(context)
        if not retire_projectile or not observing() then return end
        local actor = context:get()
        if not valid(actor) or not actor:IsA(retire_projectile) or not actor:HasAuthority()
            or not same(actor:GetWorld(), local_pawn:GetWorld())
            or not same(actor:GetOwner(), peer) or not same(actor.Instigator, peer) then return end
        projectile_retired = {address = actor:GetAddress(), class = actor:GetClass()}
        retire_projectile = nil
        actor:K2_DestroyActor()
    end)
    RegisterHook("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:SpawnProjectile", function(context)
        if not observing() then return end
        local weapon = context:get()
        if same(weapon, local_pawn.EquipedItem) or same(weapon, peer.EquipedItem) then projectiles = projectiles + 1 end
    end)
    RegisterHook("/Script/Engine.GameplayStatics:SpawnSoundAttached", function(_, sound, attached)
        if not observing() then return end
        local weapon = peer_muzzle(attached:get())
        if weapon and same(sound:get(), weapon.FireSound) then shot_sounds = shot_sounds + 1 end
    end)
    RegisterHook("/Script/Niagara.NiagaraFunctionLibrary:SpawnSystemAttached", function(_, effect, attached, _, location)
        if not observing() then return end
        local weapon = peer_muzzle(attached:get())
        if weapon and same(effect:get(), weapon.MuzzleFlash) then muzzle_flashes = muzzle_flashes + 1 end
        local held = local_pawn.EquipedItem
        if not local_pawn:HasAuthority() and valid(held)
            and same(attached:get(), held.Arrow_Muzzle) and same(effect:get(), held.MuzzleFlash) then
            local offset = location:get()
            if offset.X == 0 and offset.Y == 0 and offset.Z == 0 then local_flashes = local_flashes + 1 end
        end
    end)
    RegisterHook("/Script/Engine.SkeletalMeshComponent:PlayAnimation", function(context, animation, looping)
        if not observing() or local_pawn:HasAuthority() or looping:get() then return end
        local held, clip = local_pawn.EquipedItem, animation:get()
        if valid(held) and same(context:get(), held.Wep_Skeli)
            and (same(clip, held.Reload_Dry_Anim) or same(clip, held.Reload_Wet_Anim)) then
            local_reload = clip
        end
    end)
    hooked = true
    return "armed"
end

local function seed(pawn, rounds)
    local save = pawn.Save
    assert(pawn:HasAuthority() and valid(save) and not valid(save.PlayerEquipment[slot][item_class]), "fixture requires an empty authoritative slot")
    assert(same(local_pawn.Save, local_pawn:GetWorld().OwningGameInstance.SaveSlot_Ref)
        and (same(pawn, local_pawn) or not same(save, local_pawn.Save)), "fixture must use the co-op save and detached peer")
    local output = {}
    local defaults = asset(weapon_path):GetCDO()
    asset(factory):ItemActorToStruct(defaults, pawn, output)
    local row = output.Item or output
    row[ammo] = rounds
    local guid = identify(row)
    assert(#guid == 32 and guid ~= string.rep("0", 32), "stock weapon did not receive a GUID")
    save.PlayerEquipment[slot] = row
    -- A reflected out table omits native InstancedStruct storage. Give the real
    -- inventory row the owning class's stock data through an Unreal struct copy.
    assert(type(defaults.DataStruct) == "userdata", "stock weapon payload is not backed by Unreal storage")
    save.PlayerEquipment[slot][payload] = defaults.DataStruct
    return guid
end

local function ready(pawn)
    local row = pawn.Save.PlayerEquipment[slot]
    local weapon = pawn.EquipedItem
    if identify(row) == "-" or not valid(weapon) or not same(weapon:GetClass(), asset(weapon_path)) then return "waiting" end
    assert(same(weapon.User, pawn) and pawn.Save.PlayerEquipIndex == slot - 1, "expected the stock weapon in the selected slot")
    return ("%s,%d,%d"):format(identify(row), weapon.Ammo, row[ammo])
end

local function read_reserve(pawn)
    local pack, rounds, rows = pawn.Save.PlayerBackpack, asset(reserve_path), {}
    for index = 1, pack:GetArrayNum() do
        local row = pack[index]
        if same(row[item_class], rounds) then rows[#rows + 1] = ("%s,%d"):format(row_identity(row), row[ammo]) end
    end
    return table.concat(rows, "|")
end

local function seed_reserve(extra)
    assert(local_pawn:HasAuthority() and peer:HasAuthority() and valid(peer.Save) and not same(local_pawn.Save, peer.Save),
        "stock reload requires a detached authoritative guest inventory")
    assert(read_reserve(peer) == "", "fixture expected no preexisting matching reserve")
    local pack, output = peer.Save.PlayerBackpack, {}
    asset(factory):ItemActorToStruct(asset(reserve_path):GetCDO(), peer, output)
    local row = output.Item or output
    assert(same(row[item_class], asset(reserve_path)) and row[ammo] == reserve_units, "stock reserve did not match its profile")
    if extra then
        assert(weapon_path == arm_profiles["arm-rusty"][1] and reserve_units == 9,
            "the pursuit reserve requires the stock Rusty Pistol")
        row[ammo] = 16
    end
    pack[pack:GetArrayNum() + 1] = row
    return read_reserve(peer)
end

local function meshes(actor)
    local items = {}
    local static = asset("/Script/Engine.StaticMeshComponent")
    local skeletal = asset("/Script/Engine.SkeletalMeshComponent")
    for _, part in ipairs(actor:K2_GetComponentsByClass(asset("/Script/Engine.MeshComponent"))) do
        if type(part) == "userdata" and part:type() == "RemoteUnrealParam" then part = part:get() end
        if valid(part) and part:IsVisible() and not part.bHiddenInGame then
            local mesh = part:IsA(static) and part.StaticMesh or part:IsA(skeletal) and part.SkinnedAsset
            if valid(mesh) then items[#items + 1] = {component = part, path = mesh:GetFullName()} end
        end
    end
    return items
end

local function rifle_grip(argument)
    assert(observing())
    local weapon = peer.EquipedItem
    local hand_socket = FName("Weapon_Socket01", EFindName.FNAME_Find)
    local left = FName("LeftHand", EFindName.FNAME_Find)
    local index = FName("LeftHandIndex1", EFindName.FNAME_Find)
    local math_api = asset("/Script/Engine.Default__KismetMathLibrary")
    local stock_mesh, grip
    if argument and argument ~= "" then
        local path, x, y, z = argument:match("^(%a+ /Game/[%w_./%-]+);([%-%.%d]+),([%-%.%d]+),([%-%.%d]+)$")
        assert(path and tonumber(x) and tonumber(y) and tonumber(z))
        stock_mesh, grip = path, {X = tonumber(x), Y = tonumber(y), Z = tonumber(z)}
    else
        if not valid(weapon) or not same(weapon:GetClass(), asset(weapon_path)) then return "waiting:weapon" end
        local frame, root, marker = weapon.Mesh_Frame, weapon.RootComponent, weapon.Arrow_Forgrip
        if not valid(frame) or not valid(root) or not valid(marker) or not valid(frame.StaticMesh) then return "waiting:marker" end
        stock_mesh = frame.StaticMesh:GetFullName()
        grip = math_api:InverseTransformLocation(root:K2_GetComponentToWorld(), marker:K2_GetComponentLocation())
    end
    local witness = "waiting:frame"
    for _, item in ipairs(meshes(peer)) do
        if item.path == stock_mesh then
            local part = item.component
            local anchor = part:GetAttachParent()
            local pivot = valid(anchor) and anchor:GetAttachParent()
            for _ = 1, 3 do
                if not valid(pivot) or pivot:GetAttachSocketName():ToString() == "Weapon_Socket01" then break end
                anchor, pivot = pivot, pivot:GetAttachParent()
            end
            witness = "waiting:foregrip-pivot"
            if valid(pivot) and pivot:GetAttachSocketName():ToString() == "Weapon_Socket01" then
                local body = pivot:GetAttachParent()
                local instance = valid(body) and body.AnimScriptInstance
                local clip = valid(instance) and instance.CurrentAsset
                witness = "waiting:" .. (valid(clip) and clip:GetFullName() or "animation")
                if valid(clip) and clip:GetFullName():find("Rifle_Lowered_BlendSpace", 1, true) then
                    local wrist, knuckle = body:GetSocketLocation(left), body:GetSocketLocation(index)
                    local palm = {X = (wrist.X + knuckle.X) * .5, Y = (wrist.Y + knuckle.Y) * .5,
                        Z = (wrist.Z + knuckle.Z) * .5}
                    local baseline = math_api:TransformLocation(body:GetSocketTransform(hand_socket, 0), grip)
                    local actual = math_api:TransformLocation(anchor:K2_GetComponentToWorld(), grip)
                    local function distance(a, b)
                        return math.sqrt((a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2)
                    end
                    local angle = pivot.RelativeRotation
                    local from_pivot = math_api:TransformLocation(pivot:K2_GetComponentToWorld(), grip)
                    local from_root = math_api:InverseTransformLocation(body:GetSocketTransform(hand_socket, 0), palm)
                    return ("%.3f,%.3f,%.3f;%.3f,%.3f,%.3f;%.3f,%.3f,%.3f;%.3f,%.3f,%.3f"):format(
                        distance(baseline, palm), distance(actual, palm), distance(from_pivot, palm),
                        angle.Pitch, angle.Yaw, angle.Roll, grip.X, grip.Y, grip.Z, from_root.X, from_root.Y, from_root.Z)
                end
            end
        end
    end
    return witness
end

local function run(action, argument)
    if arm_profiles[action] then
        weapon_path, reserve_path, reserve_units, slot = table.unpack(arm_profiles[action])
        return arm()
    end
    if action == "disarm" then
        world_address, local_pawn, peer, local_reload = nil, nil, nil, nil
        retire_projectile, projectile_retired = nil, nil
        return "disarmed"
    end
    if action == "retire-peer-projectile" then
        assert(observing() and local_pawn:HasAuthority() and peer:HasAuthority())
        retire_projectile = asset("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C")
        projectile_retired = nil
        return "retirement-armed"
    end
    if action == "retirement-read" then
        if not projectile_retired then return "no birth captured" end
        local active = {}
        asset("/Script/Engine.Default__GameplayStatics"):GetAllActorsOfClass(
            local_pawn:GetWorld(), projectile_retired.class, active)
        for _, actor in ipairs(active) do
            if valid(actor) and actor:GetAddress() == projectile_retired.address then return "still in world" end
        end
        return "retired"
    end
    if action == "seed-local" then return seed(local_pawn, 0) end
    if action == "seed-peer" then return seed(peer, 1) end
    if action == "projected" then return identify(local_pawn.Save.PlayerEquipment[slot]) end
    if action == "equip" then
        assert(identify(local_pawn.Save.PlayerEquipment[slot]) ~= "-", "stock weapon is not in the projected slot")
        local_pawn:Equip_NextItem(slot - 1, false)
        return "selected"
    end
    if action == "ready" then return ready(local_pawn) end
    if action == "peer-ready" then return ready(peer) end
    if action == "rifle-grip" then return rifle_grip(argument) end
    if action == "rifle-witness" then
        assert(observing() and ready(local_pawn) ~= "waiting")
        local weapon = local_pawn.EquipedItem
        local math_api = asset("/Script/Engine.Default__KismetMathLibrary")
        local grip = math_api:InverseTransformLocation(weapon.RootComponent:K2_GetComponentToWorld(),
            weapon.Arrow_Forgrip:K2_GetComponentLocation())
        local parts = meshes(weapon)
        assert(parts[1], "the rifle has no visible stock mesh")
        return ("%s;%.4f,%.4f,%.4f"):format(parts[1].path, grip.X, grip.Y, grip.Z)
    end
    if action == "host-tail" then
        assert(observing() and local_pawn:HasAuthority()
            and same(local_pawn.Save, local_pawn:GetWorld().OwningGameInstance.SaveSlot_Ref))
        local appearance = local_pawn.Save.Character
        appearance.Mesh_Tail_SM_74_4FD875FB4D8D71A94E98888810B6EA64 = nil
        appearance.Mesh_Tail_SK_77_F2B7A1A441682020D88D3AA95D84A47E = asset(skeletal_tail)
        return "styled"
    end
    if action == "tail-witness" then
        assert(observing())
        local socket, root = FName("TailSocket_01", EFindName.FNAME_Find), FName("Tail_01", EFindName.FNAME_Find)
        for _, part in ipairs(peer:K2_GetComponentsByClass(asset("/Script/Engine.SkeletalMeshComponent"))) do
            if type(part) == "userdata" and part:type() == "RemoteUnrealParam" then part = part:get() end
            if same(part.SkinnedAsset, asset(skeletal_tail)) and part:GetAttachSocketName():ToString() == "TailSocket_01"
                and part:GetBoneIndex(root) >= 0 and part.RelativeRotation.Yaw < -9 then
                local source = part:GetSocketLocation(root)
                local target = part:GetAttachParent():GetSocketLocation(socket)
                return ("%.4f"):format(math.sqrt((source.X-target.X)^2 + (source.Y-target.Y)^2 + (source.Z-target.Z)^2))
            end
        end
        return "waiting"
    end
    if action == "stand" then
        local_pawn:UnCrouch(false)
        return "standing"
    end
    if action == "aim" then
        assert(observing() and not local_pawn:HasAuthority() and ready(local_pawn) ~= "waiting")
        local input = asset("/Game/Classes/Player/Player_Inputs/IA_Use_Secondary.IA_Use_Secondary")
        local value = asset("/Script/EnhancedInput.Default__EnhancedInputLibrary"):MakeInputActionValueOfType(1, 0, 0, 0)
        local_pawn:InpActEvt_IA_Use_Secondary_K2Node_EnhancedInputActionEvent_33(value, 0, 0, input)
        return local_pawn.Aiming and "aiming" or "waiting"
    end
    if action == "light-ar-read-mode" then
        local held = local_pawn.EquipedItem
        if not valid(held) or not same(held:GetClass(), asset(weapon_path)) then return "waiting" end
        return held.SemiAutoSelected and "semi" or "auto"
    end
    if action == "put-away" or action == "other-ready" then
        local other_slot = arm_profiles["arm-rusty"][4]
        local row = local_pawn.Save.PlayerEquipment[other_slot]
        local class = asset(arm_profiles["arm-rusty"][1])
        assert(observing() and not local_pawn:HasAuthority() and same(row[item_class], class),
            "the paired Rusty Pistol slot is required")
        if action == "put-away" then
            local_pawn:Equip_NextItem(other_slot - 1, false)
            return "changing"
        end
        local held = local_pawn.EquipedItem
        if local_pawn.Save.PlayerEquipIndex == other_slot - 1 and valid(held) and same(held:GetClass(), class)
            and same(held:GetOwner(), local_pawn) then return "other" end
        return "waiting"
    end
    if action == "light-ar-semi" or action == "light-ar-auto" then
        assert(observing() and not local_pawn:HasAuthority() and weapon_path == arm_profiles["arm-light-ar"][1]
            and ready(local_pawn) ~= "waiting", "the guest LightAR must be selected")
        local held = local_pawn.EquipedItem
        assert(same(held:GetOwner(), local_pawn) and held.HasSelectFire == true
            and type(held.SemiAutoSelected) == "boolean", "the stock LightAR selector is unavailable")
        local semi = action == "light-ar-semi"
        if held.SemiAutoSelected ~= semi then held:ItemFireSelect(true) end
        assert(same(local_pawn.EquipedItem, held) and held.SemiAutoSelected == semi, "LightAR mode did not change")
        return action
    end
    if action == "seed-reserve" or action == "seed-pursuit-reserve" then
        return seed_reserve(action == "seed-pursuit-reserve")
    end
    if action == "reserve" then return read_reserve(local_pawn) end
    if action == "peer-reserve" then return read_reserve(peer) end
    if action == "face-away" then
        local from, other = local_pawn:K2_GetActorLocation(), peer:K2_GetActorLocation()
        local_pawn.Controller:SetControlRotation({Pitch = 35, Yaw = math.deg(math.atan(from.Y - other.Y, from.X - other.X)), Roll = 0})
        return "turned"
    end
    if action == "clear" then
        clicks, projectiles, shot_sounds, muzzle_flashes, failure = {}, 0, 0, 0, nil
        local_flashes, local_reload = 0, nil
        return "clear"
    end
    if action == "shot-read" then return ("%d,%d,%d,%d"):format(projectiles, shot_sounds, muzzle_flashes, #clicks) end
    if action == "first-person-read" then
        local held = local_pawn.EquipedItem
        local rig = valid(held) and held.Wep_Skeli
        local path = valid(local_reload) and local_reload:GetFullName():match("^AnimSequence (.+)$")
        return ("%d;%s;%.3f"):format(local_flashes, path or "-", valid(rig) and rig:GetPosition() or 0)
    end
    if action == "read" then
        assert(not failure, failure)
        return ("%d;%s"):format(projectiles, table.concat(clicks, "|"))
    end
    if action == "host-press" then
        assert(local_pawn:HasAuthority() and ready(local_pawn) ~= "waiting", "only the local host can dispatch its stock trigger")
        local input = asset("/Game/Classes/Player/Player_Inputs/IA_Use_Primary.IA_Use_Primary")
        local library = asset("/Script/EnhancedInput.Default__EnhancedInputLibrary")
        local started = library:MakeInputActionValueOfType(1, 0, 0, 0)
        local_pawn:InpActEvt_IA_Use_Primary_K2Node_EnhancedInputActionEvent_36(started, 0, 0, input)
        local completed = library:MakeInputActionValueOfType(0, 0, 0, 0)
        local_pawn:InpActEvt_IA_Use_Primary_K2Node_EnhancedInputActionEvent_37(completed, .05, .05, input)
        return "pressed"
    end
    error("unknown dry-fire probe action")
end

LoopInGameThreadWithDelay(40, function()
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local data = request:read(384)
    request:close()
    if not data then return end
    local ticket, action, argument = data:match("^(%x+)\t([a-z%-]+)\t?(.*)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = pcall(run, action, argument)
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[\r\n\t]", " ")
    output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    output:close()
end)
