local stack = {}

local function guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]")
end

function stack.merge(pawn, target, canonical, escrow, source_guid)
    local merge = SilverfishMergeNativeGroundStack
    if type(merge) ~= "function" or not canonical or not canonical:IsValid() then return "append" end
    local called, ok, status, amount, before, after, destination = pcall(merge,
        pawn:GetAddress(), target:GetAddress(), canonical:GetAddress(), escrow:GetAddress(), source_guid)
    if not called or type(ok) ~= "boolean" or type(status) ~= "string" or #status > 100
        or not status:match("^[a-z0-9_]+$") then
        return "mutation_unverified_ground_stack_result"
    end
    if (ok and status == "append") or (not ok and status == "native_loot_exact_claimant_unavailable") then
        return "append"
    end
    if not ok and status ~= "merged" and status ~= "append" then return status end
    if ok and status == "merged" and guid(source_guid) and guid(destination) and destination ~= source_guid
        and math.type(amount) == "integer" and amount > 0 and math.type(before) == "integer" and before > 0
        and math.type(after) == "integer" and after <= 0x7fffffff and after - before == amount then
        return "merged", {source = source_guid, destination = destination, amount = amount, before = before, after = after}
    end
    return "mutation_unverified_ground_stack_result"
end

return stack
