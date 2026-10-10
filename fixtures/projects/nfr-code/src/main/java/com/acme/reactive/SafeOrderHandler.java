package com.acme.reactive;

import reactor.core.publisher.Mono;

public class SafeOrderHandler {
    private final OrderClient client;

    public SafeOrderHandler(OrderClient client) {
        this.client = client;
    }

    public Mono<Order> order(String id) {
        return client.customer(id).flatMap(customer -> client.order(id).map(o -> o.withCustomer(customer)));
    }

    /** A blocking adapter for a batch job, outside reactive code. */
    public Order orderNow(String id) {
        return order(id).block();
    }
}
