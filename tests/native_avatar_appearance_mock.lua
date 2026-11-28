package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local view = require("native_avatar_character_view")
local head = "Mesh_Head_60_630BB776410363C64BE2C89A3F72B1C5"
local skin = "Color_Skin_53_0B756DAC41D1CF33A2BB2480770F4445"
local eye = "EyeType_83_470E782541AF25166B548AA28A834625"
local prefix = "/Game/Classes/Player/PlayerAssets/HelmetMeshes/"
local name_reads = 0
local function mesh(name)
    return {
        IsValid = function() return true end,
        GetAddress = function(self) return self end,
        GetFullName = function()
            name_reads = name_reads + 1
            return "StaticMesh " .. prefix .. name .. "." .. name
        end,
    }
end

local character = {[head] = mesh("HeadA"), [skin] = {R = .5, G = .3, B = .2, A = 1}, [eye] = 1}
local first, sample = view.observe(character)
assert(first:find("HeadA.HeadA", 1, true) and view.decode(first).colors[1].R == .5)
assert(view.observe(character, sample) == first and name_reads == 1, "unchanged asset names are reused")

character[skin].R = .6
character[eye] = 3
local changed = view.decode(view.observe(character, sample))
assert(changed.colors[1].R == .6 and changed.eye == 3 and name_reads == 1,
    "a live color or eye change needs no asset name lookup")

character[head] = mesh("HeadB")
assert(view.observe(character, sample):find("HeadB.HeadB", 1, true) and name_reads == 2)
character[head], character[skin], character[eye] = nil, nil, 21
assert(view.observe(character, sample) == "-|-|-|-|-|-|-|-|-|-")

EFindName = {FNAME_Find = 0}
FName = function(name) return name end
local direction, updates = {X = 1, Y = 0, Z = 0}, {}
local avatar = {
    head = {GetForwardVector = function() return direction end},
    eye_material = {
        IsValid = function() return true end,
        SetVectorParameterValue = function(_, parameter, value)
            assert(parameter == "LookVector" and value.A == 1)
            updates[#updates + 1] = value
        end,
    },
}
view.update_eye_direction(avatar)
view.update_eye_direction(avatar)
assert(#updates == 1 and updates[1].R == 1 and updates[1].G == 0)
direction = {X = 0, Y = -1, Z = .2}
view.update_eye_direction(avatar)
assert(#updates == 2 and updates[2].R == 0 and updates[2].G == -1 and updates[2].B == .2)
avatar.eye_material = nil
view.update_eye_direction(avatar)
assert(#updates == 2)
print("Appearance sampling reuses unchanged values and reports live mesh, color and eye changes")

package.loaded.guest_save = {}
local player_avatar = require("native_player_avatar")
local weapon_view = require("native_avatar_weapon_view")
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local outfit_root = "/Game/Classes/Armor_Items/"
local body_path = outfit_root .. "TestArmor.TestArmor_C"
local helmet_path = outfit_root .. "TestHelmet.TestHelmet_C"
local worn_reads, weapon_reads = 0, 0
local function stock_worn(path)
    return {[item_class] = {
        IsValid = function() return true end,
        GetFullName = function()
            worn_reads = worn_reads + 1
            return "BlueprintGeneratedClass " .. path
        end,
    }}
end
local save = {Character = character, PlayerArmor = stock_worn(body_path), PlayerHelmet = stock_worn(helmet_path),
    IsValid = function() return true end, GetAddress = function() return 11 end}
local pawn = {Save = save, IsValid = function() return true end, GetAddress = function() return 12 end,
    IsLocallyControlled = function() return true end, HasAuthority = function() return true end,
    PlayerState = {IsValid = function() return true end, GetPlayerId = function() return 1 end}}
local world = {OwningGameInstance = {SaveSlot_Ref = save}, IsValid = function() return true end,
    GetAddress = function() return 13 end}
local details = {pawn = pawn, name = "Player_FP_C host"}
local state = {world = world, map = "test", pawns = {details}, local_pawn = details, guests = {}}
SilverfishReadNativeAvatarMotion = function() return false, false, false end
weapon_view.observe = function()
    weapon_reads = weapon_reads + 1
    return "-"
end
local function sync_outfit()
    player_avatar.sync(state, "hosting")
    local rows = player_avatar.rows(function(...) return {...} end)
    return rows[1]
end
local baseline = sync_outfit()
assert(baseline[4] == body_path and baseline[5] == helmet_path and worn_reads == 2 and weapon_reads == 1)

SilverfishNativeAvatarAppearanceMatches = function(w, p, s, cached, mode)
    assert(w == 13 and p == 12 and s == 11 and cached.model and mode == "outfit")
    return true, body_path, helmet_path, true
end
local native = sync_outfit()
assert(table.concat(native, "|") == table.concat(baseline, "|") and worn_reads == 2 and weapon_reads == 1)

-- A cleared head slot uses "-" while an unavailable body read uses Lua.
save.PlayerHelmet = nil
SilverfishNativeAvatarAppearanceMatches = function() return true, nil, "-", false end
local mixed = sync_outfit()
assert(mixed[4] == body_path and mixed[5] == "-" and worn_reads == 3 and weapon_reads == 2)

SilverfishNativeAvatarAppearanceMatches = function() return nil end
sync_outfit()
assert(worn_reads == 4 and weapon_reads == 3, "declined outfit samples still use stock observation")
