package com.shop.core.service.impl;

import com.shop.core.dao.impl.DefaultShopProductDao;
import com.shop.core.model.LoyaltyAccountModel;
import de.hybris.platform.servicelayer.model.ModelService;
import java.util.List;

public class DefaultLoyaltyService {

    private ModelService modelService;
    private DefaultShopProductDao productDao;

    // Positive: one save (one transaction and flush) per account.
    public void addPoints(final List<LoyaltyAccountModel> accounts, final int points) {
        for (final LoyaltyAccountModel account : accounts) {
            account.setPoints(account.getPoints() + points);
            modelService.save(account);
        }
    }

    // Negative: changes are collected and saved once.
    public void resetPoints(final List<LoyaltyAccountModel> accounts) {
        for (final LoyaltyAccountModel account : accounts) {
            account.setPoints(0);
        }
        modelService.saveAll(accounts);
    }

    public void setModelService(final ModelService modelService) {
        this.modelService = modelService;
    }

    public void setProductDao(final DefaultShopProductDao productDao) {
        this.productDao = productDao;
    }
}
