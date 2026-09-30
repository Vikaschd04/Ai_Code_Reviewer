package com.shop.core.interceptors;

import com.shop.core.model.LoyaltyAccountModel;
import de.hybris.platform.servicelayer.interceptor.InterceptorContext;
import de.hybris.platform.servicelayer.interceptor.InterceptorException;
import de.hybris.platform.servicelayer.interceptor.PrepareInterceptor;
import de.hybris.platform.servicelayer.model.ModelService;

public class LoyaltyPrepareInterceptor implements PrepareInterceptor<LoyaltyAccountModel> {

    private ModelService modelService;

    // Positive: saving from inside a prepare interceptor re-enters the save cycle.
    @Override
    public void onPrepare(final LoyaltyAccountModel account, final InterceptorContext context)
            throws InterceptorException {
        if (account.getPoints() == null) {
            account.setPoints(0);
        }
        modelService.save(account.getCustomer());
    }

    public void setModelService(final ModelService modelService) {
        this.modelService = modelService;
    }
}
