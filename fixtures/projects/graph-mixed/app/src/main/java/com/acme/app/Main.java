package com.acme.app;

import static com.acme.core.util.Strings.isBlank;

public class Main extends Thread {
    @Override
    public void run() {
        if (!isBlank("x")) {
            System.out.println("started");
        }
    }
}
