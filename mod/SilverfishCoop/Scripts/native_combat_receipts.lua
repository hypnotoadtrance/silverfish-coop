local receipts = {}

function receipts.cache(conflict)
    local saved_world, outcomes, order
    return function(fields, world, execute)
        local last = fields[#fields]
        local operation = type(last) == "string" and last:match("^op:([0-9a-f]+)$")
        if #fields < 5 or not operation or #operation ~= 32 then
            return execute(fields)
        end
        if not world or not world:IsValid() then
            return {accepted = false, message = conflict}
        end
        if not saved_world or not saved_world:IsValid() or saved_world:GetAddress() ~= world:GetAddress() then
            saved_world, outcomes, order = world, {}, {}
        end
        local request = table.concat(fields, "\t", 2, #fields - 1)
        local prior = outcomes[operation]
        if prior then
            return prior.request == request and prior or {accepted = false, message = conflict}
        end
        local outcome = execute({table.unpack(fields, 1, #fields - 1)})
        if outcome.retryable then return outcome end
        outcome.request = request
        outcomes[operation] = outcome
        order[#order + 1] = operation
        if #order > 256 then outcomes[table.remove(order, 1)] = nil end
        return outcome
    end
end

return receipts
