package com.acme.jobs;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class UnsafeWorkers {
    private final ExecutorService workers = Executors.newCachedThreadPool();
}
