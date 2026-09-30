package shop;

import java.util.Objects;

public class OrderStatus {
    public boolean isPaid(Order order) {
        Objects.requireNonNull(order, "order");
        return "PAID".equals(order.getStatus());
    }
}
