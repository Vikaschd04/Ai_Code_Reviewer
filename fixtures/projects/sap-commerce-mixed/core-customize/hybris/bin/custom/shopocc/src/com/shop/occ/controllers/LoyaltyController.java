package com.shop.occ.controllers;

import com.shop.facades.impl.DefaultLoyaltyFacade;
import javax.annotation.Resource;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping(value = "/{baseSiteId}/loyalty")
public class LoyaltyController {

    @Resource(name = "loyaltyFacade")
    private DefaultLoyaltyFacade loyaltyFacade;

    @PostMapping("/{customerId}/welcome")
    public void welcome(@PathVariable final String customerId) {
        loyaltyFacade.addWelcomePoints(customerId);
    }
}
