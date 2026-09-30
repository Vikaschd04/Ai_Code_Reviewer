package shop;

public class OrderStatus {
    public boolean isPaid(Order order) {
        String status = order.getStatus();
        return status == "PAID";
    }
}
