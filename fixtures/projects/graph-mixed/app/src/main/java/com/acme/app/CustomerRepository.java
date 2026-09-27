package com.acme.app;

import com.acme.core.*;
import com.unknown.Missing;
import java.util.List;
import java.util.Optional;
import org.slf4j.Logger;

public class CustomerRepository implements Repository<Customer> {
    private Logger log;
    private List<Missing> cache;

    @Override
    public Optional<Customer> findById(long id) {
        return Optional.empty();
    }
}
