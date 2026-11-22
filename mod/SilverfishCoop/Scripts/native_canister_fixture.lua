local fixture = {}
local host_save = require("native_host_save")
local host_enabled = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local pistol_path = "/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C"
local inventory_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local system_path = "/Script/Engine.Default__KismetSystemLibrary"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local blank = {R = 0, G = 0, B = 0, A = 0}

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function pistol_class()
    local class = StaticFindObject(pistol_path)
    if not valid(class) then LoadAsset(pistol_path); class = StaticFindObject(pistol_path) end
    return class
end

local function equipped(pawn, class)
    local weapon = pawn.EquipedItem
    if valid(weapon) and weapon:HasAuthority() and same(weapon:GetClass(), class)
        and same(weapon:GetOwner(), pawn) and same(weapon.User, pawn) and weapon.HeldByPlayer == true then
        return weapon
    end
end

local function prepare(state, pawn, class)
    local instance, canonical = host_save.canonical_host(state, "hosting")
    local factory = StaticFindObject(inventory_path)
    if not valid(factory) or not valid(instance) or not same(canonical, pawn) then
        error("Stock host pistol fixture has no isolated stock loadout", 0)
    end
    if equipped(pawn, class) then return end
    local result = {}
    factory:MakeDefaultItem(class, pawn, result)
    local item = result.Item or result
    if not item or not same(item[item_class], class) then error("The stock pistol item could not be created", 0) end
    item[item_ammo] = 7
    local accepted = {}
    instance:EquipItemToLoadout(item, accepted)
    if not (accepted.EquippedSuccessfully or accepted[1]) or not equipped(pawn, class) then
        error("The stock pistol could not be equipped in the isolated campaign", 0)
    end
end

local function shoot(state, pawn, canister, class)
    local weapon = equipped(pawn, class)
    local muzzle, component = valid(weapon) and weapon.Arrow_Muzzle, canister.PropMesh
    local trace = StaticFindObject(system_path)
    if not valid(muzzle) or not same(muzzle:GetOwner(), weapon) or not valid(component) or not valid(trace)
        or type(weapon.Ammo) ~= "number" or weapon.Ammo < 1 then error("The stock loaded pistol muzzle is unavailable", 0) end
    local center = component:GetCenterOfMass(FName("None"))
    local original, view = pawn:K2_GetActorLocation(), state.controller:GetControlRotation()
    local aim, before_muzzle
    for _, angle in ipairs({0, 45, 90, 135, 180, 225, 270, 315}) do
        local radians = math.rad(angle)
        local x, y = center.X + 260 * math.cos(radians), center.Y + 260 * math.sin(radians)
        local yaw = math.deg(math.atan(center.Y - y, center.X - x))
        if pawn:K2_TeleportTo({X = x, Y = y, Z = center.Z + 65}, {Pitch = 0, Yaw = yaw, Roll = 0}) then
            state.controller:SetControlRotation({Pitch = 0, Yaw = yaw, Roll = 0})
            before_muzzle = muzzle:K2_GetComponentRotation()
            local origin = muzzle:K2_GetComponentLocation()
            local dx, dy, dz = center.X - origin.X, center.Y - origin.Y, center.Z - origin.Z
            local horizontal = math.sqrt(dx * dx + dy * dy)
            local hit = {}
            if horizontal > 100 and trace:LineTraceSingle(pawn, origin, center, 0, false, {pawn, weapon},
                0, hit, true, blank, blank, 0) then
                hit = hit.OutHit or hit
                if hit.Component and same(hit.Component:Get(), component) then
                    aim = {Pitch = math.deg(math.atan(dz, horizontal)), Yaw = math.deg(math.atan(dy, dx)), Roll = 0}
                    break
                end
            end
        end
    end
    if not aim then
        pawn:K2_TeleportTo(original, view)
        state.controller:SetControlRotation(view)
        error("The isolated host had no stock shot lane to the canister", 0)
    end
    local before = weapon.Ammo
    local success, failure = pcall(function()
        muzzle:K2_SetWorldRotation(aim, false, {}, true)
        weapon:FireWeapon()
    end)
    muzzle:K2_SetWorldRotation(before_muzzle, false, {}, true)
    if not success then error(failure, 0) end
    if weapon.Ammo ~= before - 1 then error("The stock canister shot did not spend one round", 0) end
    return "Fired one stock pistol round toward the canister"
end

function fixture.command(args, state, phase, canister)
    local action = args[2]
    if action ~= "native-canister-prepare-fixture" and action ~= "native-canister-shoot-fixture"
        and action ~= "native-canister-guest-guard-fixture" and action ~= "native-canister-ignite-fixture" then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if #args ~= 3 or not valid(pawn) or not pawn:IsLocallyControlled() or not valid(canister) then
        error("The exact stock canister fixture is unavailable", 0)
    end
    if action == "native-canister-guest-guard-fixture" and phase == "connected" then
        canister:SetOnFire(5, 1, pawn)
        canister:PropBroken(canister:K2_GetActorLocation(), pawn)
        return "Invoked the stock canister ignition and rupture calls on the guest"
    end
    if not host_enabled or phase ~= "hosting" or not canister:HasAuthority() then
        error("The stock canister shot requires the isolated fixture host", 0)
    end
    if action == "native-canister-ignite-fixture" then
        canister:SetOnFire(5, 1, pawn)
        return "Invoked the stock canister ignition on the host"
    end
    local class = pistol_class()
    if action == "native-canister-prepare-fixture" then
        prepare(state, pawn, class)
        return "Stock isolated host pistol equipped"
    end
    return shoot(state, pawn, canister, class)
end

return fixture
