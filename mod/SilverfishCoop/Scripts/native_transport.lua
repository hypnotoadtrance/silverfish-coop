local transport = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_TRANSPORT") == "1"
local scoped = false
local artifacts = {
    stardust = "Art_DreamDust",
    green = "Art_Tesseract_Green",
    blue = "Art_Tesseract_Blue",
    red = "Art_Tesseract_Red",
    gold = "Art_Tesseract_Gold",
}
local manager_path = "/Game/Classes/GUI/New/Components/ItemManager.ItemManager_C"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"

local function valid(value)
    return value ~= nil and value:IsValid()
end

local function load_class(path)
    local class = StaticFindObject(path)
    if not valid(class) then
        LoadAsset(path)
        class = StaticFindObject(path)
    end
    if not valid(class) then error("The staged transport class could not be loaded", 0) end
    return class
end

function transport.sync(state, phase)
    if not enabled or type(SilverfishSetNativeTransportScope) ~= "function" then return end
    local local_player = state.local_pawn and state.local_pawn.pawn
    local host = phase == "hosting"
    if (host or phase == "connected") and local_player and local_player:IsValid() then
        scoped = SilverfishSetNativeTransportScope(local_player:GetAddress(), host) == true
    elseif scoped then
        SilverfishClearNativeTransportScope()
        scoped = false
    end
end

function transport.rows(_, _, status_row)
    if not enabled then return {} end
    if type(SilverfishReadNativeTransportStatus) ~= "function" then
        return {status_row("native-transport", "arming", 0, "", "", "", "")}
    end
    local ready, denied, role, item, entry, reason = SilverfishReadNativeTransportStatus()
    return {status_row("native-transport", ready and "ready" or "arming", denied or 0, role or "", item or "",
        entry or "", reason or "")}
end

function transport.command(fields, state, phase)
    if fields[2] ~= "native-transport-probe" then return end
    local stem = artifacts[fields[3]]
    if #fields ~= 3 or not enabled or not scoped or not stem
        or type(SilverfishProbeNativeTransportHeldItem) ~= "function" then
        error("An isolated native transport guard is required", 0)
    end
    load_class("/Game/Classes/Items/Artifacts/" .. stem .. "." .. stem .. "_C")
    local widgets = StaticFindObject(widgets_path)
    local details = state.local_pawn
    local pawn = details and details.pawn
    local controller = details and details.controller_object
    if not valid(widgets) or not valid(pawn) or not valid(controller) or not controller:IsLocalController() then
        error("The connected local player is unavailable", 0)
    end
    -- The scratch widget stays offscreen. Its item carries no real stack or GUID.
    local manager = widgets:Create(pawn, load_class(manager_path), controller)
    if not valid(manager) then error("Could not create the isolated stock item manager", 0) end
    manager.Player_FP = pawn
    local _, previous = SilverfishReadNativeTransportStatus()
    local verified, detail = SilverfishProbeNativeTransportHeldItem(manager:GetAddress(), fields[3])
    if not verified then error("Stock transport use probe failed (" .. tostring(detail) .. ")", 0) end
    local ready, count, role, item, entry = SilverfishReadNativeTransportStatus()
    if not ready or count ~= previous + 1 or role ~= (phase == "hosting" and "host" or "guest")
        or item ~= fields[3] or entry ~= "use_held_item" then
        error("The staged transport guard did not confirm the exact local use", 0)
    end
    return "stock transport use stopped before consumption"
end

return transport
