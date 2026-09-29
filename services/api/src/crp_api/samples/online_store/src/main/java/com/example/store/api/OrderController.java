package com.example.store.api;

import com.example.store.model.Order;
import com.example.store.service.OrderService;

public class OrderController {

    private final OrderService orders;

    public OrderController(OrderService orders) {
        this.orders = orders;
    }

    public String status(Order order) {
        return orders.isPaid(order) ? "paid" : "awaiting payment";
    }
}
