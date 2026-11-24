    if fields[2] == "portable-stale-quick-slot-reply" then
        local character, nonce, name, guid, slot = fields[3], fields[4], fields[5], fields[6], math.tointeger(fields[7])
        if #fields ~= 7 or phase ~= "connected" or not slot or slot < 0 or slot >= 8
            or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
            or not bridge:match("[/\\]portable%-title%-%x+[/\\]second[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$")
            or #nonce ~= 32 or not nonce:match("^[a-f0-9]+$") or #guid ~= 32 or not guid:match("^[0-9A-F]+$") then
            error("The stale inventory witness requires the private connected guest", 0)
        end
        local save, pawn, id = scope.bind_guest(state, phase)
        if not valid(pawn) or pawn:GetFullName() ~= name or not id or character:gsub("-", "") ~= id then
            error("The stale inventory witness lost its exact guest pawn", 0)
        end
        local function current(label)
            local selected = integer(save.PlayerEquipIndex, 0, count(save.PlayerEquipment) - 1)
            local row = save.PlayerEquipment[selected + 1]
            local identity = row[item.guid]
            local actual = ("%08X%08X%08X%08X"):format(identity.A & 0xffffffff, identity.B & 0xffffffff,
                identity.C & 0xffffffff, identity.D & 0xffffffff)
            if selected ~= slot or actual ~= guid then
                error(("%s selected slot %d with GUID %s instead of slot %d with GUID %s"):format(label, selected, actual, slot, guid), 0)
            end
            local actor = pawn.EquipedItem
            if not valid(actor) or not actor:HasAuthority() or actor:IsActorBeingDestroyed()
                or not valid(row[item.class]) or actor:GetClass():GetAddress() ~= row[item.class]:GetAddress()
                or not valid(actor:GetOwner()) or actor:GetOwner():GetAddress() ~= pawn:GetAddress()
                or not valid(actor:GetWorld()) or actor:GetWorld():GetAddress() ~= state.world:GetAddress() then
                error(label .. " did not have the exact stock weapon in hand", 0)
            end
            return actor, row[item.ammo]
        end
        local prior_actor, prior_rounds = current("Before the stale inventory reply")
        local prior_address = prior_actor:GetAddress()
        project(state, phase, character, bridge .. "/native-guest-ui-" .. nonce .. ".tsv")
        local actor, rounds = current("After the stale inventory reply")
        if actor:GetAddress() ~= prior_address or rounds ~= prior_rounds then
            error("The delayed inventory reply changed the held actor or its magazine", 0)
        end
        return ("preserved selected slot %d with GUID %s and %d rounds"):format(slot, guid, rounds)
    end
