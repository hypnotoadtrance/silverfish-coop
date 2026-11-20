local drop_items = {}

drop_items.ammo = "/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
drop_items.pistol = "/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C"
drop_items.profiles = {
    [drop_items.ammo] = {actor = "Ammo_Pistol_BP_C", label = "9mm Ball", maximum = 200},
    [drop_items.pistol] = {
        actor = "Wep_LightPistol_C", label = "Light Pistol", maximum = 1,
    },
}

function drop_items.actor_path(class, area, leaf, name)
    local profile = drop_items.profiles[class]
    if not profile or type(name) ~= "string" then return end
    local prefix = profile.actor .. " " .. area .. "." .. leaf .. ":PersistentLevel." .. profile.actor .. "_"
    if name:sub(1, #prefix) == prefix and name:sub(#prefix + 1):match("^%d+$") then
        return name:sub(#profile.actor + 2)
    end
end

return drop_items
