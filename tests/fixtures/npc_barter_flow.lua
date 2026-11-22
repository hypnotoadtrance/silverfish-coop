local probe = {}
local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if not box or not box:find("npc%-barter%-flow%-%x+") or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" then return probe end

local barter = require("native_npc_barter")
local saves = require("guest_save")
local zone = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local npc_name = zone .. ".Observitory_P:PersistentLevel.NPC_LoneStalker_C_0"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local jewelry = "/Game/Classes/Items/Item_Jewely.Item_Jewely_C"
local peaches = "/Game/Classes/Items/Item_CannedPeaches.Item_CannedPeaches_C"
local mode = os.getenv("SILVERFISH_COOP_BARTER_PROBE")
local skinflint = mode == "skinflint"
local donation, high_value = mode == "donation", mode == "high-value" or skinflint
local refresh_test = mode == "refresh"
local payment = donation and jewelry or high_value and peaches or nil
local money = high_value and 20000 or 1200
local next_attempt, armed, committed, pending_confirmation, refreshed = 0, false, false, false, false

local function valid(value) return value and value:IsValid() end

local function proof(...)
    local values = {...}
    for i, value in ipairs(values) do values[i] = tostring(value) end
    local file = assert(io.open(box .. "/npc-barter-flow.tsv", "ab"))
    file:write(table.concat(values, "\t"), "\n")
    file:close()
end

local function seed(actor, rows, path, count)
    local class = StaticFindObject(path)
    if not valid(class) then LoadAsset(path); class = StaticFindObject(path) end
    local result = {}
    actor:GenerateItem(class, count, result)
    local item = result.Item or result
    assert(valid(item[item_class]), "stock barter item generator failed")
    rows[rows:GetArrayNum() + 1] = item
end

local function place_guest(pawn, actor)
    local location = actor:K2_GetActorLocation()
    local rotation = pawn:K2_GetActorRotation()
    local nearby = pawn:K2_TeleportTo({X = location.X + 107, Y = location.Y, Z = location.Z + 20}, rotation)
        or pawn:K2_TeleportTo({X = location.X, Y = location.Y + 107, Z = location.Z + 20}, rotation)
    assert(nearby, "the exact guest teleport was blocked")
    pawn:ForceNetUpdate()
end

local function fatigue(save)
    local flag = FName("Player_Fatigue")
    return save.Flags:Contains(flag) and save.Flags:Find(flag):get() or 0
end

local function prepare(state, phase, actor)
    local guest = state.guests[1]
    if not guest then return end
    local pawn, save = saves.exact_item_context(state, phase, guest.name)
    if not valid(pawn) then return end
    place_guest(pawn, actor)
    state.world.OwningGameInstance:Give_PlayerCash(money, false)
    if high_value then seed(actor, actor.Inventory, jewelry, 8) end
    if payment then seed(actor, save.PlayerBackpack, payment, donation and 12 or 1) end
    if skinflint then
        local character = save.Character
        local key = "PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"
        character[key] = {FName("Skinflint")}
        save.Character = character
        assert(save.Character[key][1]:ToString() == "Skinflint", "Stock Skinflint trait was not stored on the guest")
        local host = state.local_pawn.pawn
        local guest_fatigue, host_fatigue = fatigue(save), fatigue(host.Save)
        RegisterHook("/Game/Classes/Player/Player_FP.Player_FP_C:PsyAttack", function(context, amount)
            local attacked = context:get()
            if valid(attacked) and attacked:GetAddress() == pawn:GetAddress() then
                proof("skinflint", attacked:GetFullName(), amount:get(), guest_fatigue, fatigue(save),
                    host_fatigue, fatigue(host.Save))
            end
        end)
    end
    armed = true
    proof("ready", guest.name, actor.Inventory:GetArrayNum())
end

local function confirm(state, screen, actor, payment_row)
    local minimum = screen.MinBalance + .020
    local factor = actor.MerchantData.CashValueMod
    local bid, row, cash = {}, nil, 0
    if payment_row then actor:ValueItem(payment_row, bid) end
    if not donation then
        for index = 1, actor.Inventory:GetArrayNum() do
            local item = actor.Inventory[index]
            local path = item[item_class]:GetFullName()
            if (high_value and path:find(jewelry, 1, true)) or (not high_value and not path:find("/Weapons/", 1, true)) then
                local quote = {}
                actor:ValueItem(item, quote)
                local price = math.ceil((quote.TraderValue * minimum / (1 - minimum) - (bid.TraderValue or 0)) / factor)
                if (high_value and price > 1000 and price <= money) or (not high_value and price > 0 and price <= 1000) then
                    row, cash = item, price
                    break
                end
            end
        end
        assert(row, "no qualified stock barter item")
    end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local save_class = StaticFindObject("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
    local staged = api:CreateSaveGameObject(save_class)
    if row then
        staged.PlayerBackpack[1] = row
        local before = actor.Inventory:GetArrayNum()
        local removed, offered = {}, {}
        actor:RemoveItem(row[item_guid], 0, removed, removed)
        assert(removed.Success and actor.Inventory:GetArrayNum() == before - 1, "stock trader did not stage its item")
        screen:AddItem(staged.PlayerBackpack[1], 1, -1, offered, offered, offered)
        assert(offered.Success and screen.TraderOffer:GetArrayNum() == 1, "stock trade screen did not stage its item")
    end
    if payment_row then
        local slot = staged.PlayerBackpack:GetArrayNum() + 1
        staged.PlayerBackpack[slot] = payment_row
        local removed, offered = {}, {}
        state.world.OwningGameInstance:RemoveItem(payment_row[item_guid], 0, removed, removed)
        assert(removed.Success, "stock backpack did not stage payment")
        screen:AddItem(staged.PlayerBackpack[slot], 0, -1, offered, offered, offered)
        assert(offered.Success and screen.PlayerOffer:GetArrayNum() == 1, "stock screen did not stage payment")
    end
    screen.PlayerCashOffer = cash
    screen:UpdateBalance()
    assert(screen.Button_Confirm:GetIsEnabled(), "stock confirmation unavailable")
    proof("screen", screen:GetFullName(), cash)
    screen:BndEvt__BarterScreen_Button_Confirm_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature()
    if donation then
        local menus = state.controller.MenuStack
        assert(menus:GetArrayNum() == 2, "stock donation modal did not open")
        proof("confirmation", menus[2]:GetFullName())
        pending_confirmation, next_attempt = true, os.time() + 2
    else
        committed = true
    end
end

function probe.sample(state, phase)
    if state.map ~= zone or os.time() < next_attempt then return end
    next_attempt = os.time() + 1
    local npc = StaticFindObject(npc_name)
    local actor = valid(npc) and npc.BarterRef
    if not valid(actor) or actor.Inventory:GetArrayNum() < 1 then return end
    local ok, error_message = pcall(function()
        if phase == "hosting" and not armed then
            prepare(state, phase, actor)
        elseif phase == "hosting" and refresh_test and not refreshed then
            local signal = io.open(box .. "/npc-barter-flow-refresh", "r")
            if not signal then return end
            signal:close()
            state.world.OwningGameInstance:Give_PlayerCash(175, false)
            seed(actor, actor.Inventory, jewelry, 8)
            refreshed = true
            proof("restocked", actor.Inventory:GetArrayNum(), 1375)
        elseif phase == "hosting" and payment then
            local guest = state.guests[1]
            local pawn = guest and saves.exact_item_context(state, phase, guest.name)
            if not valid(pawn) then return end
            local npc_distance, actor_distance = pawn:GetDistanceTo(npc), pawn:GetDistanceTo(actor)
            if math.max(npc_distance, actor_distance) > pawn.InteractionRange * .8 then
                place_guest(pawn, actor)
                proof("follow", npc_distance, actor_distance, pawn.InteractionRange)
            end
        elseif phase == "connected" and not committed and state.local_pawn and state.controller then
            local pawn, menus = state.local_pawn.pawn, state.controller.MenuStack
            if pending_confirmation then
                assert(menus:GetArrayNum() == 2, "stock donation modal closed early")
                menus[2]:BndEvt__ConfirmationScreen_Button_Confirm_K2Node_ComponentBoundEvent_2_Pressed__DelegateSignature()
                committed = true
                return
            end
            local payment_row
            if payment then
                local bag = valid(pawn.Save) and pawn.Save.PlayerBackpack
                for index = 1, bag and bag:GetArrayNum() or 0 do
                    local item = bag[index]
                    if valid(item[item_class]) and item[item_class]:GetFullName():find(payment, 1, true) then
                        payment_row = item
                        break
                    end
                end
                if not payment_row then return end
            end
            local view = barter.rows(state, phase, function(...) return {...} end)[1]
            if not view and menus:GetArrayNum() == 0 then
                local player, merchant = pawn:K2_GetActorLocation(), actor:K2_GetActorLocation()
                if (player.X - merchant.X)^2 + (player.Y - merchant.Y)^2 + (player.Z - merchant.Z)^2 > pawn.InteractionRange^2 then return end
                actor:SendInteractionToActor(pawn, actor.Box)
                proof("interact", valid(menus[1]) and menus[1]:GetFullName() or "-")
            elseif view and view[5] == "ready" and menus:GetArrayNum() == 1 then
                if refresh_test then
                    local flag = FName("Player_Cash")
                    if pawn.Save.Flags:Contains(flag) then
                        proof("visible", actor.Inventory:GetArrayNum(), pawn.Save.Flags:Find(flag):get())
                    end
                else
                    confirm(state, menus[1], actor, payment_row)
                end
            end
        end
    end)
    if not ok then proof("error", error_message) end
end

return probe
