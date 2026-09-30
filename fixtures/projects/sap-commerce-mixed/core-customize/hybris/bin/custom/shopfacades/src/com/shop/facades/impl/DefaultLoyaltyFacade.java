package com.shop.facades.impl;

import com.shop.core.service.impl.DefaultLoyaltyService;
import javax.annotation.Resource;

public class DefaultLoyaltyFacade {

    @Resource(name = "loyaltyService")
    private DefaultLoyaltyService loyaltyService;

    public void addWelcomePoints(final String customerId) {
        loyaltyService.addPoints(java.util.List.of(), 100);
    }
}
