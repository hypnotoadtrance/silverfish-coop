local reload_input = {}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_RELOAD_INPUT") == "1"
local fixtures = enabled and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_RELOAD_FIXTURES") == "1"
local fixture_proof

function reload_input.command(fields, state, phase)
    if fields[2] ~= "native-guest-reload-input-fixture" then return end
    fixture_proof = nil
    local detail = state.local_pawn
    local pawn = detail and detail.pawn
    if not fixtures or phase ~= "connected" or not state.listening or state.server ~= true or #fields ~= 3
        or not detail or type(detail.name) ~= "string" or fields[3] ~= detail.name or not pawn or not pawn:IsValid()
        or pawn:GetFullName() ~= detail.name or type(SilverfishProbeNativeGuestReloadInput) ~= "function" then
        error("The stock reload fixture requires the exact staged headless client pawn", 0)
    end
    local ok, result, sequence, wrappers = SilverfishProbeNativeGuestReloadInput(pawn:GetAddress())
    if ok ~= true or result ~= "stock_pressed" or not math.tointeger(sequence) or sequence < 1 or sequence >= 2 ^ 53
        or wrappers ~= 2 then
        error("The stock reload fixture did not verify (" .. tostring(result) .. ")", 0)
    end
    fixture_proof = { map = state.map, pawn = detail.name, sequence = sequence, wrappers = wrappers }
    return "stock guest reload input verified"
end

function reload_input.rows(state, phase, status_row)
    if not enabled or phase ~= "connected" or not state.listening or state.server ~= true
        or type(SilverfishReadNativeGuestReloadIntent) ~= "function" then return {} end
    local local_pawn = state.local_pawn
    local pawn = local_pawn and local_pawn.pawn
    if not pawn or not pawn:IsValid() or type(local_pawn.name) ~= "string" or pawn:GetFullName() ~= local_pawn.name then
        return {}
    end
    local sequence, address, guid, barrel, ammo = SilverfishReadNativeGuestReloadIntent()
    if type(sequence) ~= "number" or not math.tointeger(sequence) or sequence < 1 or sequence >= 2 ^ 53
        or address ~= pawn:GetAddress() or type(guid) ~= "string" or #guid ~= 32
        or (barrel ~= "primary" and barrel ~= "loki") then return {} end
    local option = ammo and {ammo} or {}
    local rows = {status_row("native-guest-reload-intent", state.map, local_pawn.name, sequence, guid, barrel, table.unpack(option))}
    if fixture_proof and fixture_proof.map == state.map and fixture_proof.pawn == local_pawn.name
        and fixture_proof.sequence == sequence then
        rows[#rows + 1] = status_row("native-guest-reload-fixture", state.map, local_pawn.name, sequence,
            fixture_proof.wrappers)
    end
    return rows
end

function reload_input.reset()
    fixture_proof = nil
    if enabled and type(SilverfishClearNativeGuestReloadIntent) == "function" then
        SilverfishClearNativeGuestReloadIntent()
    end
end

return reload_input
