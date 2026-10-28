package shop;

import java.util.Map;

public class Catalog {
    private final Map<String, String> names;

    public Catalog(Map<String, String> names) {
        this.names = names;
    }

    public String name(String sku) {
        return names.getOrDefault(sku, "unknown");
    }
}
