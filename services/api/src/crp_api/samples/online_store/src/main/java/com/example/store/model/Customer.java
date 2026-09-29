package com.example.store.model;

public record Customer(long id, String name, String email) {

    public boolean hasEmail() {
        return email != null && !email.isBlank();
    }
}
