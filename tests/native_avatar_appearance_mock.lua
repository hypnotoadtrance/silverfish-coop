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
print("Appearance sampling reuses unchanged values and reports live mesh, color and eye changes")
