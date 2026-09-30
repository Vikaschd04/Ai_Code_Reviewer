package com.shop.core.pricing;

import de.hybris.platform.jalo.JaloSession;
import de.hybris.platform.jalo.product.Product;

public class LegacyPriceHelper {

    // Positive: Jalo layer access in custom code.
    public String currentUser() {
        return JaloSession.getCurrentSession().getUser().getUid();
    }

    public String code(final Product product) {
        return product.getCode();
    }
}
