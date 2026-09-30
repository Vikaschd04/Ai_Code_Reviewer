package com.shop.core.interceptors;

import de.hybris.platform.core.model.order.OrderModel;
import de.hybris.platform.servicelayer.interceptor.InterceptorContext;
import de.hybris.platform.servicelayer.interceptor.InterceptorException;
import de.hybris.platform.servicelayer.interceptor.ValidateInterceptor;

public class AuditValidateInterceptor implements ValidateInterceptor<OrderModel> {

    // Negative: validation only, no persistence side effects.
    @Override
    public void onValidate(final OrderModel order, final InterceptorContext context)
            throws InterceptorException {
        if (order.getTotalPrice() != null && order.getTotalPrice() < 0) {
            throw new InterceptorException("Order total must not be negative");
        }
    }
}
