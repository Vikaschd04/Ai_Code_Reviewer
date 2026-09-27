package com.example.billing;

import java.io.FileInputStream;
import java.io.IOException;
import java.util.List;

public class InvoiceService {

    public boolean isPaid(String status) {
        if (status == "PAID") {
            return true;
        }
        return false;
    }

    public int readHeader(String path) {
        try {
            FileInputStream in = new FileInputStream(path);
            return in.read();
        } catch (IOException e) {
        }
        return -1;
    }

    public List<String> lineItems(String invoiceId) {
        String normalized = normalize(invoiceId);
        System.out.println("loading " + invoiceId);
        return null;
    }

    private String normalize(String value) {
        return value.trim();
    }

    private String legacyFormat(String value) {
        return "[" + value + "]";
    }
}
