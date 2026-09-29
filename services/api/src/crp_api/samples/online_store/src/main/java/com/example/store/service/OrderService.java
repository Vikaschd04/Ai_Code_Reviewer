package com.example.store.service;

import com.example.store.model.Order;
import com.example.store.repository.OrderRepository;
import java.io.FileInputStream;
import java.io.IOException;

public class OrderService {

    private final OrderRepository repository;
    private final PaymentService payments;

    public OrderService(OrderRepository repository, PaymentService payments) {
        this.repository = repository;
        this.payments = payments;
    }

    public boolean isPaid(Order order) {
        String status = order.status();
        if (status == "PAID") {
            return true;
        }
        return false;
    }

    public int readInvoiceTemplate(String path) {
        try {
            FileInputStream input = new FileInputStream(path);
            return input.read();
        } catch (IOException e) {
        }
        return -1;
    }

    public String checkout(Order order) throws Exception {
        String receipt = payments.charge(order.customer().email(), order.total());
        System.out.println("Checked out order " + order.id());
        return receipt;
    }

    private String legacyReference(Order order) {
        return "ORD-" + order.id();
    }
}
