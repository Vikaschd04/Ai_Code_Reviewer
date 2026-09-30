package com.shop.core.payment;

import org.apache.log4j.Logger;

public class PaymentLogger {

    private static final Logger LOG = Logger.getLogger(PaymentLogger.class);

    // Positive: a card number and a password are written to the log.
    public void logAttempt(final String cardNumber, final String password, final String orderCode) {
        LOG.info("Payment attempt for card " + cardNumber + " with password " + password);
    }

    // Negative: only the order reference is logged.
    public void logResult(final String orderCode, final boolean approved) {
        LOG.info("Payment result for order " + orderCode + ": " + approved);
    }
}
