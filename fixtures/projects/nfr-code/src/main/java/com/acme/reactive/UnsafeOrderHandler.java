package com.acme.reactive;

import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

public class UnsafeOrderHandler {
    private final OrderClient client;

    public UnsafeOrderHandler(OrderClient client) {
        this.client = client;
    }

    public Mono<Order> order(String id) {
        Customer customer = client.customer(id).block();
        return client.order(id).map(order -> order.withCustomer(customer));
    }

    public Flux<Order> recent(String id) throws InterruptedException {
        Thread.sleep(100);
        return client.orders(id);
    }
}
