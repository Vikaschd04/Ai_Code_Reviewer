package com.acme.core;

public abstract class Entity implements java.io.Serializable {
    private long id;

    public long getId() {
        return id;
    }
}
