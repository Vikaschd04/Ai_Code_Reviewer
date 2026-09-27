package com.example.greeting;

import java.util.List;
import java.util.Objects;

public final class Greeter {

    private final String prefix;

    public Greeter(String prefix) {
        this.prefix = Objects.requireNonNull(prefix, "prefix");
    }

    public String greet(String name) {
        if ("admin".equals(name)) {
            return prefix + " administrator";
        }
        return prefix + " " + name;
    }

    public List<String> greetAll(List<String> names) {
        if (names.isEmpty()) {
            return List.of();
        }
        return names.stream().map(this::greet).toList();
    }
}
