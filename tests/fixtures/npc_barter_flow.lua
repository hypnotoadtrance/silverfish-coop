local probe = {}
local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if not box or not box:find("npc%-barter%-flow%-%x+") or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" then return probe end

local barter = require("native_npc_barter")
local saves = require("guest_save")
local zone = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local npc_name = zone .. ".Observitory_P:PersistentLevel.NPC_LoneStalker_C_0"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local next_attempt, armed, committed = 0, false, false

local function valid(value) return value and value:IsValid() end

local function proof(...)
    local values = {...}
    for i, value in ipairs(values) do values[i] = tostring(value) end
    local file = assert(io.open(box .. "/npc-barter-flow.tsv", "ab"))
    file:write(table.concat(values, "\t"), "\n")
    file:close()
end

local function prepare(state, phase, actor)
    local guest = state.guests[1]
    local pawn = guest and saves.exact_item_context(state, phase, guest.name)
    if not valid(pawn) then return end
    local location = actor:K2_GetActorLocation()
    local rotation = pawn:K2_GetActorRotation()
    local nearby = pawn:K2_TeleportTo({X = location.X + 107, Y = location.Y, Z = location.Z + 20}, rotation)
        or pawn:K2_TeleportTo({X = location.X, Y = location.Y + 107, Z = location.Z + 20}, rotation)
    assert(nearby, "the exact guest teleport was blocked")
    state.world.OwningGameInstance:Give_PlayerCash(1200, false)
    pawn:ForceNetUpdate()
    armed = true
    proof("ready", guest.name, actor.Inventory:GetArrayNum())
end

local function confirm(screen, actor)
    local minimum = screen.MinBalance + .020
    local factor = actor.MerchantData.CashValueMod
    local row, cash
    for index = 1, actor.Inventory:GetArrayNum() do
        local item = actor.Inventory[index]
        local path = item[item_class]:GetFullName()
        if not path:find("/Weapons/", 1, true) then
            local quote = {}
            actor:ValueItem(item, quote)
            local price = math.ceil(quote.TraderValue * minimum / (1 - minimum) / factor)
            if price > 0 and price <= 1000 then
                row, cash = item, price
                break
            end
        end
    end
    assert(row, "no qualified stock barter item")
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local save_class = StaticFindObject("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
    local staged = api:CreateSaveGameObject(save_class)
    staged.PlayerBackpack[1] = row
    local before = actor.Inventory:GetArrayNum()
    local removed = {}
    actor:RemoveItem(row[item_guid], 0, removed, removed)
    assert(removed.Success and actor.Inventory:GetArrayNum() == before - 1, "stock trader did not stage its item")
    local offered = {}
    screen:AddItem(staged.PlayerBackpack[1], 1, -1, offered, offered, offered)
    assert(offered.Success and screen.TraderOffer:GetArrayNum() == 1, "stock trade screen did not stage its item")
    screen.PlayerCashOffer = cash
    screen:UpdateBalance()
    assert(screen.Button_Confirm:GetIsEnabled(), "stock confirmation unavailable")
    proof("screen", screen:GetFullName(), cash)
    screen:BndEvt__BarterScreen_Button_Confirm_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature()
    committed = true
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
        elseif phase == "connected" and not committed and state.local_pawn and state.controller then
            local pawn, menus = state.local_pawn.pawn, state.controller.MenuStack
            local view = barter.rows(state, phase, function(...) return {...} end)[1]
            if not view and menus:GetArrayNum() == 0 then
                local player, merchant = pawn:K2_GetActorLocation(), actor:K2_GetActorLocation()
                if (player.X - merchant.X)^2 + (player.Y - merchant.Y)^2 + (player.Z - merchant.Z)^2 > pawn.InteractionRange^2 then return end
                actor:SendInteractionToActor(pawn, actor.Box)
                proof("interact", valid(menus[1]) and menus[1]:GetFullName() or "-")
            elseif view and view[5] == "ready" and menus:GetArrayNum() == 1 then
                confirm(menus[1], actor)
            end
        end
    end)
    if not ok then proof("error", error_message) end
end

return probe
