local photo = {}
local quest_catalog = require("native_quest_catalog")
local guest_save = require("guest_save")
local players = require("players")

local id, final_step, cash_reward, xp_reward = "Photo_DB_Turrets", 3, 700, 1
local borough = "/Game/Classes/Maps/Maps_SafeZones/Borough"
local scientist = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local bridge = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local world_names = { [borough] = borough .. ".Borough", [scientist] = scientist .. ".Shop_Scientist_01",
    [bridge] = bridge .. ".DeadBridge_P" }
local boards = {
    [borough] = {path = world_names[borough] .. ":PersistentLevel.BountyBoard_BP_C_3", kind = 5},
    [scientist] = {path = world_names[scientist] .. ":PersistentLevel.BountyBoard_BP_C_2", kind = 4},
}
local turret_prefix = world_names[bridge] .. ":PersistentLevel.Turret_MechGun_01_BP_C_"
local targets = { ["turret-13"] = turret_prefix .. "13", ["turret-15"] = turret_prefix .. "15" }
local board_type = 5
local commune, base_rep, advantage = 2, 50, 150

local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local camera_class = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
local component_class = "/Game/Classes/Actors/ActorAssets/CameraTarget_BP.CameraTarget_BP_C"
local table_path = "/Game/Classes/Libraries/DataTables/Quests_DT.Quests_DT"
local schema_path = "/Game/Classes/Libraries/Structs/Quest_Struct.Quest_Struct"
local code = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local characters = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"

local fields = {
    step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A",
    final = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B",
    cash = "Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0",
    xp = "Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797",
    items = "Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A",
    objectives = "Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083",
    consumed = "ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333",
    redeem = "RedeemAt_83_FAE34B6C471D2019508A4898E9A0080F",
    boards = "AppearOn_Boards_95_2FDB27AE4695B9213302E5897144A531",
    global = "AppearOn_GlobalBountyBoards_78_B538291F4816D684913EAA8852507698",
    variant = "QuestType_47_1031681D471E9929E30B17985E174BBE",
    difficulty = "Difficulty_12_9F1A01AD46151E4F5F152B82C99B5D85",
    fail = "Fail_OnDeath_9_BAADF6C6450D1EB972F6E8A33214ACE4",
    prereq = "PreReq_Flags_105_3FA9C82A48BE1675F41E15A54570DBAD",
    rep = "ReputationGain_86_DA02B32344A59A6C236B848B22520EEC",
    advantage = "Advantage_Faction_100_F1B0B60547EEC0A936CFECB9BE005CE2",
    flags = "Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F",
    quests = "Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9",
    ping = "GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F",
    item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81",
    guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A",
}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_EVENTS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_DEATH") == "cpp"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local operations = {
    ["native-turret-photo-preflight"] = "preflight", ["native-turret-photo-start"] = "start",
    ["native-turret-photo-capture"] = "capture", ["native-turret-photo-turn-in"] = "turn_in",
    ["native-turret-photo-capture-fixture"] = "capture",
    ["native-turret-photo-xp-fixture"] = "xp_fixture",
    ["native-host-camera-aim-fixture"] = "aim_fixture",
}
local last, last_board

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function fail(reason)
    error("stock_turret_photo_" .. reason, 0)
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function int32(value)
    return finite(value) and math.tointeger(value) ~= nil and value >= -2147483648 and value <= 2147483647
end

local function xyz(value)
    if value and finite(value.X) and finite(value.Y) and finite(value.Z) then
        return {X = value.X, Y = value.Y, Z = value.Z}
    end
end

local function within(first, second, range)
    return first and second and (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2 <= range^2
end

local function fname(text)
    local name = FName(text, EFindName.FNAME_Find)
    if name:GetComparisonIndex() == 0 or name:ToString() ~= text then fail("name_unavailable") end
    return name
end

local function count(array)
    return array and array:GetArrayNum()
end

local function row_at(rows, key)
    local present = rows and rows:Contains(key)
    if type(present) ~= "boolean" then fail("quest_map_unqualified") end
    if present then
        local result = rows:Find(key):get()
        if not result then fail("quest_row_unavailable") end
        return result
    end
end

local function recipe(row)
    if not row then fail("quest_row_unavailable") end
    local boards, rep, offer = row[fields.boards], row[fields.rep], row[fields.advantage]
    local ping = row[fields.ping]
    if type(ping) == "userdata" then ping = ping:ToString() end
    if row[fields.final] ~= final_step or row[fields.cash] ~= cash_reward or row[fields.xp] ~= xp_reward
        or row[fields.redeem] ~= board_type or row[fields.variant] ~= 2 or row[fields.difficulty] ~= 4
        or row[fields.fail] ~= false or row[fields.global] ~= true or row[fields.consumed] ~= false
        or count(row[fields.items]) ~= 0 or count(row[fields.objectives]) ~= 0 or count(row[fields.quests]) ~= 0
        or not row[fields.prereq] or #row[fields.prereq] ~= 0 or not row[fields.flags] or #row[fields.flags] ~= 0
        or ping ~= "None" or count(boards) ~= 2 or boards[1] ~= board_type or boards[2] ~= 4
        or not rep or #rep ~= 1 or not rep:Contains(commune) or rep:Find(commune):get() ~= base_rep
        or not offer or #offer ~= 1 or not offer:Contains(commune) or offer:Find(commune):get() ~= advantage then
        fail("quest_recipe_unqualified")
    end
end

local function read_flag(instance, name)
    local result = {}
    instance:Check_SaveFlag(type(name) == "string" and fname(name) or name, result, result)
    if type(result.Found) ~= "boolean" or not int32(result.Value) then fail("flag_unqualified") end
    return result.Found and result.Value or 0, result.Found
end

local function actor_context(state, phase, name)
    local world_name, world, host = world_names[state.map], state.world, state.local_pawn
    if not enabled or phase ~= "hosting" or state.listening ~= true or state.server ~= false or not world_name
        or not valid(world) or world:GetFullName() ~= "World " .. world_name or not host
        or not exact(host.pawn, pawn_class) or not same(host.controller_object, state.controller)
        or not host.pawn:HasAuthority() or host.pawn:IsActorBeingDestroyed() or not host.pawn:IsLocallyControlled()
        or not state.controller:HasAuthority() or not state.controller:IsLocalController()
        or not same(host.pawn.Controller, state.controller) or not same(state.controller.Pawn, host.pawn)
        or not same(host.pawn:GetWorld(), world) or not same(state.controller:GetWorld(), world) then fail("listen_host_unavailable") end
    local driver = world.NetDriver
    if not valid(driver) or not same(driver.World, world) or valid(driver.ServerConnection) then fail("listen_driver_unqualified") end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(host.pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
        or not exact(save, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C") or not same(host.pawn.Save, save)
        or not same(instance["As Player FP"], host.pawn) then fail("canonical_save_unqualified") end

    local pawn, own_save = host.pawn, save
    if name ~= host.pawn:GetFullName() then
        pawn, own_save = guest_save.exact_item_context(state, phase, name)
        if not exact(pawn, pawn_class) or not exact(own_save, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
            or same(own_save, save) or not players.is_standing_guest(pawn, name) then fail("detached_guest_unavailable") end
    elseif players.is_downed(pawn) ~= false then
        fail("actor_unavailable")
    end
    if pawn.Dead ~= false or pawn:GetFullName() ~= name or not same(pawn:GetWorld(), world)
        or not same(pawn.Save, own_save) then fail("actor_unavailable") end
    return {host = host.pawn, world = world, map = state.map, pawn = pawn, own_save = own_save,
        instance = instance, save = save, name = name, key = fname(id)}
end

local function board_for(context, inspect_offer_only)
    local placement = boards[context.map]
    local board = placement and StaticFindObject(placement.path)
    if not exact(board, "/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C")
        or board:GetFullName() ~= "BountyBoard_BP_C " .. placement.path or not same(board:GetWorld(), context.world)
        or not same(board["As Game Instance FP"], context.instance) or not board:HasAuthority()
        or board:IsActorBeingDestroyed() or board.TurnIn_ID ~= placement.kind or board.BountyBoard_RedeemableHere ~= true
        or board.Display_GlobalContracts ~= false or board.AcceptAllGlobalContracts ~= true
        or board.ShowAllContracts ~= false then fail("board_unqualified") end
    local box = board.Box
    if not exact(box, "/Script/Engine.BoxComponent") or not same(box:GetOwner(), board) then fail("board_box_unqualified") end
    local collision = box:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then fail("board_not_interactable") end
    if inspect_offer_only then return board end
    local reach, point = context.pawn.InteractionRange, xyz(context.pawn:K2_GetActorLocation())
    if not finite(reach) or reach <= 0 or reach > 1000
        or not within(point, xyz(board:K2_GetActorLocation()), reach)
        or not within(point, xyz(box:K2_GetComponentLocation()), reach) then fail("board_out_of_reach") end
    return board
end

local function read_progress(context)
    local active = row_at(context.save.ActiveQuests, context.key)
    local step = active and active[fields.step]
    if active then
        recipe(active)
        if step ~= 1 and step ~= 2 and step ~= final_step and step ~= -10 then fail("quest_step_unqualified") end
    end
    local library, result = StaticFindObject(characters), {}
    if not valid(library) then fail("reputation_library_unavailable") end
    library:Compose_FactionRep_FlagName(commune, context.host, result)
    if not result.Name or result.Name:ToString() ~= "Rep_Commune" then fail("reputation_flag_unqualified") end
    local rep, found = read_flag(context.instance, result.Name)
    return {step = step or "-", cash = read_flag(context.instance, "Player_Cash"),
        xp = read_flag(context.instance, "Player_Xp"), rep = rep, rep_found = found}
end

local function stock_table()
    local source = StaticFindObject(table_path)
    local schema = valid(source) and source:GetRowStruct()
    if not valid(source) or source:GetFullName() ~= "DataTable " .. table_path or not valid(schema)
        or schema:GetFullName() ~= "UserDefinedStruct " .. schema_path then fail("quest_table_unqualified") end
    local template = source:FindRow(id)
    recipe(template)
    if template[fields.step] ~= 1 then fail("template_step_unqualified") end
    return source
end

local function stock_offer(context, board)
    if context.map == borough and read_flag(context.instance, "PlayerKill_InBorough") > 0 then fail("board_refuses_player") end
    board:Configure_Stock()
    local offered = row_at(board.Contracts, context.key)
    if not offered or offered[fields.step] ~= 1 then fail("quest_not_offered") end
    recipe(offered)
end

local function xp_fixture(context, board, before)
    if not fixtures or not same(context.pawn, context.host) or before.step ~= "-" or before.xp ~= 0
        or #context.save.ActiveQuests ~= 0 or read_flag(context.instance, "PlayerKill_InBorough") ~= 0 then
        fail("xp_fixture_fresh_host_required")
    end
    board:Configure_Stock()
    if row_at(board.Contracts, context.key) then fail("xp_fixture_offer_already_present") end
    local checked = pcall(function() context.instance:GivePlayer_XP(4) end)
    local read, after = pcall(read_progress, context)
    local offered = pcall(stock_offer, context, board)
    if not checked or not read or not offered or not same(context.host.Save, context.save)
        or not same(context.instance.SaveSlot_Ref, context.save) or #context.save.ActiveQuests ~= 0
        or after.step ~= "-" or after.xp ~= 4 or after.cash ~= before.cash or after.rep ~= before.rep then
        error("mutation_unverified_stock_turret_photo_xp_fixture", 0)
    end
    return after
end

local function equipped_camera(context)
    local pawn, camera = context.pawn, context.pawn.EquipedItem
    if not exact(camera, camera_class) or not camera:HasAuthority() or camera:IsActorBeingDestroyed()
        or not same(camera:GetOwner(), pawn) or not same(camera:GetWorld(), context.world)
        or not same(camera.User, pawn) or camera.HeldByPlayer ~= true or pawn.IsWeaponBlocked ~= false then
        fail("owned_camera_not_equipped")
    end
    local equipment = context.own_save.PlayerEquipment
    local index, total = context.own_save.PlayerEquipIndex, count(equipment)
    if not int32(total) or total > 4096 or not int32(index) or index < 0 or index >= total then fail("camera_slot_unqualified") end
    local entry = equipment[index + 1]
    if not entry or not same(entry[fields.item_class], camera:GetClass()) then fail("camera_slot_unqualified") end
    local guid, words = entry[fields.guid], {}
    for _, member in ipairs({"A", "B", "C", "D"}) do
        local word = guid and guid[member]
        if not int32(word) then fail("camera_identity_unqualified") end
        words[#words + 1] = word & 0xffffffff
    end
    local identity = string.format("%08X%08X%08X%08X", table.unpack(words))
    if not identity:find("[^0]") then fail("camera_identity_unqualified") end
    return camera, identity
end

local function frustum(camera, context)
    local lens = camera.SceneCaptureComponent2D
    if not exact(lens, "/Script/Engine.SceneCaptureComponent2D") or not same(lens:GetOwner(), camera) then fail("camera_lens_unqualified") end
    local frame = lens:K2_GetComponentToWorld()
    local translation, quat = frame and xyz(frame.Translation), frame and frame.Rotation
    local rotation, render = xyz(quat), lens.TextureTarget
    local fov = lens.FOVAngle
    if not within(translation, xyz(context.pawn:K2_GetActorLocation()), 400) or not rotation or not finite(quat.W)
        or math.abs(rotation.X^2 + rotation.Y^2 + rotation.Z^2 + quat.W^2 - 1) > 0.01
        or not valid(render) or not int32(render.SizeX) or not int32(render.SizeY)
        or render.SizeX < 1 or render.SizeX > 4096 or render.SizeY < 1 or render.SizeY > 4096
        or not finite(fov) or fov <= 0 or fov >= 179 then fail("camera_frustum_unqualified") end
    local horizontal = math.tan(math.rad(fov * 0.5))
    rotation.W = quat.W
    -- Eqp_Camera.TakePicture passes a unit depth and the two half-angle slopes to TakePhoto.
    return {Translation = translation, Rotation = rotation,
        Scale3D = {X = 1, Y = horizontal, Z = horizontal * render.SizeY / render.SizeX}}
end

local function stock_target(context, target, source)
    local path = targets[target]
    if context.map ~= bridge or not path then fail("unknown_target") end
    local owner = StaticFindObject(path)
    local component = StaticFindObject(path .. ".CameraTarget_BP")
    if not valid(owner) or owner:GetFullName() ~= "Turret_MechGun_01_BP_C " .. path
        or not owner:HasAuthority() or owner:IsActorBeingDestroyed() or not same(owner:GetWorld(), context.world)
        or not exact(component, component_class) or not same(component:GetOwner(), owner)
        or not same(owner:GetComponentByClass(component:GetClass()), component) then fail("target_unavailable") end
    local handle, points = component.TrackedQuest, component.TargetPoints
    local point = count(points) == 1 and points[1]
    local primitive = point and point.Primitive_6_8BEB66464DF62A10BABD8A9ECF81CFDE
    if type(primitive) == "userdata" then primitive = primitive:ToString() end
    if not handle or not same(handle.DataTable, source) or not handle.RowName or handle.RowName:ToString() ~= id
        or component.Only_Proc_once ~= true or component.Target_QuestStep ~= -1 or component.Add_QuestStep ~= 1
        or component.MaxVisibleDistance ~= 25000 or component.SuccessThreshold ~= 0.75
        or not component.ReqiredFlags or #component.ReqiredFlags ~= 0 or not component.SetFlags or #component.SetFlags ~= 0
        or primitive ~= "Capsule"
        or point.Weight_9_10321D2441683E2EC000A9BBFCDFAB5E ~= 1 then fail("target_recipe_unqualified") end
    return component, owner
end

local function capture(context, source, before, target, aim_fixture)
    if before.step ~= 1 and before.step ~= 2 then fail("quest_not_active") end
    local camera, guid = equipped_camera(context)
    local component, owner = stock_target(context, target, source)
    local function take_stock_photo()
        local frame = frustum(camera, context)
        if not within(frame.Translation, xyz(owner:K2_GetActorLocation()), component.MaxVisibleDistance) then fail("target_out_of_range") end
        local output = {}
        local checked, returned = pcall(function() return component:TakePhoto(frame, output) end)
        local read, after = pcall(read_progress, context)
        if not checked or not read or not same(context.pawn.Save, context.own_save)
            or not same(context.host.Save, context.save) or after.cash ~= before.cash or after.xp ~= before.xp
            or after.rep ~= before.rep then error("mutation_unverified_stock_turret_photo_capture", 0) end
        local score = output.Visibility or returned
        if not finite(score) or score < 0 or score > 1 then error("mutation_unverified_stock_turret_photo_score", 0) end
        if after.step == before.step and score < 0.75 then fail("target_not_in_stock_frame") end
        if after.step ~= before.step + 1 then error("mutation_unverified_stock_turret_photo_progress", 0) end
        return after, guid, score
    end
    if aim_fixture then
        local pose = require("native_turret_photo_aim_fixture")
        return pose.capture(context, camera, component, owner, target, take_stock_photo)
    end
    return take_stock_photo()
end

local function turn_in(context, before)
    if before.step ~= final_step then fail("quest_not_ready") end
    local trait = {}
    context.instance:FindPassiveEffect(fname("IntensePersonality"), true, trait, trait)
    if type(trait.EffectActive) ~= "boolean" or not int32(trait.Instances) or trait.Instances < 0 then fail("trait_unqualified") end
    local expected = before.rep + base_rep * (before.rep_found and 1 + trait.Instances or 1)
    if not int32(expected) or not int32(before.cash + cash_reward) or not int32(before.xp + xp_reward) then fail("reward_range") end
    local library, output = StaticFindObject(code), {}
    if not valid(library) or library:GetFullName() ~= "FL_CodeLib01_C " .. code then fail("turn_in_library_unavailable") end
    local checked = pcall(function() library:TurnIn_Quest(context.key, context.instance, true, context.host, output, output) end)
    local read, after = pcall(read_progress, context)
    if not checked or not read or output.Success ~= true or valid(output.ConfirmationPopup) or after.step ~= -10
        or after.cash ~= before.cash + cash_reward or after.xp ~= before.xp + xp_reward or after.rep ~= expected
        or not same(context.host.Save, context.save) or not same(context.pawn.Save, context.own_save) then
        error("mutation_unverified_stock_turret_photo_turn_in", 0)
    end
    return after
end

function photo.command(command, state, phase)
    if command[2] == "native-bounty-board-list" then
        last_board = nil
        if #command ~= 3 or type(command[3]) ~= "string" or #command[3] > 512 or command[3]:find("%c") then
            fail("board_arguments_unqualified")
        end
        local context = actor_context(state, phase, command[3])
        local board = board_for(context)
        board:Configure_Stock()
        if not board.Contracts or not same(context.host.Save, context.save) or not same(context.pawn.Save, context.own_save) then
            fail("board_offers_unavailable")
        end
        last_board = {scope = context, board = board:GetFullName(), time = os.time(),
            entries = quest_catalog.board_rows(board.Contracts, table.pack)}
        return "qualified stock board offers"
    end
    local verb = operations[command[2]]
    if not verb then return end
    last = nil
    local field_action = verb == "capture" or verb == "aim_fixture"
    if #command ~= (field_action and 5 or 3) or type(command[3]) ~= "string"
        or #command[3] > 512 or command[3]:find("%c") then fail("arguments_unqualified") end
    local context = actor_context(state, phase, command[3])
    local source = stock_table()
    local before, after, guid, visibility = read_progress(context)
    local target = "-"
    if field_action then
        target = command[4]
        -- The host supplies the durable prior step after checking its per-target receipt ledger.
        if tonumber(command[5]) ~= before.step then fail("step_changed") end
        if verb == "aim_fixture" then
            if not fixtures or (before.step ~= 1 and before.step ~= 2) then fail("aim_fixture_unavailable") end
            local camera, identity = equipped_camera(context)
            local component, owner = stock_target(context, target, source)
            require("native_turret_photo_aim_fixture").aim_host(context, camera, component, owner, target)
            guid = identity
        else
            after, guid, visibility = capture(context, source, before, target, command[2] == "native-turret-photo-capture-fixture")
        end
    else
        if (verb == "turn_in" or verb == "xp_fixture") and context.map ~= borough then fail("borough_board_required") end
        local board = board_for(context, verb == "xp_fixture")
        target = board:GetFullName()
        if verb ~= "xp_fixture" and before.step == "-" then stock_offer(context, board) end
        if verb == "xp_fixture" then
            after = xp_fixture(context, board, before)
        elseif verb == "start" and before.step == "-" then
            local checked = pcall(function() context.instance:AddQuest_ByName(context.key) end)
            local read
            read, after = pcall(read_progress, context)
            if not checked or not read or after.step ~= 1 or after.cash ~= before.cash or after.xp ~= before.xp
                or after.rep ~= before.rep or not same(context.host.Save, context.save) then
                error("mutation_unverified_stock_turret_photo_start", 0)
            end
        elseif verb == "turn_in" then
            after = turn_in(context, before)
        end
    end
    last = {context = context, before = before, after = after or before, verb = verb, target = target,
        guid = guid or "-", visibility = visibility or "-"}
    return "verified scoped stock Dead Bridge photo quest"
end

function photo.rows(state, phase, status_row)
    local result = {}
    local list = last_board
    if list then
        local scope = list.scope
        if phase == "hosting" and state.map == scope.map and same(scope.world, state.world) and os.time() <= list.time + 2
            and valid(scope.pawn) and same(scope.pawn.Save, scope.own_save) and same(scope.host.Save, scope.save) then
            result[1] = status_row("native-bounty-board", scope.map, scope.name, list.board)
            for _, fields in ipairs(list.entries) do result[#result + 1] = status_row(table.unpack(fields, 1, fields.n)) end
        else
            last_board = nil
        end
    end
    local proof = last
    if not proof then return result end
    local scope, before, after = proof.context, proof.before, proof.after
    if phase ~= "hosting" or state.map ~= scope.map or not same(state.world, scope.world)
        or not valid(scope.host) or not valid(scope.pawn) or not same(scope.host.Save, scope.save)
        or not same(scope.pawn.Save, scope.own_save) then
        last = nil
        return result
    end
    result[#result + 1] = status_row("native-turret-photo", proof.verb, scope.map, scope.name, id, proof.target,
        proof.guid, proof.visibility, before.step, after.step, before.cash, after.cash, before.xp, after.xp,
        before.rep, after.rep)
    return result
end

return photo
