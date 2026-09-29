package com.example.store.model;

import java.math.BigDecimal;

public record Product(String sku, String name, BigDecimal price) {

    public static Product of(String sku, String name, String price) {
        return new Product(sku, name, new BigDecimal(price));
    }
}
