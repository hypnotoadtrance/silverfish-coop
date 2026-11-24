local storms = "/Game/Classes/Weather/Storms/"
local particles = "/Game/Assets/PFX/"
local sounds = "/Game/Assets/Sounds/WAVs/"
local materials = "/Game/Assets/Materials/Mat_Functions/"

local function asset(directory, name)
    return directory .. name .. "." .. name
end

local wind = asset(sounds .. "Ambiant_Wav/Exterior_Amb/", "Wind_Strong_01")
return {
    [asset(storms, "StormMaster_BP") .. "_C"] = {
        particle = asset(particles, "NI_AnomStorm_01"), sound = wind, fog = asset(materials, "Mat_CustomFog"),
    },
    [asset(storms, "LightningStorm_BP") .. "_C"] = {
        particle = asset(particles, "NI_LightningStorm"), sound = wind,
    },
    [asset(storms, "TheFog_BP") .. "_C"] = {},
    [asset(storms, "EatherFlare_BP") .. "_C"] = {
        sound = wind, fog = asset(materials, "Mat_FlareFog"),
    },
    [asset(storms, "NightLightStorm_BP") .. "_C"] = {
        sound = asset(sounds .. "Ambiant_Wav/", "Ambience_Psychedelic_Loop_002"), pitch = .6,
    },
    [asset(storms, "PestilenceStorm_BP") .. "_C"] = {
        sound = asset(sounds .. "Anomaly_Wav/", "578267__brunoauzet__black-bees-swarm"),
        fog = asset(materials, "Mat_PestilenceFog"),
    },
}
