local function station(map, id, prefix, product, x, y, z, title, label)
    local world = map .. "." .. map:match("/([^/]+)$")
    local path = world .. ":PersistentLevel.DataTape_DownloadStation_BP_C_1"
    return {map = map, id = id, prefix = prefix, product = product, world = world, path = path,
        station = "DataTape_DownloadStation_BP_C " .. path, anchor = {X = x, Y = y, Z = z},
        title = title, label = label}
end

local stone = station("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P", "Clt_Tape_StoneMaze_01",
    "native-mosby-tape", "/Game/Classes/Items/DataTapes/DataTape_StoneMaze_01.DataTape_StoneMaze_01_C",
    24873.440989, -11925.632572, 19.047219, "Stone Maze Download Station", "Ground Saturation Data")
local pump = station("/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P", "Clt_EatherPumpTape",
    "native-pump-tape", "/Game/Classes/Items/DataTapes/DataTape_EatherPumps.DataTape_EatherPumps_C",
    6750.0000001046765, 9176.892222051465, -355.465292098281, "Utility Tunnels Download Station", "Eather Pump Data")
local drill = station("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01", "Clt_DrillHead",
    "native-drill-test-results", "/Game/Classes/Items/DataTapes/DataTape_DrillTestResults.DataTape_DrillTestResults_C",
    7250, 2150, -300, "Scientist Download Station", "Drill Test Results")
drill.allows_host = true

return {stone = stone, pump = pump, drill = drill, maps = {[stone.map] = stone, [pump.map] = pump, [drill.map] = drill}}
