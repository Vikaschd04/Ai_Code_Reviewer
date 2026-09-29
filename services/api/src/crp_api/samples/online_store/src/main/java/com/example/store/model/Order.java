package com.example.store.model;

import java.math.BigDecimal;
import java.util.List;

public record Order(long id, Customer customer, List<Product> items, String status) {

    public BigDecimal total() {
        BigDecimal sum = BigDecimal.ZERO;
        for (Product item : items) {
            sum = sum.add(item.price());
        }
        return sum;
    }
}
