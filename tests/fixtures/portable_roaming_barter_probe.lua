local function roaming_probe(fields, state, phase)
        local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
        local pawn = assert(state.local_pawn.pawn)
        assert(state.map == observatory and (phase == "hosting" or phase == "connected"))
        local original = StaticFindObject(observatory .. ".Observitory_P:PersistentLevel.NPC_LoneStalker_C_0")
        local mode = fields[3]
        if mode == "spawn" and phase == "hosting" then
            assert(valid(original) and original.Alive and valid(original.BarterRef))
            local origin = original:K2_GetActorLocation()
            local facing = original:K2_GetActorRotation()
            local actor = state.world:SpawnActor(original:GetClass(), {X=origin.X+120, Y=origin.Y+50, Z=origin.Z+15}, facing)
            assert(valid(actor) and actor:HasAuthority() and not actor:HasAnyFlags(EObjectFlags.RF_WasLoaded))
            actor:SetReplicates(true)
            actor:SetReplicateMovement(true)
            actor.CharacterMovement:DisableMovement()
            if valid(actor.Controller) then actor.Controller:StopMovement() end
            actor:ForceNetUpdate()
            local point = actor:K2_GetActorLocation()
            local remote = assert(state.guests[1]).pawn
            local placed = false
            for _, offset in ipairs({{0,130},{130,0},{0,-130},{-130,0}}) do
                if remote:K2_TeleportTo({X=point.X+offset[1],Y=point.Y+offset[2],Z=point.Z+20}, facing) then
                    placed = true
                    break
                end
            end
            assert(placed, "the guest could not approach the roaming trader")
            return ("%f,%f,%f"):format(point.X, point.Y, point.Z)
        end
        local x, y, z = fields[4]:match("^([^,]+),([^,]+),([^,]+)$")
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        assert(x and y and z)
        assert(valid(original), "stock Observatory trader is unavailable")
        local candidates = {}
        for _, npc in ipairs(FindAllOf(original:GetClass():GetFName():ToString()) or {}) do
            if valid(npc) and not same(npc, original) and same(npc:GetWorld(), state.world)
                and (phase == "hosting" and npc:HasAuthority() or phase == "connected" and npc:GetLocalRole() == 1) then
                local point = npc:K2_GetActorLocation()
                local distance = math.sqrt((point.X-x)^2 + (point.Y-y)^2 + (point.Z-z)^2)
                candidates[#candidates+1] = ("%s %.1f alive=%s"):format(npc:GetFName():ToString(), distance, tostring(npc.Alive))
                if distance < 250 and npc.Alive then
                    local actor = npc.BarterRef
                    if not valid(actor) then return "waiting-barter" end
                    local key = trader_identity(actor, state)
                    if mode == "inspect" then
                        local ids = {}
                        for index = 1, actor.Inventory:GetArrayNum() do
                            ids[#ids + 1] = guid(actor.Inventory[index][item_guid])
                        end
                        table.sort(ids)
                        return table.concat({tostring(npc:GetLocalRole()), guid(npc.NpcId) or "-", tostring(actor.Save),
                            guid(actor.TraderID) or "-", key or "-", #ids > 0 and table.concat(ids, '|') or "-"}, ",")
                    elseif mode == "prepare" and phase == "hosting" then
                        local guest = assert(state.guests[1]).pawn
                        local angle = pawn:K2_GetActorRotation()
                        local target = {X=point.X - 100, Y=point.Y + 20, Z=point.Z + 5}
                        assert(guest:K2_TeleportTo(target, angle), "guest could not approach the trader")
                        local funds = state.world.OwningGameInstance
                        funds:Give_PlayerCash(1200, false)
                        local asset = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
                        local class = load(asset)
                        assert(valid(class))
                        local output = {}
                        actor:GenerateItem(class, 1, output)
                        local item = output.Item or output
                        assert(valid(item[item_class]) and guid(item[item_guid]))
                        actor.Inventory[actor.Inventory:GetArrayNum() + 1] = item
                        return guid(item[item_guid]) .. "," .. ("%f,%f,%f"):format(target.X,target.Y,target.Z)
                    elseif mode == "open" and phase == "connected" then
                        actor:SendInteractionToActor(pawn, actor.Box)
                        local screen = state.controller.MenuStack[1]
                        assert(exact(screen, barter_screen) and same(screen.Trader, actor), "stock barter did not open")
                        return ("%s,%s"):format(tostring(screen:GetRenderOpacity()), tostring(screen:GetIsEnabled()))
                    elseif mode == "close" and phase == "connected" then
                        state.controller:CloseMenu({})
                        return tostring(state.controller.MenuStack:GetArrayNum())
                    elseif mode == "buy" and phase == "connected" then
                        local screen = state.controller.MenuStack[1]
                        assert(exact(screen, barter_screen) and same(screen.Trader, actor) and screen:GetIsEnabled()
                            and screen:GetRenderOpacity() == 1 and actor.Save == false)
                        local stock
                        for index = 1, actor.Inventory:GetArrayNum() do
                            local row = actor.Inventory[index]
                            if guid(row[item_guid]) == fields[5] then stock = row break end
                        end
                        assert(stock, "the host bandage was missing from guest stock")
                        local quotation = {}
                        actor:ValueItem(stock, quotation)
                        local minimum = screen.MinBalance + .020
                        local price = math.ceil(quotation.TraderValue * minimum / (1 - minimum) / actor.MerchantData.CashValueMod)
                        assert(price > 0 and price < 1200, "unexpected stock bandage price")
                        local staged = StaticFindObject("/Script/Engine.Default__GameplayStatics"):CreateSaveGameObject(load(save_class))
                        staged.PlayerBackpack[1] = stock
                        local removed, offered = {}, {}
                        actor:RemoveItem(staged.PlayerBackpack[1][item_guid], 0, removed, removed)
                        assert(removed.Success)
                        screen:AddItem(staged.PlayerBackpack[1], 1, -1, offered, offered, offered)
                        assert(offered.Success and screen.TraderOffer:GetArrayNum() == 1)
                        screen.PlayerCashOffer = price
                        screen:UpdateBalance()
                        assert(screen.Button_Confirm:GetIsEnabled(), "stock guest trade confirmation unavailable")
                        screen:BndEvt__BarterScreen_Button_Confirm_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature()
                        return tostring(price)
                    end
                    error("unknown roaming barter fixture operation", 0)
                end
            end
        end
        return "waiting-npc " .. table.concat(candidates, " | ")
end

local probe_ticket
local probe_mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local function poll_roaming_probe(state, phase)
    if not probe_mailbox or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
        or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1" then return end
    local prefix = probe_mailbox .. "/portable-roaming-barter-"
    local command = io.open(prefix .. "command.tsv", "rb")
    if not command then return end
    local body = command:read(512)
    command:close()
    local ticket, mode, position, item = (body or ""):match("^(%x+)\t([a-z]+)\t?([^\t]*)\t?([^\r\n]*)$")
    if not ticket or #ticket ~= 32 or ticket == probe_ticket then return end
    probe_ticket = ticket
    local ok, result = pcall(roaming_probe, {ticket, "fixture-roaming-barter", mode, position, item}, state, phase)
    local response = assert(io.open(prefix .. "reply.tsv", "wb"))
    response:write(ticket, "\t", ok and "ok" or "error", "\t", (tostring(result):gsub("[\t\r\n]", " ")))
    response:close()
end
