package com.shop.core.integration;

import de.hybris.platform.servicelayer.config.ConfigurationService;

public class EndpointConfig {

    private ConfigurationService configurationService;

    // Positive: environment-specific endpoint compiled into the code.
    public String loyaltyEndpoint() {
        return "https://loyalty.stage.shop.internal/api/v1";
    }

    // Negative: resolved from configuration per environment.
    public String paymentEndpoint() {
        return configurationService.getConfiguration().getString("shop.payment.endpoint");
    }
}
