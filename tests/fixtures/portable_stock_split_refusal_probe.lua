    if operation == "native-inventory-stock-split-click-fixture" and #fields >= 6
        and headless and stock_session and stock_session.split_preview
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local screen, save = stock_session.screen, stock_session.save
        if #fields == 6 and fields[6] == "stale" and not stock_drop and #stock_layout == 0 then
            for index = 1, save.PlayerBackpack:GetArrayNum() do
                local row = save.PlayerBackpack[index]
                if stock_item_guid(row) == fields[3] then
                    local count = row[item_ammo]
                    assert(count == 9 and stock_cards(screen, save, fields[3], count),
                        "fixture requires the unchanged nine Bandages")
                    -- Simulate an out-of-date guest projection. The host still has nine.
                    row[item_ammo] = count + 1
                    screen.BackpackPanel:FullRefresh()
                    assert(stock_cards(screen, save, fields[3], count + 1), "fixture stale card did not render")
                    return inventory_view.command({table.unpack(fields, 1, 5)}, state, phase)
                end
            end
            error("fixture did not find its exact Bandage stack")
        end
        if #fields == 6 and fields[6] == "verify" then
            if #stock_layout > 0 or stock_drop then return "waiting for stock split reconciliation" end
            assert(stock_cards(screen, save, fields[3], 9), "the stock menu did not restore the original Bandage stack")
            assert(stock_cards(screen, save, fields[4], 0) and stock_cards(screen, save, fields[5], 0),
                "the stock menu kept a speculative Bandage stack")
            assert(not screen.ItemManager.IsSplittingStack and not screen.ItemManager.SplitStackPopup:IsVisible(),
                "the stock menu kept the rejected split popup open")
            return "restored stock stack without either speculative card"
        end
    end
