local drop_items = {}

drop_items.ammo = "/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
drop_items.pistol = "/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C"
drop_items.profiles = {
    [drop_items.ammo] = {actor = "Ammo_Pistol_BP_C", label = "9mm Ball", maximum = 200, stackable = true},
    ["/Game/Classes/Items/Ammo_Magnum_BP.Ammo_Magnum_BP_C"] = {
        actor = "Ammo_Magnum_BP_C", label = ".45 Ball", maximum = 200, stackable = true,
    },
    ["/Game/Classes/Items/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C"] = {
        actor = "Ammo_ShotgunShells_BP_C", label = "12-gauge buckshot", maximum = 200, stackable = true,
    },
    ["/Game/Classes/Items/Ammo_5-7_AP_BP.Ammo_5-7_AP_BP_C"] = {
        actor = "Ammo_5-7_AP_BP_C", label = "5.7 AP", maximum = 200, stackable = true,
    },
    [drop_items.pistol] = {
        actor = "Wep_LightPistol_C", label = "Light Pistol", maximum = 1,
    },
}

for blueprint, label in pairs({
    Item_MRE = "MRE",
    Item_CannedPeaches = "Canned Peaches",
    Item_CannedBeans = "Canned Beans",
    Item_BrownMushroom = "Brown Mushroom",
}) do
    local actor = blueprint .. "_C"
    local class = "/Game/Classes/Items/" .. blueprint .. "." .. actor
    drop_items.profiles[class] = { actor = actor, label = label, maximum = 200, stackable = true }
end

function drop_items.actor_path(class, area, leaf, name)
    local profile = drop_items.profiles[class]
    if not profile or type(name) ~= "string" then return end
    local prefix = profile.actor .. " " .. area .. "." .. leaf .. ":PersistentLevel." .. profile.actor .. "_"
    if name:sub(1, #prefix) == prefix and name:sub(#prefix + 1):match("^%d+$") then
        return name:sub(#profile.actor + 2)
    end
end

return drop_items
