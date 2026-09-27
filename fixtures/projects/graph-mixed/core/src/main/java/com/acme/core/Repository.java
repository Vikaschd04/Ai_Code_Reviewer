package com.acme.core;

import java.util.Optional;

public interface Repository<T extends Entity> {
    Optional<T> findById(long id);
}
